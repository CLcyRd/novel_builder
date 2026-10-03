"""Storage contract for working Artifact revisions."""

from pathlib import Path
from typing import Protocol
from uuid import UUID

from novel_agent.domain.artifact import (
    ArtifactIndex,
    ArtifactRef,
    ArtifactRevision,
    ArtifactRevisionStatus,
    ArtifactType,
)
from novel_agent.domain.models import Project
from novel_agent.domain.skill import SkillRef


class ArtifactRepository(Protocol):
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
    ) -> ArtifactRevision: ...

    def revise(
        self,
        project: Project,
        ref: ArtifactRef,
        content: str,
        skill_refs: tuple[SkillRef, ...],
        dependencies: tuple[ArtifactRef, ...] | None = None,
    ) -> ArtifactRevision: ...

    def get(self, project: Project, ref: ArtifactRef) -> ArtifactRevision: ...

    def get_by_id(self, project: Project, artifact_id: UUID, revision: int) -> ArtifactRevision: ...

    def get_unlocked(self, root: Path, ref: ArtifactRef) -> ArtifactRevision: ...

    def status(self, project: Project, ref: ArtifactRef) -> ArtifactRevisionStatus: ...

    def status_unlocked(self, root: Path, ref: ArtifactRef) -> ArtifactRevisionStatus: ...

    def set_status(
        self, project: Project, ref: ArtifactRef, status: str
    ) -> ArtifactRevisionStatus: ...

    def list(self, project: Project, session_id: UUID | None) -> tuple[ArtifactIndex, ...]: ...


def revision_paths(root: Path, ref: ArtifactRef) -> tuple[Path, Path]:
    base = root / ".novel-agent/drafts" / str(ref.artifact_id)
    number = f"{ref.revision:04d}"
    return base / "revisions" / f"{number}.json", base / "statuses" / f"{number}.json"
