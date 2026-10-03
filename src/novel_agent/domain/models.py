"""Persisted Project/Session schemas and read-only service results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novel_agent.domain.chapter import ChapterDocument
from novel_agent.domain.errors import ChapterNotFound


class ChapterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chapter_id: UUID
    filename: str = Field(pattern=r"^[0-9]{4,}\.md$")


class ProjectMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    project_id: UUID
    name: str = Field(min_length=1)
    created_at: datetime
    initial_session_id: UUID
    next_chapter_number: int = Field(default=1, ge=1)
    chapter_order: tuple[UUID, ...] = ()
    chapters: dict[str, ChapterEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_chapter_index(self) -> ProjectMetadata:
        ids = [str(chapter_id) for chapter_id in self.chapter_order]
        if len(ids) != len(set(ids)) or set(ids) != set(self.chapters):
            raise ValueError("chapter_order must contain each registered chapter exactly once")
        filenames = [entry.filename for entry in self.chapters.values()]
        if len(filenames) != len(set(filenames)):
            raise ValueError("chapter filenames must be unique")
        if any(str(entry.chapter_id) != key for key, entry in self.chapters.items()):
            raise ValueError("chapter map keys must match chapter_id")
        return self


class SessionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    session_id: UUID
    project_id: UUID
    name: str = Field(min_length=1)
    created_at: datetime
    execution_mode: Literal["FREEFORM", "WORKFLOW"] = "FREEFORM"
    auto_approval: bool = False
    current_model: str | None = None
    enabled_skills: tuple[str, ...] = ()
    disabled_skills: tuple[str, ...] = ()
    next_skill: str | None = None


@dataclass(frozen=True)
class Project:
    root: Path
    metadata: ProjectMetadata
    sessions: tuple[SessionMetadata, ...]

    @property
    def project_id(self) -> UUID:
        return self.metadata.project_id


@dataclass(frozen=True)
class ProjectReadSnapshot:
    """Validated content for one operation; never persisted or cached between calls."""

    project: Project
    project_text: str
    chapters: Mapping[UUID, ChapterDocument]
    text_files: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "chapters", MappingProxyType(dict(self.chapters)))
        object.__setattr__(self, "text_files", MappingProxyType(dict(self.text_files)))

    def chapter(self, chapter_id: UUID) -> ChapterDocument:
        try:
            return self.chapters[chapter_id]
        except KeyError as exc:
            raise ChapterNotFound(f"Chapter is not registered: {chapter_id}") from exc


@dataclass(frozen=True)
class TransactionMaintenanceReport:
    pending_transactions: int
    receipt_count: int
    garbage_transactions: int
    staging_entries: int
    disk_bytes: int
    reclaimed_bytes: int
    warnings: tuple[str, ...] = ()
