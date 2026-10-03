"""Small Project-local Session checkpoint; manuscript bodies remain in Artifacts."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from novel_agent.config.setting import DEFAULT_SETTINGS
from novel_agent.domain.artifact import ArtifactRef


@dataclass
class SessionState:
    history: list[dict[str, str]] = field(default_factory=list)
    visible_refs: list[ArtifactRef] = field(default_factory=list)
    current_ref: ArtifactRef | None = None
    draft_titles: dict[str, str] = field(default_factory=dict)
    active_workflow_id: UUID | None = None
    active_workflow_kind: str | None = None
    completed_task_ids: list[UUID] = field(default_factory=list)
    last_workflow_result: dict | None = None
    pending_routing_decision: dict | None = None
    pending_draft: dict | None = None

    def remember(self, role: str, content: str) -> None:
        # Keep conversational cues, not chapter bodies or complete tool transcripts.
        self.history.append({
            "role": role,
            "content": content[:DEFAULT_SETTINGS.context_history_max_chars],
        })
        self.history = self.history[-DEFAULT_SETTINGS.context_history_max_messages:]

    def display(self, ref: ArtifactRef) -> None:
        if ref not in self.visible_refs:
            self.visible_refs.append(ref)
        self.current_ref = ref


class SessionStateStore:
    def __init__(self, root: Path) -> None:
        self.path = root / ".novel-agent" / "checkpoints.sqlite"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS session_state "
            "(session_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        return connection

    def load(self, session_id: UUID) -> SessionState:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM session_state WHERE session_id=?", (str(session_id),)
            ).fetchone()
        if row is None:
            return SessionState()
        data = json.loads(row[0])
        return SessionState(
            history=data.get("history", []),
            visible_refs=[ArtifactRef.model_validate(ref) for ref in data.get("visible_refs", [])],
            current_ref=ArtifactRef.model_validate(data["current_ref"])
            if data.get("current_ref")
            else None,
            draft_titles=data.get("draft_titles", {}),
            active_workflow_id=UUID(data["active_workflow_id"])
            if data.get("active_workflow_id")
            else None,
            active_workflow_kind=data.get("active_workflow_kind"),
            completed_task_ids=[UUID(item) for item in data.get("completed_task_ids", [])],
            last_workflow_result=data.get("last_workflow_result"),
            pending_routing_decision=data.get("pending_routing_decision"),
            pending_draft=data.get("pending_draft"),
        )

    def save(self, session_id: UUID, state: SessionState) -> None:
        payload = json.dumps(
            {
                "history": state.history,
                "visible_refs": [ref.model_dump(mode="json") for ref in state.visible_refs],
                "current_ref": state.current_ref.model_dump(mode="json")
                if state.current_ref
                else None,
                "draft_titles": state.draft_titles,
                "active_workflow_id": str(state.active_workflow_id)
                if state.active_workflow_id
                else None,
                "active_workflow_kind": state.active_workflow_kind,
                "completed_task_ids": [str(item) for item in state.completed_task_ids],
                "last_workflow_result": state.last_workflow_result,
                "pending_routing_decision": state.pending_routing_decision,
                "pending_draft": state.pending_draft,
            },
            ensure_ascii=False,
        )
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO session_state VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload",
                (str(session_id), payload),
            )
            connection.commit()
