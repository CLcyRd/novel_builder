"""Contracts captured from the pre-refactor code, plus orchestration failure boundaries."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.checkpoint.sqlite import SqliteSaver

from novel_agent.agent import freeform
from novel_agent.agent.freeform_inputs import FreeformInputBuilder
from novel_agent.agent.freeform_tools import FreeformToolFactory
from novel_agent.agent.freeform_types import FreeformRunScope, RunResult
from novel_agent.agent.workflow_intent import CreativePlan
from novel_agent.agent.workflow_request import WorkflowRequestBuilder, WorkflowTargetResolver
from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_create_chapter_workflow,
    build_multi_chapter_workflow,
    build_project_service,
    build_session_service,
)
from novel_agent.domain.change import CreateChapter, SetSection, SplitChapter
from novel_agent.domain.chapter import parse_chapter
from novel_agent.domain.errors import DecisionRequired, InvalidChange
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.services.commit_changes import ChapterChangeReducer
from novel_agent.services.commit_plan import CommitWritePlanner
from novel_agent.workflow.batch_state import _BatchState
from novel_agent.workflow.single_state import _State

CONTRACT = json.loads(
    (Path(__file__).parents[1] / "fixtures/responsibility_contracts.json").read_text(
        encoding="utf-8"
    )
)


class Writer:
    def generate(self, stage, instruction, context, *, feedback="", model=None):
        return f"{stage} 铜钥匙"


@pytest.fixture
def setup(tmp_path):
    projects = build_project_service()
    project = projects.create_project(tmp_path, "Tools", "Main")
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake", "https://example.invalid", "none")}, "fake"
    )
    agent = freeform.FreeformAgent(
        projects, build_session_service(), build_artifact_service(), build_commit_service(), models
    )
    return agent, project, project.sessions[0]


@pytest.mark.parametrize("family", ["single", "batch"])
@pytest.mark.parametrize("version", [1, 2])
def test_original_graph_topology_and_state_fields(setup, family, version):
    _, project, _ = setup
    workflow = (
        build_create_chapter_workflow(Writer())
        if family == "single"
        else build_multi_chapter_workflow(Writer())
    )
    with SqliteSaver.from_conn_string(":memory:") as saver:
        graph = workflow._build(project, saver, workflow_version=version).get_graph()
        actual = {
            "nodes": sorted(graph.nodes),
            "edges": sorted(
                [[edge.source, edge.target, edge.data, edge.conditional] for edge in graph.edges],
                key=str,
            ),
        }
    assert actual == CONTRACT[f"{family}_v{version}"]
    state = _State if family == "single" else _BatchState
    assert sorted(state.__annotations__) == CONTRACT[f"{family}_state_fields"]


def capture_graph(monkeypatch, graph):
    captured = {}

    def build(**kwargs):
        captured.update(kwargs)
        return graph

    monkeypatch.setattr(freeform, "build_chat_model", lambda *args: object())
    monkeypatch.setattr(freeform, "create_deep_agent", build)
    return captured


def test_original_tool_schemas_events_stream_and_session_isolation(setup, monkeypatch):
    agent, project, session = setup

    class Graph:
        async def astream(self, values, **kwargs):
            yield "messages", (AIMessageChunk(content="你好"), {"langgraph_node": "model"})
            yield "updates", {"model": {"messages": [AIMessage(content="你好")]}}

    captured = capture_graph(monkeypatch, Graph())
    chunks, events = [], []
    result = asyncio.run(
        agent.run(
            project,
            session,
            "不要使用Skill。你好。",
            on_content=chunks.append,
            on_event=events.append,
        )
    )
    assert [
        {
            "name": tool.name,
            "description": tool.description,
            "args": tool.args_schema.model_json_schema(),
        }
        for tool in captured["tools"]
    ] == CONTRACT["tools"]
    assert [tool.name for tool in captured["subagents"][0]["tools"]] == [
        "project_overview",
        "read_chapter",
        "search_project",
        "read_project_file",
    ]
    assert [[event.kind, event.detail] for event in events] == CONTRACT["chat_events"]
    assert events == result.events and chunks == ["你好"]
    assert agent._state(project, session)[1].history == [
        {"role": "user", "content": "不要使用Skill。你好。"},
        {"role": "assistant", "content": "你好"},
    ]
    other = agent.sessions.create(project, "Other")
    assert agent._state(project, other)[1].history == []
    assert not result.artifacts and not result.commits


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_failed_or_cancelled_model_stream_does_not_record_success(setup, monkeypatch, failure):
    agent, project, session = setup

    class Graph:
        async def astream(self, values, **kwargs):
            yield "messages", (AIMessageChunk(content="部分内容"), {"langgraph_node": "model"})
            raise failure("stopped")

    capture_graph(monkeypatch, Graph())
    events = []
    with pytest.raises(failure):
        asyncio.run(agent.run(project, session, "不要使用Skill。你好。", on_event=events.append))
    assert [event.kind for event in events] == [
        "Thinking",
        "Cancelled" if failure is asyncio.CancelledError else "Failed",
    ]
    assert agent._state(project, session)[1].history == []
    assert not agent.artifacts.list(project, session.session_id)
    assert not agent.projects.list_chapters(project)


@pytest.mark.parametrize("complete_retry", [False, True])
def test_completion_retry_is_limited_and_checks_real_tool_result(
    setup, monkeypatch, complete_retry
):
    agent, project, session = setup
    captured = {}
    inputs = []

    class Graph:
        async def astream(self, values, **kwargs):
            inputs.append(values["messages"])
            if len(inputs) == 2 and complete_retry:
                tool = next(t for t in captured["tools"] if t.name == "create_working_artifact")
                tool.invoke({"content": "连续性分析", "section": "analysis", "chapter": "project"})
            yield "messages", (AIMessageChunk(content="候选回答"), {"langgraph_node": "model"})
            yield "updates", {"model": {"messages": [AIMessage(content="已保存分析")]}}

    captured = capture_graph(monkeypatch, Graph())
    events, chunks = [], []
    run = agent.run(
        project,
        session,
        "不要使用Skill。调用 create_working_artifact 保存分析。",
        on_event=events.append,
        on_content=chunks.append,
    )
    if complete_retry:
        result = asyncio.run(run)
        assert len(result.artifacts) == 1 and not result.commits
        assert chunks == ["候选回答"]
    else:
        with pytest.raises(InvalidChange, match="did not complete requested create"):
            asyncio.run(run)
        assert not chunks and not agent._state(project, session)[1].history
    assert len(inputs) == 2
    assert len(inputs[1]) == len(inputs[0]) + 2
    assert [event.kind for event in events].count("Retrying") == 1
    assert events[-1].kind == ("Completed" if complete_retry else "Failed")


def test_mutating_tools_check_authorization_during_execution(setup):
    agent, project, session = setup
    store, state = agent._state(project, session)
    scope = FreeformRunScope(
        agent, project, session, store, state, agent.models.definition(), RunResult("", "fake")
    )
    FreeformInputBuilder.prepare(scope, "只读查询", ())
    tools = {tool.name: tool for tool in FreeformToolFactory(scope).build()}
    with pytest.raises(InvalidChange, match="no explicit Commit intent"):
        tools["commit_working_artifact"].invoke({"artifact_id": str(uuid4()), "revision": 1})
    assert "拒绝删除" in tools["delete_formal_chapter"].invoke({"chapter_id": str(uuid4())})
    assert not agent.projects.list_chapters(project) and not scope.result.commits


def test_reducer_and_write_plan_are_pure_and_do_not_mutate_input(setup, monkeypatch):
    agent, project, _ = setup
    chapter_id, split_id = uuid4(), uuid4()
    agent.commits.commit(
        project,
        (
            CreateChapter("第一章", chapter_id=chapter_id),
            SetSection(chapter_id, "manuscript", text="起点。转折。"),
        ),
    )
    snapshot = agent.projects.read_snapshot(project)
    metadata_before = snapshot.project.metadata.model_dump(mode="json")
    chapter_before = snapshot.chapter(chapter_id)

    def unexpected_io(*args, **kwargs):
        raise AssertionError("In-memory planning must not access the filesystem")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", unexpected_io)
        patch.setattr(Path, "read_bytes", unexpected_io)
        patch.setattr(Path, "write_bytes", unexpected_io)
        reducer = ChapterChangeReducer(snapshot)
        reducer.apply(SplitChapter(chapter_id, "转折。", "第二章"), new_id=split_id)
        plan = CommitWritePlanner.build(snapshot, reducer)
        assert plan == CommitWritePlanner.build(snapshot, reducer)
    assert parse_chapter(plan.writes["chapters/0001.md"].decode()).manuscript == "起点。"
    assert parse_chapter(plan.writes["chapters/0002.md"].decode()).manuscript == "转折。"
    assert snapshot.project.metadata.model_dump(mode="json") == metadata_before
    assert snapshot.chapter(chapter_id) == chapter_before
    assert agent.projects.read_chapter(project, chapter_id).manuscript == "起点。转折。"
    assert not (project.root / "chapters/0002.md").exists()


def test_ambiguous_targets_create_no_brief_and_long_brief_is_constructed_once(setup):
    agent, project, session = setup
    resolver = WorkflowTargetResolver(agent.projects)
    builder = WorkflowRequestBuilder(agent.artifacts)
    plan = CreativePlan(operation="MULTI_CHAPTER")
    with pytest.raises(DecisionRequired):
        resolver.resolve(project, "小说共四章，写小说。第一章：甲。第二章：乙。", (), plan)
    assert not agent.artifacts.list(project, session.session_id)
    brief = "铜钥匙线索" * 500
    target = resolver.resolve(project, f"写小说。第一章：{brief}第二章：结尾。", (), plan)
    assert not agent.artifacts.list(project, session.session_id)
    request = builder.build(project, session, target, ())
    assert request.kind == "MULTI_CHAPTER" and len(request.chapters) == 2
    first, second = request.chapters
    assert first.brief == "" and first.brief_ref
    assert second.brief_ref is None and second.brief == "结尾。"
    revision = agent.artifacts.get(project, first.brief_ref)
    assert revision.content == brief and revision.chapter_id == first.chapter_id
    assert len(agent.artifacts.list(project, session.session_id)) == 1
