"""Persistent single Chapter graph schema and Artifact reference decoding."""

from __future__ import annotations

from typing import TypedDict

from novel_agent.domain.artifact import ArtifactRef
from novel_agent.domain.errors import InvalidChange


class _State(TypedDict, total=False):
    workflow_version: int
    request: dict
    dependency_paths: list[str]
    summary_ref: dict | None
    outline_ref: dict | None
    manuscript_ref: dict | None
    review_ref: dict | None
    pending_gate: dict | None
    answer: dict | None
    plan_round: int
    summary_round: int
    outline_round: int
    upstream_changed: list[str]
    reuse_downstream: bool
    return_stage: str
    manuscript_round: int
    feedback: str
    commit: dict | None
    result: dict | None
    approval_source: str | None
    approvals: list[dict]
    generation_problem: str | None
    generation_retry_node: str
    generation_action: str
    generation_answer: dict | None
    generation_round: int


def _ref(state: _State, name: str) -> ArtifactRef:
    value = state.get(name)
    if value is None:
        raise InvalidChange(f"Workflow is missing {name}")
    return ArtifactRef.model_validate(value)
