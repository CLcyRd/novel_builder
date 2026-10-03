"""Choose frozen, read-only writing Skills for a Workflow Request."""

from __future__ import annotations

import json
import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from novel_agent.domain.errors import InvalidChange
from novel_agent.domain.models import Project, SessionMetadata
from novel_agent.infrastructure.models.gateway import ModelGateway, ModelInvocationError
from novel_agent.infrastructure.models.langchain import build_chat_model
from novel_agent.prompts.prompt import skill_selector_prompt
from novel_agent.services.artifact import ArtifactService
from novel_agent.services.skill import SkillDefinition, SkillService
from novel_agent.workflow.models import WorkflowRequest, WorkflowSkillBinding


class SkillChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    chapter_indexes: tuple[int, ...] = ()


class SkillChoices(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selections: tuple[SkillChoice, ...] = ()


class SkillSelector(Protocol):
    def select(
        self,
        instruction: str,
        chapter_briefs: tuple[str, ...],
        candidates: tuple[SkillDefinition, ...],
        *,
        model: str | None = None,
    ) -> SkillChoices: ...


class LLMSkillSelector:
    """The model sees catalog metadata, never Skill body or file paths."""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def select(
        self,
        instruction: str,
        chapter_briefs: tuple[str, ...],
        candidates: tuple[SkillDefinition, ...],
        *,
        model: str | None = None,
    ) -> SkillChoices:
        catalog = [
            {
                "name": item.ref.name,
                "description": item.description,
                "tags": item.tags,
                "stages": item.stages,
            }
            for item in candidates
        ]
        task = {
            "instruction": instruction[:2500],
            "chapters": [brief[:1000] for brief in chapter_briefs],
        }
        prompt = skill_selector_prompt(task, catalog)
        content = build_chat_model(self.gateway, model).invoke(prompt).content
        if not isinstance(content, str) or not content.strip():
            raise ModelInvocationError("Workflow Skill selector returned no content")
        value = content.strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
        try:
            return SkillChoices.model_validate(json.loads(value))
        except (ValueError, ValidationError) as exc:
            raise ModelInvocationError("Workflow Skill selector returned invalid JSON") from exc


class WorkflowSkillResolver:
    def __init__(
        self,
        skills: SkillService,
        artifacts: ArtifactService,
        selector: SkillSelector,
    ) -> None:
        self.skills = skills
        self.artifacts = artifacts
        self.selector = selector

    def _briefs(self, project: Project, request: WorkflowRequest) -> tuple[str, ...]:
        if not request.chapters:
            return (request.instruction,)
        return tuple(
            target.brief
            + (
                "\n" + self.artifacts.get(project, target.brief_ref).content
                if target.brief_ref
                else ""
            )
            for target in request.chapters
        )

    def resolve(
        self, project: Project, session: SessionMetadata, request: WorkflowRequest
    ) -> WorkflowRequest:
        enabled = self.skills.enabled(project, session)
        briefs = self._briefs(project, request)
        full_text = "\n".join((request.instruction, *briefs)).casefold()
        if re.search(r"(?:不要|不用|禁止).{0,4}使用\s*skill", full_text):
            return request
        excluded = {
            skill.ref.name
            for skill in enabled
            if re.search(rf"(?:不要|不用|禁用).{{0,12}}{re.escape(skill.ref.name)}", full_text)
        }
        explicit_name = session.next_skill
        if explicit_name and explicit_name not in {skill.ref.name for skill in enabled}:
            raise InvalidChange(f"Selected Skill is not enabled: {explicit_name}")
        explicit = tuple(
            skill
            for skill in enabled
            if skill.ref.name not in excluded
            and (
                skill.ref.name == explicit_name
                or (not explicit_name and skill.ref.name in full_text)
            )
        )
        if explicit:
            bindings = tuple(
                WorkflowSkillBinding(ref=self.skills.snapshot(project, skill), reason="explicit")
                for skill in explicit[:2]
            )
            return request.model_copy(
                update={
                    "skill_refs": tuple(binding.ref for binding in bindings),
                    "skill_bindings": bindings,
                }
            )

        def negated(skill: SkillDefinition) -> bool:
            return any(
                re.search(rf"(?:不要|避免|禁止).{{0,10}}{re.escape(tag)}", full_text)
                for tag in skill.tags
            )

        candidates = tuple(
            skill
            for skill in enabled
            if skill.activation == "auto"
            and skill.ref.name not in excluded
            and not negated(skill)
            and (not skill.workflows or request.kind in skill.workflows)
        )
        if not candidates:
            return request
        frozen = {skill.ref.name: self.skills.snapshot(project, skill) for skill in candidates}
        bindings = []
        for skill in candidates:
            matches = tuple(
                index
                for index, brief in enumerate(briefs)
                if skill.tags
                and any(
                    tag in (request.instruction + "\n" + brief).casefold() for tag in skill.tags
                )
            )
            if matches:
                bindings.append(
                    WorkflowSkillBinding(
                        ref=frozen[skill.ref.name],
                        chapter_indexes=matches if len(matches) != len(briefs) else (),
                        reason="tag",
                    )
                )
        if not bindings or len(bindings) > 2:
            choices = self.selector.select(
                request.instruction, briefs, candidates, model=session.current_model
            )
            by_name = {skill.ref.name: skill for skill in candidates}
            bindings = []
            for choice in choices.selections:
                if choice.name not in by_name:
                    raise InvalidChange(f"Skill selector chose an unavailable Skill: {choice.name}")
                if any(index < 0 or index >= len(briefs) for index in choice.chapter_indexes):
                    raise InvalidChange("Skill selector chose an invalid Chapter index")
                if choice.name in {binding.ref.name for binding in bindings}:
                    continue
                bindings.append(
                    WorkflowSkillBinding(
                        ref=frozen[choice.name],
                        chapter_indexes=choice.chapter_indexes,
                        reason="model",
                    )
                )
            if len(bindings) > 2:
                raise InvalidChange("Workflow supports at most two automatic Skills")
        return request.model_copy(
            update={
                "skill_refs": tuple(binding.ref for binding in bindings),
                "skill_candidate_refs": tuple(frozen.values()),
                "skill_bindings": tuple(bindings),
            }
        )
