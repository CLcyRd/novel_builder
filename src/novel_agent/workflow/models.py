"""Small, serializable Workflow control protocol. Content lives in Artifacts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from novel_agent.domain.artifact import ArtifactRef
from novel_agent.domain.skill import SkillRef
from novel_agent.services.commit import CommitResult


class ChapterTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chapter_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    brief: str = ""
    brief_ref: ArtifactRef | None = None


class WorkflowSkillBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: SkillRef
    chapter_indexes: tuple[int, ...] = ()  # Empty means every Chapter in this Request.
    reason: str = ""


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID = Field(default_factory=uuid4)
    task_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    session_id: UUID
    depends_on: tuple[UUID, ...] = ()
    kind: Literal[
        "CREATE_CHAPTER",
        "CONTINUE_CHAPTER",
        "BACKFILL_CHAPTER",
        "REWRITE_CHAPTER",
        "MULTI_CHAPTER",
        "IMPORT_NOVEL",
    ] = "CREATE_CHAPTER"
    chapter_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    position: int | None = Field(default=None, ge=0)
    chapters: tuple[ChapterTarget, ...] = ()
    planned_total: int | None = Field(default=None, ge=1)
    source_path: str | None = None
    target_characters: int | None = Field(default=None, ge=1)
    constraints: tuple[str, ...] = ()
    attachments: tuple[str, ...] = ()
    working_artifact_refs: tuple[ArtifactRef, ...] = ()
    skill_refs: tuple[SkillRef, ...] = ()
    skill_candidate_refs: tuple[SkillRef, ...] = ()
    skill_bindings: tuple[WorkflowSkillBinding, ...] = ()
    auto_approval: bool | None = None


class WorkflowGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_id: UUID
    kind: Literal["APPROVAL", "DECISION"]
    stage: Literal["PLAN", "SUMMARY", "OUTLINE", "MANUSCRIPT", "COMMIT", "SUMMARIES", "OUTLINES"]
    artifact_refs: tuple[ArtifactRef, ...] = ()
    reason: str | None = None
    paths: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    generation_blocked: bool = False


class GateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_id: UUID
    action: Literal["APPROVE", "REVISE", "REJECT", "EXIT", "REGENERATE", "KEEP"]
    feedback: str | None = None


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_id: UUID
    stage: str
    source: Literal["HUMAN", "AUTO"]
    artifact_refs: tuple[ArtifactRef, ...]


class WorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID
    task_id: UUID
    status: Literal["COMPLETED", "CANCELLED"]
    chapter_id: UUID
    chapter_ids: tuple[UUID, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()
    commit: CommitResult | None = None
    completed_tasks: tuple[str, ...] = ()
    project_changes: tuple[str, ...] = ()
    important_facts_created: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    remaining_tasks: tuple[str, ...] = ()
    approvals: tuple[ApprovalRecord, ...] = ()


class WorkflowSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID
    kind: str | None = None
    status: Literal["RUNNING", "WAITING_APPROVAL", "WAITING_DECISION", "COMPLETED", "CANCELLED"]
    gate: WorkflowGate | None = None
    result: WorkflowResult | None = None
    artifact_refs: tuple[ArtifactRef, ...] = ()
    approvals: tuple[ApprovalRecord, ...] = ()
