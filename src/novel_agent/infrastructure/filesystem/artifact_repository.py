"""Project-local storage for exact, immutable Artifact revision content."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from novel_agent.domain.artifact import (
    ArtifactIndex,
    ArtifactRef,
    ArtifactRevision,
    ArtifactRevisionStatus,
    ArtifactStatus,
    ArtifactType,
)
from novel_agent.domain.errors import ArtifactNotFound, InvalidArtifact, InvalidProject
from novel_agent.domain.models import Project
from novel_agent.domain.skill import SkillRef
from novel_agent.infrastructure.filesystem.paths import resolve_project_file
from novel_agent.infrastructure.filesystem.transaction import (
    atomic_write,
    commit_files_unlocked,
    digest,
    json_bytes,
    project_lock,
    read_bytes_or_none,
    recover_pending_unlocked,
)
from novel_agent.ports.artifact_repository import revision_paths


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _load(path: Path, model_type):
    internal = next((parent for parent in path.parents if parent.name == ".novel-agent"), None)
    if internal is not None:
        root = internal.parent.resolve(strict=True)
        if not path.resolve(strict=False).is_relative_to(root):
            raise InvalidArtifact("Artifact path escapes Project")
        cursor = path
        while cursor != root:
            if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
                raise InvalidArtifact("Linked Artifact path is not allowed")
            cursor = cursor.parent
    try:
        return model_type.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as exc:
        raise ArtifactNotFound(f"Missing or invalid Artifact data: {path}") from exc


def _save(path: Path, model) -> None:
    atomic_write(path, json_bytes(model.model_dump(mode="json")))


def _check_scope(scope: str, chapter_id: UUID | None, section: str | None) -> None:
    if scope not in {"PROJECT", "CHAPTER", "SECTION", "PARAGRAPH", "TEXT_RANGE"}:
        raise InvalidArtifact("Unknown Artifact scope")
    if scope != "PROJECT" and chapter_id is None:
        raise InvalidArtifact("Chapter-scoped Artifact requires chapter_id")
    if scope == "SECTION" and section not in {"summary", "outline", "manuscript"}:
        raise InvalidArtifact("Section Artifact requires a valid section")


class FileArtifactRepository:
    def __init__(self, failure_hook: Callable[[int, str], None] | None = None) -> None:
        self._failure_hook = failure_hook

    def _save_revision_transaction(self, root: Path, models: dict[Path, object]) -> None:
        writes = {
            path.relative_to(root).as_posix(): json_bytes(model.model_dump(mode="json"))
            for path, model in models.items()
        }
        baselines = {relative: digest(read_bytes_or_none(root / relative)) for relative in writes}
        fingerprint = hashlib.sha256(b"".join(writes[key] for key in sorted(writes))).hexdigest()
        commit_files_unlocked(
            root,
            uuid4(),
            fingerprint,
            writes,
            baselines,
            {"kind": "ARTIFACT_REVISION"},
            self._failure_hook,
            kind="ARTIFACT_REVISION",
        )

    def create(
        self,
        project: Project,
        session_id: UUID,
        artifact_type: ArtifactType,
        scope: str,
        content: str,
        chapter_id: UUID | None,
        section: str | None,
        dependency_paths: tuple[str, ...],
        dependencies: tuple[ArtifactRef, ...],
        skill_refs: tuple[SkillRef, ...],
    ) -> ArtifactRevision:
        _check_scope(scope, chapter_id, section)
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            current = self._read_project_metadata(project.root)
            if current.project_id != project.project_id:
                raise InvalidProject("Project identity changed")
            if session_id not in self._session_ids(project.root):
                raise InvalidArtifact("Session does not belong to Project")
            baselines: dict[str, str | None] = {}
            if chapter_id is not None:
                entry = current.chapters.get(str(chapter_id))
                if entry is None:
                    baselines[".novel-agent/project.json"] = digest(
                        (project.root / ".novel-agent/project.json").read_bytes()
                    )
                else:
                    relative = f"chapters/{entry.filename}"
                    baselines[relative] = digest((project.root / relative).read_bytes())
            for relative in dependency_paths:
                file = resolve_project_file(project.root, relative)
                baselines[file.relative_to(project.root).as_posix()] = digest(file.read_bytes())
            if scope == "PROJECT":
                baselines["PROJECT.md"] = digest((project.root / "PROJECT.md").read_bytes())
            for dependency in dependencies:
                self.get_unlocked(project.root, dependency)
            artifact_id = uuid4()
            revision = ArtifactRevision(
                artifact_id=artifact_id,
                revision=1,
                project_id=current.project_id,
                session_id=session_id,
                artifact_type=artifact_type,
                scope=scope,
                chapter_id=chapter_id,
                section=section,
                content=content,
                content_hash=content_hash(content),
                base_hashes=baselines,
                dependencies=dependencies,
                skill_refs=skill_refs,
                created_at=datetime.now(UTC),
            )
            ref = revision.ref
            revision_path, status_path = revision_paths(project.root, ref)
            index_path = project.root / ".novel-agent/drafts" / str(artifact_id) / "index.json"
            self._save_revision_transaction(
                project.root,
                {
                    revision_path: revision,
                    status_path: ArtifactRevisionStatus(
                        artifact_id=artifact_id, revision=1, status=ArtifactStatus.CANDIDATE
                    ),
                    index_path: ArtifactIndex(
                        artifact_id=artifact_id,
                        project_id=current.project_id,
                        session_id=session_id,
                        latest_revision=1,
                    ),
                },
            )
            return revision

    def revise(
        self,
        project: Project,
        ref: ArtifactRef,
        content: str,
        skill_refs: tuple[SkillRef, ...],
        dependencies: tuple[ArtifactRef, ...] | None = None,
    ) -> ArtifactRevision:
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            previous = self.get_unlocked(project.root, ref)
            index_path = project.root / ".novel-agent/drafts" / str(ref.artifact_id) / "index.json"
            index = _load(index_path, ArtifactIndex)
            if index.project_id != project.project_id or previous.project_id != project.project_id:
                raise InvalidArtifact("Artifact belongs to another Project")
            if index.latest_revision != ref.revision:
                raise InvalidArtifact("Only the latest revision can be revised")
            status = self.status_unlocked(project.root, ref)
            if status.status in {ArtifactStatus.COMMITTED, ArtifactStatus.DISCARDED}:
                raise InvalidArtifact("Committed or discarded Artifact cannot be revised")
            if dependencies is not None:
                for dependency in dependencies:
                    self.get_unlocked(project.root, dependency)
            next_revision = previous.model_copy(
                update={
                    "revision": ref.revision + 1,
                    "content": content,
                    "content_hash": content_hash(content),
                    "skill_refs": tuple(dict.fromkeys((*previous.skill_refs, *skill_refs))),
                    "created_at": datetime.now(UTC),
                    "dependencies": previous.dependencies if dependencies is None else dependencies,
                }
            )
            revision_path, status_path = revision_paths(project.root, next_revision.ref)
            self._save_revision_transaction(
                project.root,
                {
                    revision_path: next_revision,
                    status_path: ArtifactRevisionStatus(
                        artifact_id=ref.artifact_id,
                        revision=next_revision.revision,
                        status=ArtifactStatus.CANDIDATE,
                    ),
                    revision_paths(project.root, ref)[1]: status.model_copy(
                        update={"status": ArtifactStatus.SUPERSEDED}
                    ),
                    index_path: index.model_copy(
                        update={"latest_revision": next_revision.revision}
                    ),
                },
            )
            return next_revision

    def get(self, project: Project, ref: ArtifactRef) -> ArtifactRevision:
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            revision = self.get_unlocked(project.root, ref)
            if revision.project_id != project.project_id:
                raise InvalidArtifact("Artifact belongs to another Project")
            return revision

    def get_by_id(self, project: Project, artifact_id: UUID, revision: int) -> ArtifactRevision:
        if revision < 1:
            raise InvalidArtifact("Artifact revision must be positive")
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            ref = ArtifactRef(artifact_id=artifact_id, revision=revision, content_hash="0" * 64)
            path = revision_paths(project.root, ref)[0]
            artifact = _load(path, ArtifactRevision)
            if (
                artifact.project_id != project.project_id
                or artifact.artifact_id != artifact_id
                or artifact.revision != revision
            ):
                raise InvalidArtifact("Artifact identity mismatch")
            return self.get_unlocked(project.root, artifact.ref)

    def status(self, project: Project, ref: ArtifactRef) -> ArtifactRevisionStatus:
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            revision = self.get_unlocked(project.root, ref)
            if revision.project_id != project.project_id:
                raise InvalidArtifact("Artifact belongs to another Project")
            return self.status_unlocked(project.root, ref)

    def set_status(self, project: Project, ref: ArtifactRef, status: str) -> ArtifactRevisionStatus:
        if status not in {ArtifactStatus.APPROVED, ArtifactStatus.DISCARDED}:
            raise InvalidArtifact("Only Approval or Discard is allowed outside Commit")
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            revision = self.get_unlocked(project.root, ref)
            if revision.project_id != project.project_id:
                raise InvalidArtifact("Artifact belongs to another Project")
            previous = self.status_unlocked(project.root, ref)
            if previous.status == ArtifactStatus.COMMITTED:
                raise InvalidArtifact("Committed Artifact status cannot change")
            updated = previous.model_copy(update={"status": ArtifactStatus(status)})
            _save(revision_paths(project.root, ref)[1], updated)
            return updated

    def list(self, project: Project, session_id: UUID | None) -> tuple[ArtifactIndex, ...]:
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            root = project.root / ".novel-agent/drafts"
            found = []
            for directory in sorted(root.iterdir()):
                if not directory.is_dir():
                    raise InvalidArtifact("Unexpected Artifact entry")
                index = _load(directory / "index.json", ArtifactIndex)
                if index.project_id != project.project_id:
                    raise InvalidArtifact("Artifact belongs to another Project")
                if session_id is None or index.session_id == session_id:
                    found.append(index)
            return tuple(found)

    @staticmethod
    def _read_project_metadata(root: Path):
        from novel_agent.domain.models import ProjectMetadata

        return _load(root / ".novel-agent/project.json", ProjectMetadata)

    @staticmethod
    def _session_ids(root: Path) -> set[UUID]:
        return {UUID(path.stem) for path in (root / ".novel-agent/sessions").glob("*.json")}

    @staticmethod
    def get_unlocked(root: Path, ref: ArtifactRef) -> ArtifactRevision:
        revision = _load(revision_paths(root, ref)[0], ArtifactRevision)
        if revision.artifact_id != ref.artifact_id or revision.revision != ref.revision:
            raise InvalidArtifact("Artifact reference mismatch")
        if (
            revision.content_hash != ref.content_hash
            or content_hash(revision.content) != ref.content_hash
        ):
            raise InvalidArtifact("Artifact content hash mismatch")
        return revision

    @staticmethod
    def status_unlocked(root: Path, ref: ArtifactRef) -> ArtifactRevisionStatus:
        status = _load(revision_paths(root, ref)[1], ArtifactRevisionStatus)
        if status.artifact_id != ref.artifact_id or status.revision != ref.revision:
            raise InvalidArtifact("Artifact status reference mismatch")
        return status
