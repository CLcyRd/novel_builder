"""Local-first novel Project foundation."""

from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
    build_session_service,
)

__all__ = [
    "build_project_service",
    "build_artifact_service",
    "build_commit_service",
    "build_session_service",
]
