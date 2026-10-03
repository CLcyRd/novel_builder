"""Storage contract for the Phase 1 Project service."""

from pathlib import Path
from typing import Protocol
from uuid import UUID

from novel_agent.domain.chapter import ChapterDocument
from novel_agent.domain.models import Project, ProjectReadSnapshot, TransactionMaintenanceReport


class ProjectRepository(Protocol):
    def create(self, workspace: Path, name: str, initial_session_name: str) -> Project: ...

    def open(self, root: Path) -> Project: ...

    def open_unlocked(self, root: Path) -> Project: ...

    def read_snapshot_unlocked(self, root: Path) -> ProjectReadSnapshot: ...

    def read_snapshot(
        self,
        project: Project,
        *,
        reference_directories: tuple[str, ...] = (),
        reference_keywords: tuple[str, ...] = (),
        attachments: tuple[str, ...] = (),
    ) -> ProjectReadSnapshot: ...

    def list_projects(self, workspace: Path) -> tuple[Project, ...]: ...

    def read_chapter(self, project: Project, chapter_id: UUID) -> ChapterDocument: ...

    def read_project_text(self, project: Project) -> str: ...

    def resolve_attachment(self, project: Project, relative_path: str | Path) -> Path: ...

    def maintain_transactions(self, project: Project) -> TransactionMaintenanceReport: ...
