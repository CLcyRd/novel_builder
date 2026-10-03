"""Project-local CLI conversation log, separate from model context and Artifacts."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from novel_agent.config.setting import DEFAULT_SETTINGS, Settings
from novel_agent.domain.artifact import ArtifactRef


@dataclass(frozen=True)
class HistoryMessage:
    role: str
    content: str
    artifact_refs: tuple[ArtifactRef, ...] = ()
    truncated: bool = False


class SessionHistoryStore:
    def __init__(self, root: Path, settings: Settings = DEFAULT_SETTINGS) -> None:
        self.path = root / ".novel-agent" / "checkpoints.sqlite"
        self.settings = settings

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS session_history ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, "
            "artifact_refs TEXT NOT NULL, truncated INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS session_history_by_session "
            "ON session_history(session_id, sequence)"
        )
        return connection

    def record_exchange(
        self,
        session_id: UUID,
        user_content: str,
        assistant_content: str,
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> None:
        key = str(session_id)
        limit = self.settings.session_history_max_chars
        with closing(self._connect()) as connection:
            for role, content, refs in (
                ("user", user_content, ()),
                ("assistant", assistant_content, artifact_refs),
            ):
                connection.execute(
                    "INSERT INTO session_history "
                    "(session_id, role, content, artifact_refs, truncated) VALUES (?, ?, ?, ?, ?)",
                    (
                        key,
                        role,
                        content[:limit],
                        json.dumps([ref.model_dump(mode="json") for ref in refs]),
                        int(len(content) > limit),
                    ),
                )
            connection.execute(
                "DELETE FROM session_history WHERE session_id=? AND sequence NOT IN "
                "(SELECT sequence FROM session_history WHERE session_id=? "
                "ORDER BY sequence DESC LIMIT ?)",
                (key, key, self.settings.session_history_max_messages),
            )
            connection.commit()

    def recent(self, session_id: UUID) -> tuple[HistoryMessage, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT role, content, artifact_refs, truncated FROM session_history "
                "WHERE session_id=? ORDER BY sequence DESC LIMIT ?",
                (str(session_id), self.settings.session_history_display_messages),
            ).fetchall()
        return tuple(
            HistoryMessage(
                role=role,
                content=content,
                artifact_refs=tuple(
                    ArtifactRef.model_validate(ref) for ref in json.loads(refs)
                ),
                truncated=bool(truncated),
            )
            for role, content, refs, truncated in reversed(rows)
        )
