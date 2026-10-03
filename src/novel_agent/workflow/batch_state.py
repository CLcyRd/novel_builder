"""Persistent batch graph schema and row Artifact reference decoding."""

from __future__ import annotations

from typing import TypedDict

from novel_agent.domain.artifact import ArtifactRef
from novel_agent.domain.errors import InvalidChange


class _BatchState(TypedDict, total=False):
    request: dict
    rows: list[dict]
    paths: list[str]
    index: int
    round: int
    feedback: str
    pending_gate: dict | None
    answer: dict | None
    commit: dict | None
    result: dict | None
    approval_source: str | None
    approvals: list[dict]
    generation_problem: str | None
    generation_retry_node: str
    generation_action: str
    generation_answer: dict | None
    generation_resume_index: int | None
    generation_round: int


def _ref(row: dict, section: str) -> ArtifactRef:
    value = row.get(section)
    if value is None:
        raise InvalidChange(f"Workflow Chapter is missing {section} Artifact")
    return ArtifactRef.model_validate(value)
