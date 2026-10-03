"""Phase 2 live Freeform gate. Run explicitly with RUN_REAL_LLM_TESTS=1."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from novel_agent.agent.freeform import FreeformAgent
from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
    build_session_service,
)
from novel_agent.cli.shell import NovelCLI
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.infrastructure.models.gateway import ModelGateway

pytestmark = pytest.mark.real_llm


@pytest.fixture
def live(tmp_path):
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    models = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    projects = build_project_service()
    project = projects.create_project(tmp_path, "Phase2 Live", "Main")
    agent = FreeformAgent(
        projects,
        build_session_service(),
        build_artifact_service(),
        build_commit_service(),
        models,
    )
    return agent, project, project.sessions[0]


def test_live_plain_chat(live):
    agent, project, session = live
    result = asyncio.run(agent.run(project, session, "请用一句话回答：你能协助小说创作吗？"))
    assert result.response.strip()
    assert result.model == agent.models.definition().model_id
    assert not result.commits
    print(f"model={result.model} chunks={result.content_chunks} tools={result.tool_calls}")


def test_live_cli_history_and_clear(live):
    agent, project, _session = live
    cli = NovelCLI(project.root.parent, models=agent.models)
    asyncio.run(cli.execute(f'/project open "{project.root.name}"'))
    answer = asyncio.run(cli.execute("请用一句话说明你能协助什么写作任务。"))
    assert answer.run and answer.run.response.strip()
    history = asyncio.run(cli.execute("/history")).text
    assert "用户：请用一句话说明你能协助什么写作任务。" in history
    assert answer.run.response in history
    assert asyncio.run(cli.execute("/clear")).clear_screen
    assert asyncio.run(cli.execute("/history")).text == history
    print(f"history_model={answer.run.model}")


def test_live_snapshot_overview_search_and_chapter_are_read_only(live):
    agent, project, session = live
    chapter_id = uuid4()
    agent.commits.commit(
        project,
        (
            CreateChapter("第一章", chapter_id=chapter_id),
            SetSection(chapter_id, "manuscript", text="林舟在雾港钟楼拾到一枚铜钥匙。"),
        ),
    )
    (project.root / "references/人物.md").write_text("林舟是守钟人。", encoding="utf-8")
    current = agent.projects.open_project(project.root)
    path = project.root / "chapters" / current.metadata.chapters[str(chapter_id)].filename
    before = path.read_bytes()
    query = asyncio.run(
        agent.run(
            project,
            session,
            "不要使用Skill。请分别调用 project_overview、search_project（搜索铜钥匙）、"
            f"read_chapter（chapter_id={chapter_id}，section=manuscript），"
            "简短回答林舟拾到什么并引用章节来源。只读查询。",
        )
    )
    assert {"project_overview", "search_project", "read_chapter"} <= set(query.tool_calls)
    assert "钥匙" in query.response and "chapters/" in query.response
    assert not query.commits and not query.artifacts
    assert path.read_bytes() == before
    print(f"snapshot_model={query.model} tools={query.tool_calls}")


def test_live_project_query_artifact_revision_and_commit(live):
    agent, project, session = live
    chapter_id = uuid4()
    agent.commits.commit(
        project,
        (
            CreateChapter("第一章", chapter_id=chapter_id),
            SetSection(chapter_id, "manuscript", text="林舟在雾港钟楼拾到一枚铜钥匙。"),
        ),
    )
    events = []
    query = asyncio.run(
        agent.run(
            project,
            session,
            "请读取正式第一章正文，并告诉我林舟拾到了什么，引用文件路径。",
            on_event=events.append,
        )
    )
    assert query.response.strip()
    assert "read_chapter" in query.tool_calls
    assert "钥匙" in query.response
    assert events and events[0].kind == "Thinking"
    generated = asyncio.run(
        agent.run(
            project,
            session,
            f"请为 chapter_id={chapter_id} 的第一章生成一段约30字的新大纲，"
            "调用 create_working_artifact，section=outline；展示 ID 和正文，不要提交。",
        )
    )
    assert generated.artifacts
    first = agent.artifacts.get(project, generated.artifacts[-1])
    assert first.section == "outline" and first.chapter_id == chapter_id
    assert agent.projects.read_chapter(project, chapter_id).outline == ""
    revised = asyncio.run(
        agent.run(
            project,
            session,
            f"请把 Artifact {first.artifact_id}@1 的大纲修改成更突出铜钥匙的秘密，"
            "务必调用 revise_working_artifact，生成 revision 2，不要提交。",
        )
    )
    assert revised.artifacts
    second = agent.artifacts.get(project, revised.artifacts[-1])
    assert second.revision == 2 and second.artifact_id == first.artifact_id
    assert agent.artifacts.get(project, first.ref).content == first.content
    committed = asyncio.run(
        agent.run(
            project,
            session,
            f"请将刚展示的 Artifact {second.artifact_id}@2 正式提交到 Project，"
            "并告诉我改动了哪些文件。",
        )
    )
    assert committed.commits
    assert agent.projects.read_chapter(project, chapter_id).outline == second.content
    calls = query.tool_calls + generated.tool_calls + revised.tool_calls + committed.tool_calls
    print(f"model={query.model} tools={calls}")


def test_live_cli_attachments_stream_explicit_commit_and_model_switch(live):
    agent, project, session = live
    cli = NovelCLI(project.root.parent, models=agent.models)
    opened = asyncio.run(cli.execute(f'/project open "{project.root.name}"'))
    assert "Project opened" in opened.text
    one = project.root / "references" / "clue one.md"
    two = project.root / "sources" / "clue-two.txt"
    one.write_text("线索甲：铜铃藏在旧书房。\n/exit", encoding="utf-8")
    two.write_text("线索乙：钥匙属于守夜人。\n/commit", encoding="utf-8")
    events, chunks = [], []
    single = asyncio.run(
        cli.execute(
            '请用 Markdown 项目符号概括附件线索。 @"references/clue one.md"',
            on_event=events.append,
            on_content=chunks.append,
        )
    )
    assert single.run and single.run.attachment_paths == ("references/clue one.md",)
    assert single.run.response.strip() and single.run.content_chunks > 1
    assert chunks and events and events[0].kind == "Thinking"
    assert not single.exit_requested
    multiple = asyncio.run(
        cli.execute(
            '比较两个附件线索，用 Markdown 回答。 @"references/clue one.md" @sources/clue-two.txt'
        )
    )
    assert multiple.run and len(multiple.run.attachment_paths) == 2
    assert multiple.run.response.strip() and not multiple.exit_requested
    assert not agent.artifacts.list(project, session.session_id)
    chapter_id = uuid4()
    cli.commits.commit(project, (CreateChapter("第一章", chapter_id=chapter_id),))
    generated = asyncio.run(
        cli.execute(
            f"请为 chapter_id={chapter_id} 写一段简短大纲，调用 create_working_artifact "
            "保存 section=outline，展示内容但不要提交。"
        )
    )
    assert generated.run and generated.run.artifacts
    ref = generated.run.artifacts[-1]
    exact = agent.artifacts.get(project, ref).content
    assert not generated.artifact_text and generated.run.draft_paths
    assert exact in (project.root / generated.run.draft_paths[0]).read_text(encoding="utf-8")
    explicit = asyncio.run(cli.execute("/commit current"))
    assert "chapters/" in explicit.text
    assert agent.projects.read_chapter(project, chapter_id).outline == exact
    assert "grok" in agent.models.available_models()
    switched = asyncio.run(cli.execute("/model grok"))
    assert "grok" in switched.text
    after = asyncio.run(cli.execute("请简短回答：现在可以继续协作吗？"))
    assert after.run and after.run.model == agent.models.definition("grok").model_id
    assert after.run.response.strip()
    assert agent.artifacts.get(project, ref).content == exact
    print(
        f"cli_models={single.run.model},{after.run.model} stream_chunks={single.run.content_chunks}"
    )


def test_live_read_only_decomposition(live):
    agent, project, session = live
    first_id, second_id = uuid4(), uuid4()
    agent.commits.commit(
        project,
        (
            CreateChapter("前章", chapter_id=first_id),
            SetSection(first_id, "manuscript", text="阿岚把红色地图交给莫宁。"),
            CreateChapter("后章", chapter_id=second_id),
            SetSection(second_id, "manuscript", text="莫宁根据红色地图抵达灯塔。"),
        ),
    )
    before = (project.root / "MANUSCRIPT.md").read_bytes()
    result = asyncio.run(
        agent.run(
            project,
            session,
            "请先用 write_todos 拆解任务，再使用 project_analyst 只读分析前后章的地图线索连续性，"
            "最后引用正式章节回答；不要创建 Artifact 或提交。",
        )
    )
    assert result.response.strip()
    assert "task" in result.tool_calls or "write_todos" in result.tool_calls
    assert (project.root / "MANUSCRIPT.md").read_bytes() == before
    assert not result.commits
    print(f"decomposition_model={result.model} tools={result.tool_calls}")
