"""Real model routing gate; RUN_REAL_LLM_TESTS=1."""

import asyncio
import os
from pathlib import Path

import pytest

from novel_agent.agent.workflow_intent import LLMIntentPlanner, resolved_creation
from novel_agent.cli.shell import NovelCLI
from novel_agent.infrastructure.models.gateway import ModelGateway

pytestmark = pytest.mark.real_llm


@pytest.fixture
def planner():
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    gateway = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    print(f"model={gateway.definition().model_id}")
    return LLMIntentPlanner(gateway)


@pytest.mark.parametrize(
    ("prompt", "expected", "total"),
    [
        ("写一篇悬疑小说，小说有4个章节，每章约80字。", (1, 2, 3, 4), 4),
        (
            "写一篇悬疑小说，每章内容如下。"
            "第一章：发现密信。第二章：追查失踪者。第三章：揭示骗局。",
            (1, 2, 3), None,
        ),
        (
            "写一部悬疑小说，全书共五章，先编写前三章，后两章以后再写。",
            (1, 2, 3), 5,
        ),
    ],
)
def test_real_semantic_plan_and_deterministic_targets(planner, prompt, expected, total):
    plan = planner.plan(prompt)
    assert plan.operation == "MULTI_CHAPTER"
    targets, planned_total, _ = resolved_creation(prompt, plan, existing_count=0)
    assert tuple(item.number for item in targets) == expected
    assert planned_total == total


def test_real_model_routes_partial_five_chapter_novel_into_three_chapter_workflow(
    planner, tmp_path
):
    class Generator:
        def __init__(self):
            self.calls = []

        def generate(self, stage, instruction, context, *, feedback="", model=None):
            self.calls.append((stage, context))
            return f"{stage} 候选稿"

    cli = NovelCLI(tmp_path / "workspace", models=planner.gateway)
    cli.agent.intent_planner = planner
    cli.agent.generator = Generator()
    asyncio.run(cli.execute("/project create Story"))
    asyncio.run(cli.execute("/mode workflow"))
    result = asyncio.run(
        cli.execute("写一部悬疑小说，全书共五章，先编写前三章，后两章以后再写。")
    )
    assert result.run.workflow_snapshot.kind == "MULTI_CHAPTER"
    assert len([call for call in cli.agent.generator.calls if call[0] == "summary"]) == 3
    assert not cli.projects.list_chapters(cli._selected()[0])


def test_real_model_numbered_briefs_reach_multi_chapter_summary_gate(planner, tmp_path):
    cli = NovelCLI(tmp_path / "workspace", models=planner.gateway)
    asyncio.run(cli.execute("/project create NumberedStory"))
    asyncio.run(cli.execute("/mode workflow"))
    result = asyncio.run(
        cli.execute(
            "写一篇简短悬疑小说，每章内容如下。"
            "第一章：发现铜钥匙。第二章：调查旧钟楼。第三章：找到失踪者。"
        )
    )
    snapshot = result.run.workflow_snapshot
    assert snapshot.kind == "MULTI_CHAPTER"
    assert snapshot.gate.stage == "SUMMARIES"
    assert len(snapshot.gate.artifact_refs) == 3
    project, _ = cli._selected()
    assert all(
        cli.artifacts.get(project, ref).content.strip() for ref in snapshot.gate.artifact_refs
    )
    assert not cli.projects.list_chapters(project)
