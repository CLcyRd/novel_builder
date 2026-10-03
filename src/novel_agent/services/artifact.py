"""Working Artifact API shared by Freeform and future Workflows."""

from uuid import UUID

from novel_agent.domain.artifact import ArtifactRef, ArtifactRevision, ArtifactType
from novel_agent.domain.models import Project
from novel_agent.domain.skill import SkillRef
from novel_agent.ports.artifact_repository import ArtifactRepository


class ArtifactService:
    def __init__(self, repository: ArtifactRepository) -> None:
        self._repository = repository

    def create(
        self,
        project: Project,
        session_id: UUID,
        artifact_type: ArtifactType,
        scope: str,
        content: str,
        *,
        chapter_id: UUID | None = None,
        section: str | None = None,
        dependency_paths: tuple[str, ...] = (),
        dependencies: tuple[ArtifactRef, ...] = (),
        skill_refs: tuple[SkillRef, ...] = (),
    ) -> ArtifactRevision:
        return self._repository.create(
            project,
            session_id,
            artifact_type,
            scope,
            content,
            chapter_id,
            section,
            dependency_paths,
            dependencies,
            skill_refs,
        )

    def revise(
        self,
        project: Project,
        ref: ArtifactRef,
        content: str,
        *,
        skill_refs: tuple[SkillRef, ...] = (),
        dependencies: tuple[ArtifactRef, ...] | None = None,
    ) -> ArtifactRevision:
        return self._repository.revise(project, ref, content, skill_refs, dependencies)

    def get(self, project: Project, ref: ArtifactRef) -> ArtifactRevision:
        return self._repository.get(project, ref)

    def get_by_id(self, project: Project, artifact_id: UUID, revision: int) -> ArtifactRevision:
        return self._repository.get_by_id(project, artifact_id, revision)

    def approve(self, project: Project, ref: ArtifactRef):
        return self._repository.set_status(project, ref, "APPROVED")

    def discard(self, project: Project, ref: ArtifactRef):
        return self._repository.set_status(project, ref, "DISCARDED")

    def status(self, project: Project, ref: ArtifactRef):
        return self._repository.status(project, ref)

    def list(self, project: Project, session_id: UUID | None = None):
        return self._repository.list(project, session_id)
