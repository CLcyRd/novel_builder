"""Prepare validated attachments, Skills, and model input messages."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from langchain_core.messages import HumanMessage

from novel_agent.agent.freeform_policy import (
    _chapter_delete_baselines,
    _requested_chapter_deletion,
)
from novel_agent.agent.freeform_types import FreeformRunScope, TaskContext
from novel_agent.domain.errors import (
    InvalidChange,
)
from novel_agent.prompts.prompt import (
    CURRENT_DRAFT_RULE,
)


class FreeformInputBuilder:
    @staticmethod
    def prepare(scope: FreeformRunScope, prompt: str, attachments: Sequence[str | Path]):
        scope.ignore_skills = bool(re.search(r"(?:不要|不用|禁止).{0,4}使用\s*Skill", prompt, re.I))
        scope.available_skills = (
            () if scope.ignore_skills else scope.agent.skills.enabled(scope.project, scope.session)
        )
        mentioned = tuple(
            skill.ref.name
            for skill in scope.available_skills
            if skill.ref.name in prompt.casefold()
        )
        scope.selected_skill = (
            None
            if scope.ignore_skills
            else (scope.session.next_skill or (mentioned[0] if len(mentioned) == 1 else None))
        )
        if scope.selected_skill and scope.selected_skill not in {
            skill.ref.name for skill in scope.available_skills
        }:
            raise InvalidChange(f"Selected Skill is not enabled: {scope.selected_skill}")
        scope.skill_files = scope.agent.skills.virtual_files(scope.available_skills)
        scope.selected_definition = next(
            (skill for skill in scope.available_skills if skill.ref.name == scope.selected_skill),
            None,
        )
        scope.used_skills: set[str] = {scope.selected_skill} if scope.selected_skill else set()

        if scope.selected_skill:
            scope.emit("Reading", f"Skill {scope.selected_skill}")

        safe_attachments: list[tuple[str, str]] = []
        if len(attachments) > 8:
            raise InvalidChange("At most 8 attachments are supported")
        for name in attachments:
            path = scope.agent.projects.resolve_attachment(scope.project, name)
            content = path.read_text(encoding="utf-8")
            if len(content) > 32_000:
                raise InvalidChange(f"Attachment exceeds 32,000 characters: {name}")
            safe_attachments.append((path.relative_to(scope.project.root).as_posix(), content))
        if sum(len(content) for _, content in safe_attachments) > 100_000:
            raise InvalidChange("Combined attachments exceed 100,000 characters")
        scope.task = TaskContext(
            prompt, tuple(name for name, _ in safe_attachments), scope.model_alias
        )
        scope.result.attachment_paths = scope.task.attachment_paths
        scope.delete_target = _requested_chapter_deletion(
            prompt, scope.project, scope.agent.projects
        )
        scope.delete_baselines = (
            _chapter_delete_baselines(scope.project) if scope.delete_target else {}
        )

        scope.emit("Thinking", f"{scope.model_alias} · {scope.project.metadata.name}")

        return safe_attachments

    @staticmethod
    def messages(scope: FreeformRunScope, prompt: str, safe_attachments: list[tuple[str, str]]):
        attachment_text = "".join(
            f"\n<attachment path={name!r}>\n{text}\n</attachment>"
            for name, text in safe_attachments
        )
        if safe_attachments:
            scope.emit("Reading", ", ".join(name for name, _ in safe_attachments))
        selected_text = (
            f"\n<selected_skill name={scope.selected_skill!r}>\n"
            + scope.selected_definition.files["SKILL.md"].decode("utf-8")
            + "\n</selected_skill>"
            if scope.selected_definition
            else ""
        )
        draft_context = ""
        if scope.state.pending_draft and not scope.state.active_workflow_id:
            pending = scope.state.pending_draft
            record = scope.agent.drafts.record(
                scope.project,
                scope.session.session_id,
                UUID(pending["task_id"]),
                UUID(pending["chapter_id"]),
            )
            if record:
                references = ", ".join(
                    f"{key}: {value['artifact_id']}@{value['revision']}"
                    for key, value in record["refs"].items()
                )
                draft_context = CURRENT_DRAFT_RULE.format(
                    chapter_id=pending["chapter_id"], references=references
                )
        messages = [
            *scope.state.history,
            HumanMessage(
                content=selected_text
                + draft_context
                + ("\n用户请求：\n" if selected_text else "")
                + prompt
                + attachment_text
            ),
        ]

        return messages
