"""Project facade; formal mutation will be introduced via CommitService."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from novel_agent.domain.chapter import ChapterDocument
from novel_agent.domain.models import Project, ProjectReadSnapshot, TransactionMaintenanceReport
from novel_agent.ports.project_repository import ProjectRepository


class ProjectService:
    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def create_project(self, workspace: Path, name: str, initial_session_name: str) -> Project:
        return self._repository.create(workspace, name, initial_session_name)

    def open_project(self, root: Path) -> Project:
        return self._repository.open(root)

    def load_project(self, root: Path) -> Project:
        return self.open_project(root)

    def list_projects(self, workspace: Path) -> tuple[Project, ...]:
        return self._repository.list_projects(workspace)

    def read_chapter(self, project: Project, chapter_id: UUID) -> ChapterDocument:
        return self._repository.read_chapter(project, chapter_id)

    def read_project_text(self, project: Project) -> str:
        return self._repository.read_project_text(project)

    def read_snapshot(
        self,
        project: Project,
        *,
        reference_directories: tuple[str, ...] = (),
        reference_keywords: tuple[str, ...] = (),
        attachments: tuple[str, ...] = (),
    ) -> ProjectReadSnapshot:
        return self._repository.read_snapshot(
            project,
            reference_directories=reference_directories,
            reference_keywords=reference_keywords,
            attachments=attachments,
        )

    def resolve_attachment(self, project: Project, relative_path: str | Path) -> Path:
        return self._repository.resolve_attachment(project, relative_path)

    def maintain_transactions(self, project: Project) -> TransactionMaintenanceReport:
        return self._repository.maintain_transactions(project)

    def list_chapters(self, project: Project) -> tuple[tuple[UUID, ChapterDocument], ...]:
        snapshot = self.read_snapshot(project)
        return tuple(
            (chapter_id, snapshot.chapter(chapter_id))
            for chapter_id in snapshot.project.metadata.chapter_order
        )

    def read_section(self, project: Project, chapter_id: UUID, section: str) -> str:
        if section not in {"summary", "outline", "manuscript"}:
            raise ValueError("Unknown chapter section")
        return getattr(self.read_chapter(project, chapter_id), section)

    def count_characters(self, project: Project, chapter_id: UUID) -> int:
        """Count body characters including spaces, excluding line separators."""
        manuscript = self.read_section(project, chapter_id, "manuscript")
        return sum(char not in "\r\n" for char in manuscript)

    def search(self, project: Project, needle: str) -> tuple["SearchHit", ...]:
        if not needle:
            raise ValueError("Search text cannot be empty")
        hits: list[SearchHit] = []
        snapshot = self.read_snapshot(project, reference_directories=("references", "sources"))
        current = snapshot.project
        for chapter_id in current.metadata.chapter_order:
            entry = current.metadata.chapters[str(chapter_id)]
            chapter = snapshot.chapter(chapter_id)
            for section in ("manuscript", "outline", "summary"):
                for line_number, line in enumerate(getattr(chapter, section).splitlines(), 1):
                    if needle in line:
                        hits.append(
                            SearchHit(f"chapters/{entry.filename}", section, line_number, line)
                        )
        for line_number, line in enumerate(snapshot.project_text.splitlines(), 1):
            if needle in line:
                hits.append(SearchHit("PROJECT.md", "project", line_number, line))
        for relative, text in snapshot.text_files.items():
            for line_number, line in enumerate(text.splitlines(), 1):
                if needle in line:
                    hits.append(SearchHit(relative, "reference", line_number, line))
        return tuple(hits)


@dataclass(frozen=True)
class SearchHit:
    path: str
    section: str
    line: int
    text: str
