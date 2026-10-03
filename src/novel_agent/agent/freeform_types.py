"""Freeform request-local progress and public result types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from novel_agent.domain.artifact import ArtifactRef
from novel_agent.domain.models import Project, SessionMetadata
from novel_agent.domain.skill import SkillRef
from novel_agent.infrastructure.models.gateway import ModelDefinition
from novel_agent.runtime.events import RuntimeEvent
from novel_agent.runtime.state import SessionState, SessionStateStore
from novel_agent.services.commit import CommitResult
from novel_agent.services.skill import SkillDefinition

if TYPE_CHECKING:
    from novel_agent.agent.freeform import FreeformAgent


@dataclass(frozen=True)
class TaskContext:
    prompt: str
    attachment_paths: tuple[str, ...] = ()
    model: str = ""


@dataclass
class RunResult:
    response: str
    model: str
    events: list[RuntimeEvent] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    skill_refs: list[SkillRef] = field(default_factory=list)
    display_refs: list[ArtifactRef] = field(default_factory=list)
    commits: list[CommitResult] = field(default_factory=list)
    content_chunks: int = 0
    attachment_paths: tuple[str, ...] = ()
    workflow_snapshot: object | None = None
    draft_paths: list[str] = field(default_factory=list)
    file_review: bool = False


@dataclass
class FreeformRunScope:
    """One request's mutable progress; never a persisted control plane."""

    agent: FreeformAgent
    project: Project
    session: SessionMetadata
    store: SessionStateStore
    state: SessionState
    definition: ModelDefinition
    result: RunResult
    on_event: Callable[[RuntimeEvent], None] | None = None
    on_content: Callable[[str], None] | None = None
    task: TaskContext | None = None
    available_skills: tuple[SkillDefinition, ...] = ()
    selected_skill: str | None = None
    selected_definition: SkillDefinition | None = None
    ignore_skills: bool = False
    skill_files: dict = field(default_factory=dict)
    used_skills: set[str] = field(default_factory=set)
    delete_target: UUID | None = None
    delete_baselines: dict[str, str | None] = field(default_factory=dict)
    delete_blocker: str | None = None
    delete_report: str | None = None

    @property
    def model_alias(self) -> str:
        return self.session.current_model or self.agent.models.default_model

    def used_refs(self) -> tuple[SkillRef, ...]:
        return tuple(
            skill.ref for skill in self.available_skills if skill.ref.name in self.used_skills
        )

    def emit(self, kind: str, detail: str = "") -> None:
        event = RuntimeEvent(kind, detail)
        self.result.events.append(event)
        if self.on_event:
            self.on_event(event)

    def trace(self, name: str, detail: str = "") -> None:
        self.result.tool_calls.append(name)
        self.emit("Calling Tool", f"{name} {detail}".strip())

    def offer_display(self, ref: ArtifactRef) -> None:
        self.result.display_refs = [
            old for old in self.result.display_refs if old.artifact_id != ref.artifact_id
        ]
        self.result.display_refs.append(ref)
