"""Filesystem implementation for creating and opening local Projects."""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from novel_agent.domain.chapter import ChapterDocument, parse_chapter
from novel_agent.domain.errors import (
    InvalidChapter,
    InvalidProject,
    InvalidProjectName,
    ProjectAlreadyExists,
    ProjectNotFound,
    UnsafeProjectPath,
)
from novel_agent.domain.models import (
    Project,
    ProjectMetadata,
    ProjectReadSnapshot,
    SessionMetadata,
    TransactionMaintenanceReport,
)
from novel_agent.infrastructure.filesystem.paths import resolve_attachment, resolve_project_file
from novel_agent.infrastructure.filesystem.transaction import (
    maintain_transactions_unlocked,
    project_lock,
    recover_pending_unlocked,
)

_REQUIRED_DIRECTORIES = (
    "chapters",
    "sources",
    "references",
    ".novel-agent",
    ".novel-agent/sessions",
    ".novel-agent/drafts",
    ".novel-agent/cache",
    ".novel-agent/tmp",
)
_REQUIRED_FILES = ("PROJECT.md", "MANUSCRIPT.md", ".novel-agent/project.json")
_RESERVED_WINDOWS_NAMES = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def _validate_name(name: str, label: str) -> None:
    if (
        not name
        or name != name.strip()
        or name in {".", ".."}
        or name.endswith((".", " "))
        or any(char in name for char in '<>:"/\\|?*\r\n\0')
        or any(ord(char) < 32 for char in name)
        or _RESERVED_WINDOWS_NAMES.match(name)
    ):
        raise InvalidProjectName(f"Invalid {label} name")


def _write_json(path: Path, model: ProjectMetadata | SessionMetadata) -> None:
    path.write_text(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_json(path: Path, model_type: type[ProjectMetadata] | type[SessionMetadata]):
    try:
        return model_type.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError, UnicodeError) as exc:
        raise InvalidProject(f"Invalid metadata: {path}") from exc


def _read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise InvalidProject(f"Cannot read UTF-8 Project file: {path}") from exc


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


