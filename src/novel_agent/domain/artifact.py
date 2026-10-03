"""Immutable working-content revisions and their separately mutable status."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from novel_agent.domain.skill import SkillRef


class ArtifactType(StrEnum):
    TASK_BRIEF = "TASK_BRIEF"
    SUMMARY = "SUMMARY"
    OUTLINE = "OUTLINE"
    MANUSCRIPT = "MANUSCRIPT"
    REWRITE = "REWRITE"
    TRANSLATION = "TRANSLATION"
    PATCH = "PATCH"
    ANALYSIS = "ANALYSIS"
    CONTEXT_SUMMARY = "CONTEXT_SUMMARY"
    REVIEW_REPORT = "REVIEW_REPORT"


class ArtifactStatus(StrEnum):
    WORKING = "WORKING"
    CANDIDATE = "CANDIDATE"
    APPROVED = "APPROVED"
    COMMITTED = "COMMITTED"
    SUPERSEDED = "SUPERSEDED"
    DISCARDED = "DISCARDED"


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: UUID
    revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ArtifactRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: UUID
    revision: int = Field(ge=1)
    project_id: UUID
    session_id: UUID
    artifact_type: ArtifactType
    scope: Literal["PROJECT", "CHAPTER", "SECTION", "PARAGRAPH", "TEXT_RANGE"]
    chapter_id: UUID | None = None
    section: Literal["summary", "outline", "manuscript"] | None = None
    content: str
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_hashes: dict[str, str | None]
    dependencies: tuple[ArtifactRef, ...] = ()
    skill_refs: tuple[SkillRef, ...] = ()
    created_at: datetime

    @property
    def ref(self) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=self.artifact_id,
            revision=self.revision,
            content_hash=self.content_hash,
        )


class ArtifactIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: UUID
    project_id: UUID
    session_id: UUID
    latest_revision: int = Field(ge=1)


class ArtifactRevisionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: UUID
    revision: int = Field(ge=1)
    status: ArtifactStatus
    commit_id: UUID | None = None
