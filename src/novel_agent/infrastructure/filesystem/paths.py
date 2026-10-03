"""Canonical Project-local path validation shared by storage and later CLI code."""

from __future__ import annotations

import os
from pathlib import Path

from novel_agent.domain.errors import InvalidAttachment, UnsafeProjectPath

_ATTACHMENT_SUFFIXES = frozenset({".md", ".txt"})


def resolve_project_file(root: Path, relative_path: str | Path) -> Path:
    """Resolve an existing file while rejecting traversal and protected internals."""
    project_root = root.resolve(strict=True)
    path = Path(relative_path)
    if not path.parts or path.is_absolute() or path.drive or ".." in path.parts:
        raise UnsafeProjectPath("Project file path must be relative and cannot traverse parents")
    if path.parts[0].casefold() == ".novel-agent":
        raise UnsafeProjectPath("Project internal state is not readable as content")
    candidate = project_root / path
    try:
        canonical = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileNotFoundError(f"Project file not found: {relative_path}") from exc
    if not canonical.is_relative_to(project_root):
        raise UnsafeProjectPath("Project file resolves outside the Project root")
    if not canonical.is_file():
        raise UnsafeProjectPath("Project path is not a regular file")
    return canonical


def resolve_attachment(root: Path, relative_path: str | Path) -> Path:
    """Validate a Project-local chat attachment before an Agent is invoked."""
    try:
        path = resolve_project_file(root, relative_path)
    except FileNotFoundError as exc:
        raise InvalidAttachment(str(exc)) from exc
    if path.suffix.casefold() not in _ATTACHMENT_SUFFIXES:
        raise InvalidAttachment("Chat attachments must be .txt or .md files")
    if not os.access(path, os.R_OK) or path.stat().st_size == 0:
        raise InvalidAttachment("Chat attachment is empty or unreadable")
    return path
