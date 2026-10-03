"""Deterministic Freeform intent and operation-completion policy."""

from __future__ import annotations

import re
from uuid import UUID

from novel_agent.agent.freeform_types import FreeformRunScope
from novel_agent.domain.artifact import ArtifactType
from novel_agent.domain.chapter_selector import ordinal
from novel_agent.domain.errors import (
    InvalidChange,
)
from novel_agent.domain.models import Project
from novel_agent.infrastructure.filesystem.transaction import digest, read_bytes_or_none
from novel_agent.services.project import ProjectService

_COMMIT_INTENT = re.compile(r"提交|正式写入|正式保存|commit|写入项目", re.IGNORECASE)
_NEGATED_COMMIT = re.compile(
    r"(?:不要|无需|先别|暂不|别|不|未).{0,32}(?:提交|commit|写入项目)", re.IGNORECASE
)
_SECTION_TYPE = {
    "summary": ArtifactType.SUMMARY,
    "outline": ArtifactType.OUTLINE,
    "manuscript": ArtifactType.MANUSCRIPT,
}
_DELETE_VERB = r"(?:删除|删掉|移除)"
_CHAPTER_NUMBER = r"第([一二三四五六七八九十百零〇两\d]+)章"
_NEGATED_DELETE = re.compile(rf"(?:不要|先别|暂不|无需|不准|禁止|不能|别).{{0,12}}{_DELETE_VERB}")
_DELETION_DISCUSSION = re.compile(
    r"先给方案|只给方案|不要执行|暂不执行|先说方案|先说明|如何|怎么|如果|假如"
)


def _has_commit_intent(prompt: str) -> bool:
    return bool(_COMMIT_INTENT.search(prompt) and not _NEGATED_COMMIT.search(prompt))


def _requested_chapter_deletion(
    prompt: str, project: Project, projects: ProjectService
) -> UUID | None:
    """Bind an explicit whole-chapter deletion to an ID before the model runs."""
    if (
        _NEGATED_DELETE.search(prompt)
        or _DELETION_DISCUSSION.search(prompt)
        or re.search(rf"(?:请问|能否|可以|是否|为什么|如何).{{0,20}}{_DELETE_VERB}", prompt)
    ):
        return None
    direct = re.search(rf"{_DELETE_VERB}\s*{_CHAPTER_NUMBER}", prompt)
    reverse = re.search(rf"{_CHAPTER_NUMBER}\s*{_DELETE_VERB}", prompt)
    if direct or reverse:
        match = direct or reverse
        assert match is not None
        if direct and re.match(r"\s*(?:的|中|里|内)", prompt[match.end() :]):
            return None  # A request to delete text inside the chapter is not structural deletion.
        position = ordinal(match.group(1))
        if not 1 <= position <= len(project.metadata.chapter_order):
            raise InvalidChange(f"Chapter position does not exist: {position}")
        return project.metadata.chapter_order[position - 1]
    if not re.search(_DELETE_VERB, prompt):
        return None
    by_id = re.search(
        rf"{_DELETE_VERB}\s*(?:章节|chapter_id\s*=\s*)\s*"
        r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27,})",
        prompt,
    )
    if by_id:
        chapter_id = UUID(by_id.group(1))
        if chapter_id not in project.metadata.chapter_order:
            raise InvalidChange(f"Chapter does not exist: {chapter_id}")
        return chapter_id
    by_title = re.search(rf"{_DELETE_VERB}\s*(?:章节)?\s*《([^》]+)》", prompt)
    if by_title:
        title = by_title.group(1)
        matches = [
            chapter_id
            for chapter_id, chapter in projects.list_chapters(project)
            if chapter.title == title
        ]
        if len(matches) != 1:
            raise InvalidChange(f"Chapter title is missing or ambiguous: {title}")
        return matches[0]
    return None


def _chapter_delete_baselines(project: Project) -> dict[str, str | None]:
    paths = [".novel-agent/project.json", "MANUSCRIPT.md"]
    paths.extend(f"chapters/{entry.filename}" for entry in project.metadata.chapters.values())
    return {path: digest(read_bytes_or_none(project.root / path)) for path in paths}


class FreeformOperationPolicy:
    @staticmethod
    def expected(prompt: str, delete_target: UUID | None) -> str | None:
        expected = None
        if delete_target is not None:
            expected = "delete"
        elif _has_commit_intent(prompt) and re.search(r"Artifact|草稿|@\d", prompt, re.I):
            expected = "commit"
        elif re.search(r"修订|修改|改写|revision|revise", prompt, re.I) and re.search(
            r"Artifact|草稿", prompt, re.I
        ):
            expected = "revise"
        elif "create_working_artifact" in prompt or (
            re.search(r"生成|写|创作|保存", prompt)
            and re.search(r"大纲|正文|概述|章节|Artifact|草稿", prompt, re.I)
        ):
            expected = "create"

        return expected

    @staticmethod
    def completed(scope: FreeformRunScope, expected: str | None) -> bool:
        if expected == "delete":
            return bool(scope.result.commits) or scope.delete_blocker is not None
        if expected == "commit":
            return bool(scope.result.commits)
        if expected == "revise":
            return any(ref.revision > 1 for ref in scope.result.artifacts)
        if expected == "create":
            return bool(scope.result.artifacts)
        return True