class FileProjectRepository:
    def create(self, workspace: Path, name: str, initial_session_name: str) -> Project:
        _validate_name(name, "Project")
        _validate_name(initial_session_name, "Session")
        workspace.mkdir(parents=True, exist_ok=True)
        workspace = workspace.resolve(strict=True)
        destination = workspace / name
        if destination.exists():
            raise ProjectAlreadyExists(f"Project already exists: {destination}")

        project_id, session_id = uuid4(), uuid4()
        created_at = datetime.now(UTC)
        metadata = ProjectMetadata(
            project_id=project_id,
            name=name,
            created_at=created_at,
            initial_session_id=session_id,
        )
        session = SessionMetadata(
            session_id=session_id,
            project_id=project_id,
            name=initial_session_name,
            created_at=created_at,
        )

        with tempfile.TemporaryDirectory(prefix=".novel-agent-create-", dir=workspace) as tmp:
            staged_root = Path(tmp)
            for relative in _REQUIRED_DIRECTORIES:
                (staged_root / relative).mkdir(parents=True, exist_ok=True)
            (staged_root / "PROJECT.md").write_text(f"# {name}\n", encoding="utf-8", newline="\n")
            (staged_root / "MANUSCRIPT.md").write_text("", encoding="utf-8", newline="\n")
            _write_json(staged_root / ".novel-agent/project.json", metadata)
            _write_json(staged_root / f".novel-agent/sessions/{session_id}.json", session)
            try:
                staged_root.rename(destination)
            except FileExistsError as exc:
                raise ProjectAlreadyExists(f"Project already exists: {destination}") from exc
        return self.open(destination)

    def open(self, root: Path) -> Project:
        try:
            canonical = root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProjectNotFound(f"Project not found: {root}") from exc
        if not canonical.is_dir():
            raise ProjectNotFound(f"Project is not a directory: {root}")
        if not (canonical / ".novel-agent").is_dir():
            raise InvalidProject("Project internal directory is missing")
        with project_lock(canonical):
            recover_pending_unlocked(canonical)
            return self.open_unlocked(canonical)

    def open_unlocked(self, root: Path) -> Project:
        return self.read_snapshot_unlocked(root).project

    def read_snapshot_unlocked(self, root: Path) -> ProjectReadSnapshot:
        """Caller holds the Project lock and has recovered pending transactions."""
        try:
            root = root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProjectNotFound(f"Project not found: {root}") from exc
        if not root.is_dir():
            raise ProjectNotFound(f"Project is not a directory: {root}")
        for relative in _REQUIRED_DIRECTORIES:
            path = root / relative
            if _is_link(path) or not path.is_dir():
                raise InvalidProject(f"Missing or unsafe Project directory: {relative}")
        for relative in _REQUIRED_FILES:
            path = root / relative
            if _is_link(path) or not path.is_file():
                raise InvalidProject(f"Missing or unsafe Project file: {relative}")

        metadata = _read_json(root / ".novel-agent/project.json", ProjectMetadata)
        if root.name != metadata.name:
            raise InvalidProject("Project directory name differs from metadata")
        _validate_name(metadata.name, "Project")
        project_text = _read_text(root / "PROJECT.md")
        _read_text(root / "MANUSCRIPT.md")

        sessions_root = root / ".novel-agent/sessions"
        sessions: list[SessionMetadata] = []
        for session_path in sorted(sessions_root.iterdir()):
            if (
                _is_link(session_path)
                or not session_path.is_file()
                or session_path.suffix != ".json"
            ):
                raise InvalidProject(f"Unexpected Session entry: {session_path.name}")
            session = _read_json(session_path, SessionMetadata)
            if session.project_id != metadata.project_id or session_path.stem != str(
                session.session_id
            ):
                raise InvalidProject(f"Session identity mismatch: {session_path.name}")
            _validate_name(session.name, "Session")
            sessions.append(session)
        if not sessions or metadata.initial_session_id not in {s.session_id for s in sessions}:
            raise InvalidProject("Initial Session is missing")

        registered = {entry.filename for entry in metadata.chapters.values()}
        documents = {}
        for chapter_id in metadata.chapter_order:
            filename = metadata.chapters[str(chapter_id)].filename
            relative = Path("chapters") / filename
            try:
                path = resolve_project_file(root, relative)
            except (FileNotFoundError, UnsafeProjectPath) as exc:
                raise InvalidProject(f"Missing or unsafe registered chapter: {filename}") from exc
            try:
                documents[chapter_id] = parse_chapter(_read_text(path))
            except InvalidChapter as exc:
                raise InvalidProject(f"Invalid registered chapter: {filename}") from exc
        for child in (root / "chapters").iterdir():
            if _is_link(child) or not child.is_file() or child.name not in registered:
                raise InvalidProject(f"Unregistered or unsafe chapter entry: {child.name}")
        return ProjectReadSnapshot(
            project=Project(root=root, metadata=metadata, sessions=tuple(sessions)),
            project_text=project_text,
            chapters=documents,
            text_files={},
        )

    def read_snapshot(
        self,
        project: Project,
        *,
        reference_directories: tuple[str, ...] = (),
        reference_keywords: tuple[str, ...] = (),
        attachments: tuple[str, ...] = (),
    ) -> ProjectReadSnapshot:
        if any(directory not in {"references", "sources"} for directory in reference_directories):
            raise UnsafeProjectPath("Batch references must be under references or sources")
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            snapshot = self.read_snapshot_unlocked(project.root)
            current = snapshot.project
            if current.project_id != project.project_id:
                raise InvalidProject("Project identity changed since it was opened")
            files: dict[str, str] = {}
            keywords = tuple(word.casefold() for word in reference_keywords)
            for directory in reference_directories:
                for path in sorted((current.root / directory).rglob("*")):
                    if (
                        path.is_file()
                        and path.suffix.casefold() in {".txt", ".md"}
                        and (not keywords or any(word in path.stem.casefold() for word in keywords))
                    ):
                        relative = path.relative_to(current.root).as_posix()
                        safe = resolve_attachment(current.root, relative)
                        if relative not in files:
                            files[relative] = safe.read_text(encoding="utf-8")
            for relative in attachments:
                safe = resolve_attachment(current.root, relative)
                if relative not in files:
                    files[relative] = safe.read_text(encoding="utf-8")
            return ProjectReadSnapshot(current, snapshot.project_text, snapshot.chapters, files)

    def list_projects(self, workspace: Path) -> tuple[Project, ...]:
        if not workspace.exists():
            return ()
        if not workspace.is_dir():
            raise ProjectNotFound(f"Project workspace is not a directory: {workspace}")
        projects = []
        for child in sorted(workspace.iterdir()):
            if child.is_dir() and (child / ".novel-agent/project.json").exists():
                projects.append(self.open(child))
        return tuple(projects)

    def read_chapter(self, project: Project, chapter_id: UUID) -> ChapterDocument:
        return self.read_snapshot(project).chapter(chapter_id)

    def read_project_text(self, project: Project) -> str:
        return self.read_snapshot(project).project_text

    def resolve_attachment(self, project: Project, relative_path: str | Path) -> Path:
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            current = self.open_unlocked(project.root)
            if current.project_id != project.project_id:
                raise InvalidProject("Project identity changed since it was opened")
            return resolve_attachment(current.root, relative_path)

    def maintain_transactions(self, project: Project) -> TransactionMaintenanceReport:
        with project_lock(project.root):
            metadata = _read_json(project.root / ".novel-agent/project.json", ProjectMetadata)
            if metadata.project_id != project.project_id:
                raise InvalidProject("Project identity changed since it was opened")
            report = maintain_transactions_unlocked(project.root)
            self.open_unlocked(project.root)
            return report
