import pytest

from novel_agent.agent.workflow_intent import (
    CreativePlan,
    PlannedChapter,
    chapter_evidence,
    resolved_creation,
)
from novel_agent.domain.errors import DecisionRequired


def test_count_and_inline_chapter_briefs_are_distinct():
    prompt = (
        "写一篇悬疑小说，每章内容如下。"
        "第一章：发现密信。第二章：追查失踪者。第三章：揭示骗局。"
    )
    evidence = chapter_evidence(prompt)
    assert [item.number for item in evidence.listed] == [1, 2, 3]
    assert [item.brief for item in evidence.listed] == [
        "发现密信。", "追查失踪者。", "揭示骗局。"
    ]
    targets, total, instruction = resolved_creation(
        prompt,
        CreativePlan(operation="MULTI_CHAPTER", current_chapters=(1, 2, 3)),
        existing_count=0,
    )
    assert [item.number for item in targets] == [1, 2, 3]
    assert total is None
    assert instruction == "写一篇悬疑小说，每章内容如下。"


def test_planned_five_but_write_first_three_now():
    prompt = "写一部悬疑小说，全书共五章，先编写前三章，后两章以后再写。"
    targets, total, _ = resolved_creation(
        prompt,
        CreativePlan(operation="MULTI_CHAPTER", planned_total=5, current_chapters=(1, 2, 3)),
        existing_count=0,
    )
    assert total == 5
    assert [item.number for item in targets] == [1, 2, 3]


def test_reverse_total_form_with_current_batch():
    targets, total, _ = resolved_creation(
        "编写5章节的小说，先写前三章，后两章以后再写。",
        CreativePlan(operation="MULTI_CHAPTER"),
        existing_count=0,
    )
    assert total == 5
    assert [item.number for item in targets] == [1, 2, 3]


def test_explicit_total_with_missing_per_chapter_brief_requires_choice():
    prompt = "小说有4个章节。每章内容如下。第一章：甲。第二章：乙。第三章：丙。"
    with pytest.raises(DecisionRequired, match="逐章要求"):
        resolved_creation(
            prompt, CreativePlan(operation="MULTI_CHAPTER"), existing_count=0
        )


def test_model_miscounts_does_not_override_textual_evidence():
    targets, total, _ = resolved_creation(
        "写一篇悬疑小说，小说有4个章节。",
        CreativePlan(operation="CREATE_CHAPTER", planned_total=3, current_chapters=(1,)),
        existing_count=0,
    )
    assert total == 4
    assert [item.number for item in targets] == [1, 2, 3, 4]


def test_model_cannot_invent_per_chapter_brief():
    targets, _, _ = resolved_creation(
        "写一篇小说，小说有两章。",
        CreativePlan(
            operation="MULTI_CHAPTER",
            chapters=(PlannedChapter(number=1, brief="神秘人在钟楼出现"),),
        ),
        existing_count=0,
    )
    assert targets[0].brief == ""


def test_duplicate_numbered_briefs_require_choice():
    with pytest.raises(DecisionRequired, match="重复"):
        chapter_evidence("第一章：甲。第一章：乙。")
