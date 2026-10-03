"""Deterministic formal Project change requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from novel_agent.domain.artifact import ArtifactRef

Section = Literal["summary", "outline", "manuscript"]


@dataclass(frozen=True)
class CreateChapter:
    title: str
    position: int | None = None  # zero-based; None appends
    chapter_id: UUID | None = None


@dataclass(frozen=True)
class SetSection:
    chapter_id: UUID
    section: Section
    text: str | None = None
    artifact: ArtifactRef | None = None


@dataclass(frozen=True)
class ReplaceText:
    chapter_id: UUID
    section: Section
    anchor: str
    replacement: str


@dataclass(frozen=True)
class InsertText:
    chapter_id: UUID
    section: Section
    anchor: str
    text: str
    position: Literal["before", "after"] = "after"


@dataclass(frozen=True)
class DeleteText:
    chapter_id: UUID
    section: Section
    anchor: str


@dataclass(frozen=True)
class RenameChapter:
    chapter_id: UUID
    title: str


@dataclass(frozen=True)
class DeleteChapter:
    chapter_id: UUID


@dataclass(frozen=True)
class MoveChapter:
    chapter_id: UUID
    position: int  # zero-based final position


@dataclass(frozen=True)
class SplitChapter:
    chapter_id: UUID
    anchor: str  # unique text beginning the new chapter manuscript
    new_title: str
    new_chapter_id: UUID | None = None


@dataclass(frozen=True)
class MergeChapters:
    first_id: UUID
    second_id: UUID  # must immediately follow first


@dataclass(frozen=True)
class SetProjectText:
    text: str | None = None
    artifact: ArtifactRef | None = None


Change = (
    CreateChapter
    | SetSection
    | ReplaceText
    | InsertText
    | DeleteText
    | RenameChapter
    | DeleteChapter
    | MoveChapter
    | SplitChapter
    | MergeChapters
    | SetProjectText
)


def new_chapter_id() -> UUID:
    """Reserve a stable chapter ID before creating dependent Artifacts."""
    return uuid4()
