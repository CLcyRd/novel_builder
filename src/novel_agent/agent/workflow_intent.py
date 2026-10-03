"""Semantic creative routing with deterministic chapter evidence checks."""

from __future__ import annotations

import json
import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from novel_agent.domain.chapter_selector import ordinal
from novel_agent.domain.errors import DecisionRequired, InvalidChange
from novel_agent.infrastructure.models.gateway import ModelGateway, ModelInvocationError
from novel_agent.infrastructure.models.langchain import build_chat_model
from novel_agent.prompts.prompt import intent_prompt

NUMBER = r"[一二三四五六七八九十百零〇两\d]+"
_TOTAL = re.compile(
    rf"(?:小说|全书|整部(?:小说|故事)|故事).{{0,8}}?"
    rf"(?:共|总共|一共|有|分为|规划为|计划为)\s*({NUMBER})\s*个?\s*章(?:节)?"
)
_REVERSE_TOTAL = re.compile(rf"({NUMBER})\s*个?\s*章(?:节)?\s*的?.{{0,8}}?小说")
_FIRST = re.compile(rf"(?:先|本次|这次|目前).{{0,14}}?前\s*({NUMBER})\s*章")
_CURRENT_COUNT = re.compile(
    rf"(?:先|本次|这次|目前).{{0,10}}?(?:写|创作|生成|完成)\s*({NUMBER})\s*章"
)
_HEADING = re.compile(rf"第({NUMBER})章\s*[:：]")
_SIMPLE_COUNT = re.compile(rf"(?:写|创作|生成|构思)\s*({NUMBER})\s*章")


class PlannedChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1)
    brief: str = ""


class CreativePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal[
        "CREATE_CHAPTER", "MULTI_CHAPTER", "CONTINUE_CHAPTER", "BACKFILL_CHAPTER",
        "REWRITE_CHAPTER", "IMPORT_NOVEL",
    ]
    planned_total: int | None = Field(default=None, ge=1)
    current_chapters: tuple[int, ...] = ()
    chapters: tuple[PlannedChapter, ...] = ()


class IntentPlanner(Protocol):
    def plan(self, prompt: str, *, model: str | None = None) -> CreativePlan: ...


class LLMIntentPlanner:
    """One small model call; the result is never trusted for chapter arithmetic."""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def plan(self, prompt: str, *, model: str | None = None) -> CreativePlan:
        instruction = intent_prompt(prompt)
        answer = build_chat_model(self.gateway, model).invoke(instruction).content
        if not isinstance(answer, str) or not answer.strip():
            raise ModelInvocationError("Workflow intent planner returned no content")
        value = answer.strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
        try:
            return CreativePlan.model_validate(json.loads(value))
        except (ValueError, ValidationError) as exc:
            raise ModelInvocationError("Workflow intent planner returned invalid JSON") from exc


class ChapterEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    declared_total: int | None = None
    first_count: int | None = None
    listed: tuple[PlannedChapter, ...] = ()
    preamble: str = ""


def chapter_evidence(prompt: str) -> ChapterEvidence:
    totals = {ordinal(match.group(1)) for match in _TOTAL.finditer(prompt)}
    totals.update(ordinal(match.group(1)) for match in _REVERSE_TOTAL.finditer(prompt))
    if len(totals) > 1:
        raise DecisionRequired("全书章数出现多个不同数字，请明确总章数。")
    firsts = {ordinal(match.group(1)) for match in _FIRST.finditer(prompt)}
    firsts.update(ordinal(match.group(1)) for match in _CURRENT_COUNT.finditer(prompt))
    if len(firsts) > 1:
        raise DecisionRequired("本次创作章数出现多个不同数字，请明确本次范围。")
    matches = list(_HEADING.finditer(prompt))
    listed = tuple(
        PlannedChapter(
            number=ordinal(match.group(1)),
            brief=prompt[match.end(): matches[index + 1].start()
                         if index + 1 < len(matches) else len(prompt)].strip(),
        )
        for index, match in enumerate(matches)
    )
    numbers = [item.number for item in listed]
    if len(numbers) != len(set(numbers)):
        raise DecisionRequired("逐章要求中有重复章节编号，请明确每章要求。")
    return ChapterEvidence(
        declared_total=next(iter(totals), None),
        first_count=next(iter(firsts), None),
        listed=listed,
        preamble=prompt[:matches[0].start()].strip() if matches else prompt,
    )


def needs_semantic_plan(prompt: str) -> bool:
    """Keep explicit short workflow commands cheap; route broad novel requests semantically."""
    if re.search(r"小说|故事|全书|整部|每章|以后再写|后续再写|分批", prompt):
        return True
    if _FIRST.search(prompt) or len(list(_HEADING.finditer(prompt))) > 1:
        return True
    return False


def resolved_creation(
    prompt: str, plan: CreativePlan | None, *, existing_count: int
) -> tuple[tuple[PlannedChapter, ...], int | None, str]:
    """Resolve actual targets from text evidence, then check the model's candidate plan."""
    evidence = chapter_evidence(prompt)
    total = evidence.declared_total
    explicit = evidence.first_count
    listed = evidence.listed
    if explicit is not None:
        count = explicit
    elif total is not None:
        count = total - existing_count
    elif listed:
        count = len(listed)
    else:
        match = _SIMPLE_COUNT.search(prompt)
        count = ordinal(match.group(1)) if match else 1
    if count < 1 or count > 20:
        raise InvalidChange("Create between 1 and 20 Chapters in one Workflow")
    if total is not None and existing_count + count > total:
        raise DecisionRequired("本次创作范围超过全书计划章数，请明确计划或本次范围。")
    expected = tuple(range(existing_count + 1, existing_count + count + 1))
    listed_numbers = tuple(item.number for item in listed)
    if listed and listed_numbers != expected:
        permits_unlisted = (
            listed_numbers == expected[:len(listed_numbers)]
            and bool(re.search(r"(?:由你|由系统|你来).{0,6}(?:规划|构思|补全)|"
                               r"(?:规划|构思|补全).{0,6}(?:剩余|其余|后续|第)", prompt))
        )
        if not permits_unlisted:
            raise DecisionRequired(
                f"逐章要求列出 {listed_numbers}，本次预计创作 {expected}；"
                "请明确缺失、顺序或暂缓章节。"
            )
    if plan and plan.current_chapters and plan.current_chapters != expected:
        # Explicit textual evidence wins over model arithmetic; a real conflict is clarified.
        if explicit is None and evidence.declared_total is None and not listed:
            raise DecisionRequired("本次创作范围不明确，请指定要写的章节。")
    briefs = {item.number: item.brief for item in listed}
    if plan:
        for item in plan.chapters:
            if (
                item.number in expected
                and item.number not in briefs
                and item.brief
                and item.brief in prompt
            ):
                briefs[item.number] = item.brief
    targets = tuple(PlannedChapter(number=n, brief=briefs.get(n, "")) for n in expected)
    return targets, total, evidence.preamble
