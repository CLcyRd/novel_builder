"""Produce deterministic write payloads from in-memory Chapter changes."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from novel_agent.domain.chapter import ChapterDocument, serialize_chapter
from novel_agent.domain.models import ChapterEntry, ProjectMetadata, ProjectReadSnapshot
from novel_agent.infrastructure.filesystem.transaction import (
    json_bytes,
)
from novel_agent.services.commit_changes import ChapterChangeReducer


def _rebuild_manuscript(order: list[UUID], documents: dict[UUID, ChapterDocument]) -> bytes:
    parts = [
        f"# {documents[chapter_id].title}\n\n{documents[chapter_id].manuscript}"
        for chapter_id in order
    ]
    return ("\n\n".join(parts) + ("\n" if parts else "")).encode("utf-8")


@dataclass(frozen=True)
class CommitWritePlan:
    writes: dict[str, bytes | None]
    changed_chapters: tuple[UUID, ...]


class CommitWritePlanner:
    """Serialize the changed in-memory project; perform no reads or writes."""

    @staticmethod
    def build(snapshot: ProjectReadSnapshot, changes: ChapterChangeReducer) -> CommitWritePlan:
        metadata = snapshot.project.metadata
        order = changes.order
        entries = dict(changes.entries)
        documents = changes.documents
        changed_ids = list(changes.changed_ids)
        project_text = changes.project_text
        structure = changes.structure
        rebuild = changes.rebuild
        next_number = changes.next_number
        writes: dict[str, bytes | None] = {}
        if structure:
            # Chapter order is the user-facing numbering. A structural Commit
            # moves all surviving files to that order in the same transaction.
            for number, chapter_id in enumerate(order, 1):
                filename = f"{number:04d}.md"
                previous = metadata.chapters.get(str(chapter_id))
                if previous is not None and previous.filename != filename:
                    changed_ids.append(chapter_id)
                entries[str(chapter_id)] = ChapterEntry(chapter_id=chapter_id, filename=filename)
            next_number = len(order) + 1
            old_paths = {f"chapters/{entry.filename}" for entry in metadata.chapters.values()}
            new_paths = {f"chapters/{entry.filename}" for entry in entries.values()}
            for relative in old_paths - new_paths:
                writes[relative] = None
            for chapter_id in order:
                entry = entries[str(chapter_id)]
                writes[f"chapters/{entry.filename}"] = serialize_chapter(
                    documents[chapter_id]
                ).encode("utf-8")
            new_metadata = ProjectMetadata.model_validate(
                {
                    **metadata.model_dump(mode="json"),
                    "chapter_order": [str(chapter_id) for chapter_id in order],
                    "chapters": {
                        key: entry.model_dump(mode="json") for key, entry in entries.items()
                    },
                    "next_chapter_number": next_number,
                }
            )
            writes[".novel-agent/project.json"] = json_bytes(new_metadata.model_dump(mode="json"))
        else:
            for chapter_id in dict.fromkeys(changed_ids):
                entry = entries[str(chapter_id)]
                writes[f"chapters/{entry.filename}"] = serialize_chapter(
                    documents[chapter_id]
                ).encode("utf-8")
        if rebuild:
            writes["MANUSCRIPT.md"] = _rebuild_manuscript(order, documents)
        if project_text is not None:
            writes["PROJECT.md"] = project_text.encode("utf-8")
        return CommitWritePlan(writes, tuple(changed_ids))
