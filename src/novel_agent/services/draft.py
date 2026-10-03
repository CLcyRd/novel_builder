"""Editable chapter copies backed by immutable Artifact revisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from novel_agent.config.setting import DEFAULT_SETTINGS
from novel_agent.domain.artifact import ArtifactRef
from novel_agent.domain.chapter import ChapterDocument, parse_chapter, serialize_chapter
from novel_agent.domain.errors import DecisionRequired, InvalidChange, UnsafeProjectPath
from novel_agent.domain.models import Project
from novel_agent.infrastructure.filesystem.transaction import atomic_write, json_bytes, project_lock
from novel_agent.services.artifact import ArtifactService

SECTIONS = ("summary", "outline", "manuscript")
LABELS = {"summary": "章节概述", "outline": "章节大纲", "manuscript": "章节正文"}


@dataclass(frozen=True)
class DraftSync:
    refs: dict[str, ArtifactRef]
    changed: tuple[str, ...]
    path: str


class DraftService:
    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts

    @staticmethod
    def _safe(project: Project, relative: str) -> Path:
        root = project.root.resolve(strict=True)
        path = Path(relative)
        if path.is_absolute() or path.drive or ".." in path.parts:
            raise UnsafeProjectPath("工作稿路径必须位于当前 Project 内")
        target = root / path
        for item in (target, *target.parents):
            if item == root:
                break
            if item.is_symlink() or item.is_junction():
                raise UnsafeProjectPath("工作稿路径不允许符号链接或目录联接")
        if not target.resolve().is_relative_to(root):
            raise UnsafeProjectPath("工作稿路径越过 Project root")
        return target

    @staticmethod
    def _meta(session_id: UUID, task_id: UUID, chapter_id: UUID) -> str:
        return f".novel-agent/working/{session_id}/{task_id}/{chapter_id}.json"

    def record(self, project: Project, session_id: UUID, task_id: UUID, chapter_id: UUID):
        path = self._safe(project, self._meta(session_id, task_id, chapter_id))
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _save(self, project: Project, record: dict) -> None:
        relative = self._meta(
            UUID(record["session_id"]), UUID(record["task_id"]), UUID(record["chapter_id"])
        )
        atomic_write(self._safe(project, relative), json_bytes(record))

    @staticmethod
    def _hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def dirty(self, project: Project, record: dict) -> bool:
        path = self._safe(project, record["path"])
        return path.exists() and self._hash(path.read_bytes()) != record["file_hash"]

    def export(
        self,
        project: Project,
        session_id: UUID,
        task_id: UUID,
        chapter_id: UUID,
        title: str,
        refs: dict[str, ArtifactRef],
        *,
        replace_refs: bool = False,
    ) -> str:
        with project_lock(project.root):
            record = self.record(project, session_id, task_id, chapter_id)
            if record and self.dirty(project, record):
                raise DecisionRequired(
                    "生成期间工作稿又被修改，请选择保留手动稿或重新生成。", (record["path"],)
                )
            # Split-stage workflows bind exactly their active sections. Unbound
            # downstream revisions remain in Artifact history, not in the draft.
            merged = dict(record["refs"]) if record and not replace_refs else {}
            merged = {
                key: value
                for key, value in merged.items()
                if self.artifacts.status(project, ArtifactRef.model_validate(value)).status
                not in {"COMMITTED", "DISCARDED"}
            }
            merged.update({key: ref.model_dump(mode="json") for key, ref in refs.items()})
            values = {
                key: self.artifacts.get(project, ArtifactRef.model_validate(merged[key])).content
                if key in merged
                else ""
                for key in SECTIONS
            }
            document = ChapterDocument(title=title, **values)
            text = serialize_chapter(document)
            for key in SECTIONS:
                text = text.replace(
                    f"<!-- novel-agent:{key}:start -->\n",
                    f"## {LABELS[key]}\n<!-- novel-agent:{key}:start -->\n",
                    1,
                )
            relative = f"working/{session_id}/{task_id}/{chapter_id}.draft.md"
            data = text.encode("utf-8")
            if record and self.dirty(project, record):
                raise DecisionRequired("生成期间工作稿又被修改，文件已保留。", (record["path"],))
            atomic_write(self._safe(project, relative), data)
            self._save(
                project,
                {
                    "session_id": str(session_id),
                    "task_id": str(task_id),
                    "chapter_id": str(chapter_id),
                    "title": title,
                    "path": relative,
                    "refs": merged,
                    "file_hash": self._hash(data),
                    "active": True,
                },
            )
            return relative

    def sync(
        self,
        project: Project,
        session_id: UUID,
        task_id: UUID,
        chapter_id: UUID,
    ) -> DraftSync | None:
        with project_lock(project.root):
            record = self.record(project, session_id, task_id, chapter_id)
            if not record:
                return None
            refs = {key: ArtifactRef.model_validate(value) for key, value in record["refs"].items()}
            if not record["active"]:
                return DraftSync(refs, (), record["path"])
            path = self._safe(project, record["path"])
            if not path.is_file():
                raise InvalidChange(f"工作稿文件缺失：{record['path']}；请恢复文件后再确认。")
            data = path.read_bytes()
            if len(data) > DEFAULT_SETTINGS.draft_max_bytes:
                raise InvalidChange("工作稿文件超过大小限制")
            text = data.decode("utf-8-sig").replace("\r\n", "\n")
            for key in SECTIONS:
                text = text.replace(
                    f"## {LABELS[key]}\n<!-- novel-agent:{key}:start -->",
                    f"<!-- novel-agent:{key}:start -->",
                    1,
                )
            document = parse_chapter(text)
            if document.title != record["title"]:
                raise InvalidChange("请保留工作稿章节标题，当前只支持修改概述、大纲与正文。")
            changed = []
            for key in SECTIONS:
                content = getattr(document, key)
                if key not in refs:
                    if content:
                        raise InvalidChange(f"{LABELS[key]}尚未生成，请保留该部分为空。")
                    continue
                old = self.artifacts.get(project, refs[key])
                prior_sections = SECTIONS[: SECTIONS.index(key)]
                dependencies = tuple(
                    ref
                    for ref in old.dependencies
                    if self.artifacts.get(project, ref).chapter_id != chapter_id
                    or self.artifacts.get(project, ref).section not in prior_sections
                ) + tuple(refs[name] for name in prior_sections if name in refs)
                index = next(
                    item
                    for item in self.artifacts.list(project, session_id)
                    if item.artifact_id == old.artifact_id
                )
                latest = self.artifacts.get_by_id(project, old.artifact_id, index.latest_revision)
                if (
                    content == old.content
                    and set(dependencies) == set(old.dependencies)
                    and latest.ref == old.ref
                ):
                    continue
                if not content.strip():
                    raise InvalidChange(f"{LABELS[key]}不能清空")
                # A checkpoint replay may find the revision already saved before a crash.
                updated = (
                    latest
                    if (latest.content == content and set(latest.dependencies) == set(dependencies))
                    else self.artifacts.revise(
                        project,
                        latest.ref,
                        content,
                        skill_refs=old.skill_refs,
                        dependencies=dependencies,
                    )
                )
                refs[key] = updated.ref
                record["refs"][key] = updated.ref.model_dump(mode="json")
                self._save(project, record)
                changed.append(key)
            record["file_hash"] = self._hash(data)
            self._save(project, record)
            return DraftSync(refs, tuple(changed), record["path"])

    def clean(self, project: Project, session_id: UUID, task_id: UUID, chapter_id: UUID) -> bool:
        with project_lock(project.root):
            record = self.record(project, session_id, task_id, chapter_id)
            if not record:
                return True
            if self.dirty(project, record):
                return False  # Later unsaved edits are not part of the confirmed Commit.
            # Persist the cleanup intent first so restart does not accept new edits implicitly.
            record["active"] = False
            self._save(project, record)
            self._safe(project, record["path"]).unlink(missing_ok=True)
            return True

    def find(self, project: Project, session_id: UUID, ref: ArtifactRef):
        directory = self._safe(project, f".novel-agent/working/{session_id}")
        if directory.exists():
            for path in directory.glob("*/*.json"):
                record = json.loads(
                    self._safe(project, path.relative_to(project.root).as_posix()).read_text(
                        encoding="utf-8"
                    )
                )
                if any(
                    value["artifact_id"] == str(ref.artifact_id)
                    for value in record["refs"].values()
                ):
                    return record
        return None
