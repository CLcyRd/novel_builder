"""The sole public path from working content or explicit edits to formal Project files."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from novel_agent.domain.artifact import (
    ArtifactRef,
)
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
from novel_agent.domain.errors import (
    DecisionRequired,
    InvalidArtifact,
    InvalidChange,
    InvalidProject,
)
from novel_agent.domain.models import Project, ProjectReadSnapshot
from novel_agent.infrastructure.filesystem.transaction import (
    commit_files_unlocked,
    digest,
    get_manifest,
    project_lock,
    read_bytes_or_none,
    recover_pending_unlocked,
)
from novel_agent.ports.artifact_repository import ArtifactRepository
from novel_agent.ports.project_repository import ProjectRepository
from novel_agent.services.commit_changes import ChapterChangeReducer
from novel_agent.services.commit_content import CommitContentResolver
from novel_agent.services.commit_plan import CommitWritePlanner


class CommitResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commit_id: UUID
    status: Literal["COMPLETED"] = "COMPLETED"
    project_id: UUID
    changed_files: tuple[str, ...]
    changed_chapters: tuple[UUID, ...]
    committed_artifacts: tuple[ArtifactRef, ...]


class CommitStatus(StrEnum):
    PREPARED = "PREPARED"
    VALIDATING = "VALIDATING"
    APPLYING = "APPLYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class PreparedCommit:
    commit_id: UUID
    project_root: Path
    project_id: UUID
    fingerprint: str
    changes: tuple[Change, ...]
    baselines: dict[str, str | None]
    status: CommitStatus = CommitStatus.PREPARED


def _fingerprint(changes: tuple[Change, ...]) -> str:
    payload = [{"type": type(change).__name__, "fields": asdict(change)} for change in changes]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


class CommitService:
    def __init__(
        self,
        projects: ProjectRepository,
        artifacts: ArtifactRepository,
        failure_hook: Callable[[int, str], None] | None = None,
    ) -> None:
        self._projects = projects
        self._artifacts = artifacts
        self._failure_hook = failure_hook

    def prepare(
        self, project: Project, changes: tuple[Change, ...], *, commit_id: UUID | None = None
    ) -> PreparedCommit:
        if not changes:
            raise InvalidChange("Commit requires at least one Change")
        with project_lock(project.root):
            recover_pending_unlocked(project.root)
            current = self._projects.open_unlocked(project.root)
            if current.project_id != project.project_id:
                raise InvalidProject("Project identity changed")
            metadata = current.metadata
            touched: set[str] = set()
            structural = False
            rebuild = False
            artifact_refs: list[ArtifactRef] = []
            modified_sections: set[tuple[UUID, str]] = set()
            for change in changes:
                if isinstance(change, SetSection):
                    key = (change.chapter_id, change.section)
                    if key in modified_sections:
                        raise InvalidChange("Multiple changes to the same Chapter section")
                    modified_sections.add(key)
                if isinstance(
                    change,
                    CreateChapter | DeleteChapter | MoveChapter | SplitChapter | MergeChapters,
                ):
                    structural = True
                    rebuild = True
                if isinstance(change, RenameChapter):
                    rebuild = True
                if (
                    isinstance(change, SetSection | ReplaceText | InsertText | DeleteText)
                    and change.section == "manuscript"
                ):
                    rebuild = True
                for chapter_id in self._change_chapter_ids(change):
                    entry = metadata.chapters.get(str(chapter_id))
                    if entry:
                        touched.add(f"chapters/{entry.filename}")
                if isinstance(change, SetProjectText):
                    touched.add("PROJECT.md")
                if isinstance(change, SetSection | SetProjectText) and change.artifact:
                    artifact_refs.append(change.artifact)
            if structural:
                touched.add(".novel-agent/project.json")
            if rebuild:
                touched.add("MANUSCRIPT.md")
                touched.update(f"chapters/{entry.filename}" for entry in metadata.chapters.values())
            baselines = {
                relative: digest(read_bytes_or_none(project.root / relative))
                for relative in touched
            }
            for ref in artifact_refs:
                revision = self._artifacts.get_unlocked(project.root, ref)
                if revision.project_id != project.project_id:
                    raise InvalidArtifact("Artifact belongs to another Project")
                for relative, expected in revision.base_hashes.items():
                    existing = baselines.setdefault(relative, expected)
                    if existing != expected:
                        raise DecisionRequired(
                            "Artifact is based on stale Project content", (relative,)
                        )
                for dependency in revision.dependencies:
                    self._artifacts.get_unlocked(project.root, dependency)
            return PreparedCommit(
                commit_id=commit_id or uuid4(),
                project_root=project.root,
                project_id=project.project_id,
                fingerprint=_fingerprint(changes),
                changes=changes,
                baselines=baselines,
            )

    def apply(self, prepared: PreparedCommit) -> CommitResult:
        if prepared.status == CommitStatus.CANCELLED:
            raise InvalidChange("Cancelled Commit cannot be applied")
        prepared.status = CommitStatus.VALIDATING
        try:
            return self._apply_prepared(prepared)
        except Exception:
            if prepared.status == CommitStatus.VALIDATING or (
                prepared.status == CommitStatus.APPLYING
                and get_manifest(prepared.project_root, prepared.commit_id) is None
            ):
                prepared.status = CommitStatus.FAILED
            raise

    def _apply_prepared(self, prepared: PreparedCommit) -> CommitResult:
        root = prepared.project_root
        with project_lock(root):
            recover_pending_unlocked(root)
            existing = get_manifest(root, prepared.commit_id)
            if existing is not None:
                if existing.get("fingerprint") != prepared.fingerprint:
                    raise InvalidChange("Commit ID was reused for different changes")
                prepared.status = CommitStatus.COMPLETED
                return CommitResult.model_validate(existing["result"])
            snapshot = self._projects.read_snapshot_unlocked(root)
            current = snapshot.project
            if current.project_id != prepared.project_id:
                raise InvalidProject("Project identity changed")
            stale = tuple(
                relative
                for relative, expected in prepared.baselines.items()
                if digest(read_bytes_or_none(root / relative)) != expected
            )
            if stale:
                raise DecisionRequired("Project changed after Commit preparation", stale)
            writes, changed_chapters, artifact_refs = self._render(
                snapshot, prepared.changes, prepared.commit_id
            )
            for relative, after in list(writes.items()):
                if relative not in prepared.baselines:
                    prepared.baselines[relative] = (
                        None
                        if relative.startswith("chapters/")
                        else digest(read_bytes_or_none(root / relative))
                    )
                if after == read_bytes_or_none(root / relative):
                    del writes[relative]
            if not writes:
                raise InvalidChange("Commit has no effect")
            formal_files = tuple(
                sorted(relative for relative in writes if not relative.startswith(".novel-agent/"))
            )
            result = CommitResult(
                commit_id=prepared.commit_id,
                project_id=prepared.project_id,
                changed_files=formal_files,
                changed_chapters=tuple(dict.fromkeys(changed_chapters)),
                committed_artifacts=tuple(
                    {(ref.artifact_id, ref.revision): ref for ref in artifact_refs}.values()
                ),
            )
            prepared.status = CommitStatus.APPLYING
            payload = commit_files_unlocked(
                root,
                prepared.commit_id,
                prepared.fingerprint,
                writes,
                prepared.baselines,
                result.model_dump(mode="json"),
                self._failure_hook,
            )
            prepared.status = CommitStatus.COMPLETED
            return CommitResult.model_validate(payload)

    @staticmethod
    def cancel(prepared: PreparedCommit) -> None:
        if prepared.status not in {CommitStatus.PREPARED, CommitStatus.FAILED}:
            raise InvalidChange("Only an unapplied Commit can be cancelled")
        prepared.status = CommitStatus.CANCELLED

    def commit(
        self, project: Project, changes: tuple[Change, ...], *, commit_id: UUID | None = None
    ) -> CommitResult:
        if commit_id is not None:
            with project_lock(project.root):
                recover_pending_unlocked(project.root)
                existing = get_manifest(project.root, commit_id)
                if existing is not None:
                    if existing.get("fingerprint") != _fingerprint(changes):
                        raise InvalidChange("Commit ID was reused for different changes")
                    return CommitResult.model_validate(existing["result"])
        return self.apply(self.prepare(project, changes, commit_id=commit_id))

    @staticmethod
    def _change_chapter_ids(change: Change) -> tuple[UUID, ...]:
        if isinstance(change, MergeChapters):
            return change.first_id, change.second_id
        if isinstance(change, SetProjectText):
            return ()
        if isinstance(change, CreateChapter):
            return (change.chapter_id,) if change.chapter_id else ()
        return (change.chapter_id,)

    def _render(
        self, snapshot: ProjectReadSnapshot, changes: tuple[Change, ...], commit_id: UUID
    ) -> tuple[dict[str, bytes | None], list[UUID], list[ArtifactRef]]:
        reducer = ChapterChangeReducer(snapshot)
        resolver = CommitContentResolver(self._artifacts)
        artifact_refs: list[ArtifactRef] = []
        for change in changes:
            reducer.validate_content_target(change)
            text = (
                resolver.resolve(snapshot.project, change, artifact_refs)
                if isinstance(change, SetSection | SetProjectText)
                else None
            )
            needs_id = (
                isinstance(change, CreateChapter)
                and change.chapter_id is None
                or isinstance(change, SplitChapter)
                and change.new_chapter_id is None
            )
            reserved = uuid4() if needs_id else None
            reducer.apply(change, text, new_id=reserved)
        plan = CommitWritePlanner.build(snapshot, reducer)
        plan.writes.update(resolver.status_writes(snapshot.project.root, artifact_refs, commit_id))
        return plan.writes, list(plan.changed_chapters), artifact_refs
