import asyncio
from uuid import uuid4

from novel_agent.agent.freeform import FreeformAgent, RunResult
from novel_agent.agent.workflow_intent import CreativePlan
from novel_agent.cli.shell import NovelCLI
from novel_agent.domain.change import CreateChapter
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway


class Planner:
    def __init__(self, plan):
        self.plan_value = plan
        self.calls = []

    def plan(self, prompt, *, model=None):
        self.calls.append((prompt, model))
        return self.plan_value


class Generator:
    def __init__(self):
        self.calls = []

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, instruction, context))
        return f"{stage} 候选稿"


def setup_cli(tmp_path, plan):
    models = ModelGateway(
        {"local": ModelDefinition("local", "fake", "https://example.invalid/v1", "none")},
        "local",
    )
    cli = NovelCLI(tmp_path / "workspace", models=models)
    cli.agent.intent_planner = Planner(plan)
    cli.agent.generator = Generator()
    asyncio.run(cli.execute("/project create Story"))
    asyncio.run(cli.execute("/mode workflow"))
    return cli


def test_total_four_enters_multi_chapter(tmp_path):
    cli = setup_cli(tmp_path, CreativePlan(operation="CREATE_CHAPTER", current_chapters=(1,)))
    result = asyncio.run(cli.execute("写一篇悬疑小说，小说有4个章节。"))
    assert result.run.workflow_snapshot.kind == "MULTI_CHAPTER"
    assert len([call for call in cli.agent.generator.calls if call[0] == "summary"]) == 4
    assert cli.agent.intent_planner.calls[0][1] is None  # use configured default model
    project, _ = cli._selected()
    assert not cli.projects.list_chapters(project)


def test_explicit_continue_outweighs_wrong_semantic_candidate(tmp_path):
    cli = setup_cli(tmp_path, CreativePlan(operation="CREATE_CHAPTER"))
    cli.commits.commit(cli._selected()[0], (CreateChapter("第一章", chapter_id=uuid4()),))
    started = asyncio.run(cli.execute("续写下一章，保持悬疑小说风格。"))
    assert started.run.workflow_snapshot.kind == "CONTINUE_CHAPTER"


def test_numbered_briefs_feed_matching_chapter_only(tmp_path):
    cli = setup_cli(tmp_path, CreativePlan(operation="MULTI_CHAPTER"))
    result = asyncio.run(
        cli.execute(
            "写一篇悬疑小说，每章内容如下。"
            "第一章：铜钥匙。第二章：旧钟楼。第三章：失踪信件。"
        )
    )
    assert result.run.workflow_snapshot.kind == "MULTI_CHAPTER"
    summaries = [call for call in cli.agent.generator.calls if call[0] == "summary"]
    assert len(summaries) == 3
    assert "铜钥匙" in summaries[0][2] and "旧钟楼" not in summaries[0][2]
    assert "旧钟楼" in summaries[1][2] and "失踪信件" not in summaries[1][2]
    assert "失踪信件" in summaries[2][2]


def test_five_planned_three_now_and_two_deferred(tmp_path):
    cli = setup_cli(
        tmp_path,
        CreativePlan(operation="MULTI_CHAPTER", planned_total=5, current_chapters=(1, 2, 3)),
    )
    result = asyncio.run(
        cli.execute("写一部悬疑小说，全书共五章，先编写前三章，后两章以后再写。")
    )
    assert result.run.workflow_snapshot.kind == "MULTI_CHAPTER"
    assert len([call for call in cli.agent.generator.calls if call[0] == "summary"]) == 3
    assert all("全书计划：5 章；本次仅创作 3 章" in call[2]
               for call in cli.agent.generator.calls)
    project, session = cli._selected()
    assert not cli.projects.list_chapters(project)
    assert not cli.agent.load_active(project, session).result


def test_conflicting_four_total_three_listed_does_not_start(tmp_path):
    cli = setup_cli(tmp_path, CreativePlan(operation="MULTI_CHAPTER"))
    decision = asyncio.run(
        cli.execute("小说有4个章节，写一篇悬疑小说。第一章：甲。第二章：乙。第三章：丙。")
    )
    assert "逐章要求" in decision.text
    assert cli.prompt_label() == "decision[reply]> "
    project, session = cli._selected()
    assert cli.agent.load_active(project, session) is None
    assert not cli.projects.list_chapters(project)
    reopened = NovelCLI(cli.workspace, models=cli.models)
    reopened.agent.intent_planner = cli.agent.intent_planner
    reopened.agent.generator = cli.agent.generator
    asyncio.run(reopened.execute("/project open Story"))
    assert reopened.prompt_label() == "decision[reply]> "
    resolved = asyncio.run(reopened.execute("第四章由你规划"))
    assert resolved.run.workflow_snapshot.kind == "MULTI_CHAPTER"
    assert len([call for call in reopened.agent.generator.calls if call[0] == "summary"]) == 4
    assert reopened.agent.load_routing_decision(*reopened._selected()) is None


def test_pending_routing_decision_does_not_consume_ordinary_chat(tmp_path, monkeypatch):
    cli = setup_cli(tmp_path, CreativePlan(operation="MULTI_CHAPTER"))
    asyncio.run(
        cli.execute("小说有4个章节，写一篇悬疑小说。第一章：甲。第二章：乙。第三章：丙。")
    )

    async def fake_chat(self, project, session, prompt, attachments=(), **kwargs):
        return RunResult(response="章节规划可以稍后再决定。", model="fake")

    monkeypatch.setattr(FreeformAgent, "run", fake_chat)
    answer = asyncio.run(cli.execute("为什么需要补充第四章？"))
    assert answer.text == "章节规划可以稍后再决定。"
    assert cli.agent.load_routing_decision(*cli._selected()) is not None
    assert cli.agent.load_active(*cli._selected()) is None
    cancelled = asyncio.run(cli.execute("取消"))
    assert "已取消" in cancelled.text
    assert cli.agent.load_routing_decision(*cli._selected()) is None


def test_long_chapter_brief_is_stored_as_artifact_reference(tmp_path):
    cli = setup_cli(tmp_path, CreativePlan(operation="MULTI_CHAPTER"))
    long_brief = "线索" * 1100
    started = asyncio.run(
        cli.execute(f"写一篇悬疑小说。第一章：{long_brief}第二章：收束悬念。")
    )
    assert started.run.workflow_snapshot.kind == "MULTI_CHAPTER"
    project, session = cli._selected()
    briefs = [
        index
        for index in cli.artifacts.list(project, session.session_id)
        if cli.artifacts.get_by_id(project, index.artifact_id, 1).artifact_type.value
        == "TASK_BRIEF"
    ]
    assert len(briefs) == 1
    assert long_brief[:100] in cli.agent.generator.calls[0][2]
