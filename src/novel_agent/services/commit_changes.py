"""Apply ordered Chapter changes to an isolated in-memory working copy."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from novel_agent.domain.change import (
    Change,
    CreateChapter,
    DeleteChapter,
    DeleteText,
    InsertText,
    MergeChapters,
    MoveChapter,
    RenameChapter,
    ReplaceText,
    SetProjectText,
    SetSection,
    SplitChapter,
)
from novel_agent.domain.chapter import SECTIONS, ChapterDocument, serialize_chapter
from novel_agent.domain.errors import (
    DecisionRequired,
    InvalidChange,
)
from novel_agent.domain.models import ChapterEntry, ProjectReadSnapshot


def _joined(first: str, second: str) -> str:
    return "\n\n".join(part for part in (first, second) if part)


class ChapterChangeReducer:
    """Apply ordered changes in memory, without filesystem or Artifact access."""

    def __init__(self, snapshot: ProjectReadSnapshot) -> None:
        metadata = snapshot.project.metadata
        self.order = list(metadata.chapter_order)
        self.entries = dict(metadata.chapters)
        self.documents = dict(snapshot.chapters)
        self.changed_ids: list[UUID] = []
        self.project_text: str | None = None
        self.structure = False
        self.rebuild = False
        self.next_number = max(
            [metadata.next_chapter_number]
            + [int(entry.filename[:-3]) + 1 for entry in self.entries.values()]
        )

    def validate_content_target(self, change: Change) -> None:
        if isinstance(change, SetSection):
            self._document(self.documents, change.chapter_id)
            if change.section not in SECTIONS:
                raise InvalidChange("Unknown Chapter section")

    def apply(self, change: Change, text: str | None = None, *, new_id: UUID | None = None) -> None:
        if isinstance(
            change,
            SetSection | ReplaceText | InsertText | DeleteText | RenameChapter | SetProjectText,
        ):
            self._apply_content(change, text)
        elif isinstance(
            change, CreateChapter | DeleteChapter | MoveChapter | SplitChapter | MergeChapters
        ):
            self._apply_structure(change, new_id)
        else:
            raise InvalidChange("Unsupported Project Change")

    def _apply_content(self, change: Change, text: str | None) -> None:
        if isinstance(change, SetSection):
            assert text is not None
            document = self._document(self.documents, change.chapter_id)
            if change.section not in SECTIONS:
                raise InvalidChange("Unknown Chapter section")
            self.documents[change.chapter_id] = replace(document, **{change.section: text})
            self.changed_ids.append(change.chapter_id)
            self.rebuild |= change.section == "manuscript"

        elif isinstance(change, ReplaceText | InsertText | DeleteText):
            document = self._document(self.documents, change.chapter_id)
            if change.section not in SECTIONS or not change.anchor:
                raise InvalidChange("Invalid text replacement section or anchor")
            old = getattr(document, change.section)
            if old.count(change.anchor) != 1:
                raise DecisionRequired("Text anchor missing or ambiguous")
            if isinstance(change, ReplaceText):
                replacement = change.replacement
            elif isinstance(change, DeleteText):
                replacement = ""
            elif change.position == "before":
                replacement = change.text + change.anchor
            elif change.position == "after":
                replacement = change.anchor + change.text
            else:
                raise InvalidChange("Insert position must be before or after")
            self.documents[change.chapter_id] = replace(
                document, **{change.section: old.replace(change.anchor, replacement, 1)}
            )
            self.changed_ids.append(change.chapter_id)
            self.rebuild |= change.section == "manuscript"

        elif isinstance(change, RenameChapter):
            document = self._document(self.documents, change.chapter_id)
            self.documents[change.chapter_id] = replace(document, title=change.title)
            self.changed_ids.append(change.chapter_id)
            self.rebuild = True

        elif isinstance(change, SetProjectText):
            assert text is not None
            self.project_text = text

    def _apply_structure(self, change: Change, new_id: UUID | None) -> None:
        if isinstance(change, CreateChapter):
            chapter_id = change.chapter_id or new_id
            assert chapter_id is not None
            if chapter_id in self.documents:
                raise InvalidChange("Chapter ID already exists")
            if change.position is not None and not 0 <= change.position <= len(self.order):
                raise InvalidChange("Invalid chapter insertion position")
            document = ChapterDocument(change.title, "", "", "")
            serialize_chapter(document)
            self.documents[chapter_id] = document
            filename = f"{self.next_number:04d}.md"
            self.next_number += 1
            self.entries[str(chapter_id)] = ChapterEntry(chapter_id=chapter_id, filename=filename)
            self.order.insert(
                len(self.order) if change.position is None else change.position, chapter_id
            )
            self.changed_ids.append(chapter_id)
            self.structure = self.rebuild = True

        elif isinstance(change, DeleteChapter):
            self._document(self.documents, change.chapter_id)
            del self.documents[change.chapter_id]
            del self.entries[str(change.chapter_id)]
            self.order.remove(change.chapter_id)
            self.changed_ids.append(change.chapter_id)
            self.structure = self.rebuild = True

        elif isinstance(change, MoveChapter):
            self._document(self.documents, change.chapter_id)
            if not 0 <= change.position < len(self.order):
                raise InvalidChange("Invalid chapter move position")
            self.order.remove(change.chapter_id)
            self.order.insert(change.position, change.chapter_id)
            self.changed_ids.append(change.chapter_id)
            self.structure = self.rebuild = True

        elif isinstance(change, SplitChapter):
            document = self._document(self.documents, change.chapter_id)
            if not change.anchor or document.manuscript.count(change.anchor) != 1:
                raise DecisionRequired("Split anchor missing or ambiguous")
            new_id = change.new_chapter_id or new_id
            assert new_id is not None
            if new_id in self.documents:
                raise InvalidChange("Split destination Chapter ID already exists")
            split_at = document.manuscript.index(change.anchor)
            first, second = document.manuscript[:split_at], document.manuscript[split_at:]
            if not first or not second:
                raise InvalidChange("Split would create an empty manuscript")
            self.documents[change.chapter_id] = replace(document, manuscript=first)
            new_document = ChapterDocument(change.new_title, "", "", second)
            serialize_chapter(new_document)
            self.documents[new_id] = new_document
            self.entries[str(new_id)] = ChapterEntry(
                chapter_id=new_id, filename=f"{self.next_number:04d}.md"
            )
            self.next_number += 1
            self.order.insert(self.order.index(change.chapter_id) + 1, new_id)
            self.changed_ids.extend((change.chapter_id, new_id))
            self.structure = self.rebuild = True

        elif isinstance(change, MergeChapters):
            first = self._document(self.documents, change.first_id)
            second = self._document(self.documents, change.second_id)
            if self.order.index(change.second_id) != self.order.index(change.first_id) + 1:
                raise InvalidChange("Only adjacent Chapters can be merged in this version")
            self.documents[change.first_id] = ChapterDocument(
                first.title,
                _joined(first.summary, second.summary),
                _joined(first.outline, second.outline),
                _joined(first.manuscript, second.manuscript),
            )
            del self.documents[change.second_id]
            del self.entries[str(change.second_id)]
            self.order.remove(change.second_id)
            self.changed_ids.extend((change.first_id, change.second_id))
            self.structure = self.rebuild = True

    @staticmethod
    def _document(documents: dict[UUID, ChapterDocument], chapter_id: UUID) -> ChapterDocument:
        try:
            return documents[chapter_id]
        except KeyError as exc:
            raise InvalidChange(f"Chapter does not exist: {chapter_id}") from exc
