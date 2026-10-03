"""Project-scoped Session metadata; conversational state arrives in Phase 2."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from novel_agent.domain.errors import InvalidChange, InvalidProject
from novel_agent.domain.models import Project, SessionMetadata
from novel_agent.infrastructure.filesystem.project_repository import _validate_name
from novel_agent.infrastructure.filesystem.transaction import (
    atomic_write,
    json_bytes,
    project_lock,
    recover_pending_unlocked,
)
from novel_agent.ports.project_repository import ProjectRepository

_UNSET = object()


class SessionService:
    def __init__(self, projects: ProjectRepository) -> None:
        self._projects = projects

    def create(self, project: Project, name: str) -> SessionMetadata:
        _validate_name(name, "Session")
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            current = self._projects.open_unlocked(project.root)
            if current.project_id != project.project_id:
                raise InvalidProject("Project identity changed")
            session = SessionMetadata(
                session_id=uuid4(),
                project_id=project.project_id,
                name=name,
                created_at=datetime.now(UTC),
            )
            target = project.root / f".novel-agent/sessions/{session.session_id}.json"
            atomic_write(target, json_bytes(session.model_dump(mode="json")))
            return session

    def list(self, project: Project) -> tuple[SessionMetadata, ...]:
        current = self._projects.open(project.root)
        if current.project_id != project.project_id:
            raise InvalidProject("Project identity changed")
        return current.sessions

    def load(self, project: Project, session_id: UUID) -> SessionMetadata:
        for session in self.list(project):
            if session.session_id == session_id:
                return session
        raise InvalidChange(f"Session does not exist: {session_id}")

    def update_settings(
        self,
        project: Project,
        session_id: UUID,
        *,
        execution_mode: str | None = None,
        auto_approval: bool | None = None,
        current_model: str | None = None,
        enabled_skills: tuple[str, ...] | None = None,
        disabled_skills: tuple[str, ...] | None = None,
        next_skill: str | None | object = _UNSET,
    ) -> SessionMetadata:
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            current = self._projects.open_unlocked(project.root)
            if current.project_id != project.project_id:
                raise InvalidProject("Project identity changed")
            old = next((s for s in current.sessions if s.session_id == session_id), None)
            if old is None:
                raise InvalidChange(f"Session does not exist: {session_id}")
            updates = {}
            if execution_mode is not None:
                updates["execution_mode"] = execution_mode
            if auto_approval is not None:
                updates["auto_approval"] = auto_approval
            if current_model is not None:
                updates["current_model"] = current_model
            if enabled_skills is not None:
                updates["enabled_skills"] = enabled_skills
            if disabled_skills is not None:
                updates["disabled_skills"] = disabled_skills
            if next_skill is not _UNSET:
                updates["next_skill"] = next_skill
            updated = SessionMetadata.model_validate({**old.model_dump(), **updates})
            target = project.root / f".novel-agent/sessions/{session_id}.json"
            atomic_write(target, json_bytes(updated.model_dump(mode="json")))
            return updated
