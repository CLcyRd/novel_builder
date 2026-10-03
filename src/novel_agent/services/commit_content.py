"""Resolve exact Artifact revisions and validate their Commit targets."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from novel_agent.domain.artifact import (
    ArtifactRef,
    ArtifactRevisionStatus,
    ArtifactStatus,
    ArtifactType,
)
from novel_agent.domain.change import (
    SetProjectText,
    SetSection,
)
from novel_agent.domain.errors import (
    InvalidArtifact,
    InvalidChange,
)
from novel_agent.domain.models import Project
from novel_agent.infrastructure.filesystem.transaction import (
    json_bytes,
)
from novel_agent.ports.artifact_repository import ArtifactRepository, revision_paths

_TYPE_FOR_SECTION = {
    "summary": {ArtifactType.SUMMARY},
    "outline": {ArtifactType.OUTLINE},
    "manuscript": {
        ArtifactType.MANUSCRIPT,
        ArtifactType.REWRITE,
        ArtifactType.TRANSLATION,
    },
}


class CommitContentResolver:
    """Resolve exact Artifact revisions; own Artifact validation and status plans."""

    def __init__(self, artifacts: ArtifactRepository) -> None:
        self._artifacts = artifacts

    def resolve(
        self,
        project: Project,
        change: SetSection | SetProjectText,
        artifact_refs: list[ArtifactRef],
    ) -> str:
        if (change.text is None) == (change.artifact is None):
            raise InvalidChange("Specify exactly one of direct text or Artifact reference")
        if change.text is not None:
            return change.text
        ref = change.artifact
        assert ref is not None
        revision = self._artifacts.get_unlocked(project.root, ref)
        status = self._artifacts.status_unlocked(project.root, ref)
        if revision.project_id != project.project_id:
            raise InvalidArtifact("Artifact belongs to another Project")
        if status.status in {ArtifactStatus.DISCARDED, ArtifactStatus.COMMITTED}:
            raise InvalidArtifact("Artifact revision cannot be committed")
        if isinstance(change, SetSection):
            if (
                revision.chapter_id != change.chapter_id
                or revision.artifact_type not in _TYPE_FOR_SECTION.get(change.section, set())
            ):
                raise InvalidArtifact("Artifact target or type does not match Chapter section")
            if revision.scope == "PROJECT":
                raise InvalidArtifact("Project-scoped Artifact cannot replace a Chapter section")
            if revision.section is not None and revision.section != change.section:
                raise InvalidArtifact("Artifact section does not match Change")
        elif revision.scope != "PROJECT":
            raise InvalidArtifact("Project text requires Project-scoped Artifact")
        elif revision.artifact_type in {
            ArtifactType.ANALYSIS,
            ArtifactType.CONTEXT_SUMMARY,
            ArtifactType.REVIEW_REPORT,
        }:
            raise InvalidArtifact("Auxiliary Artifact is not committable Project content")
        artifact_refs.append(ref)
        return revision.content

    @staticmethod
    def status_writes(
        root: Path, artifact_refs: list[ArtifactRef], commit_id: UUID
    ) -> dict[str, bytes | None]:
        writes = {}
        unique_artifacts = {(ref.artifact_id, ref.revision): ref for ref in artifact_refs}.values()
        for ref in unique_artifacts:
            status_path = revision_paths(root, ref)[1]
            relative = status_path.relative_to(root).as_posix()
            updated = ArtifactRevisionStatus(
                artifact_id=ref.artifact_id,
                revision=ref.revision,
                status=ArtifactStatus.COMMITTED,
                commit_id=commit_id,
            )
            writes[relative] = json_bytes(updated.model_dump(mode="json"))
        return writes
