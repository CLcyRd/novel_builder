import asyncio
from uuid import uuid4

import pytest

from novel_agent.agent.freeform import RunResult
from novel_agent.cli.shell import NovelCLI
from novel_agent.config.setting import Settings
from novel_agent.domain.artifact import ArtifactType
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.runtime.history import SessionHistoryStore
from novel_agent.runtime.state import SessionState, SessionStateStore


def _cli(tmp_path, settings=None):
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake-model", "https://example.invalid/v1", "none")},
        "fake",
    )
    cli = NovelCLI(tmp_path / "workspace", models=models, settings=settings or Settings())
    asyncio.run(cli.execute("/project create History"))
    return cli


def _run(cli, line):
    return asyncio.run(cli.execute(line))


def test_history_persists_successful_chat_and_artifact_refs_across_reopen(tmp_path):
    cli = _cli(tmp_path)
    project, session = cli._selected()
    artifact = cli.artifacts.create(
        project, session.session_id, ArtifactType.OUTLINE, "PROJECT", "完整大纲工作稿"
    )

    async def fake_run(_project, _session, prompt, attachments, **_kwargs):
        assert prompt == "请写大纲" and not attachments
        return RunResult(
            response="已生成大纲。",
            model="fake-model",
            display_refs=[artifact.ref],
        )

    cli.agent.run = fake_run
    _run(cli, "请写大纲")
    history = _run(cli, "/history").text
    assert "用户：请写大纲" in history
    assert "助手：已生成大纲。" in history
    assert f"工作稿：{artifact.artifact_id}@1" in history
    assert "完整大纲工作稿" not in history
    assert len(SessionHistoryStore(project.root).recent(session.session_id)) == 2

    reopened = NovelCLI(cli.workspace, models=cli.models)
    _run(reopened, "/project open History")
    assert _run(reopened, "/history").text == history


def test_history_is_session_local_and_commands_do_not_call_agent(tmp_path):
    cli = _cli(tmp_path)
    project, first = cli._selected()
    assert "暂无" in _run(cli, "/history").text

    async def fake_run(*_args, **_kwargs):
        return RunResult(response="第一会话回答", model="fake-model")

    cli.agent.run = fake_run
    _run(cli, "第一会话提问")
    _run(cli, "/session create 第二会话")
    assert "暂无" in _run(cli, "/history").text
    assert not SessionHistoryStore(project.root).recent(cli.session.session_id)
    _run(cli, "/session open Main")
    assert cli.session.session_id == first.session_id
    assert "第一会话提问" in _run(cli, "/history").text
    assert len(SessionHistoryStore(project.root).recent(first.session_id)) == 2


def test_history_limits_and_clear_preserve_everything(tmp_path):
    settings = Settings(
        session_history_max_messages=4,
        session_history_max_chars=8,
        session_history_display_messages=4,
    )
    cli = _cli(tmp_path, settings)
    project, session = cli._selected()

    async def fake_run(*_args, **_kwargs):
        return RunResult(response="助手回答很长很长", model="fake-model")

    cli.agent.run = fake_run
    for index in range(3):
        _run(cli, f"第{index}轮用户提问很长")
    before = SessionHistoryStore(project.root, settings).recent(session.session_id)
    assert len(before) == 4
    assert before[0].content.startswith("第1轮")
    assert all(len(message.content) <= 8 for message in before)
    assert "已截断" in _run(cli, "/history").text
    state_store = SessionStateStore(project.root)
    checkpoint_before = state_store.load(session.session_id)
    checkpoint_before.active_workflow_id = uuid4()
    checkpoint_before.active_workflow_kind = "CREATE_CHAPTER"
    checkpoint_before.pending_routing_decision = {"reason": "保留待决策状态"}
    state_store.save(session.session_id, checkpoint_before)
    clear = _run(cli, "/clear")
    assert clear.clear_screen and not clear.text and clear.run is None
    assert SessionHistoryStore(project.root, settings).recent(session.session_id) == before
    assert state_store.load(session.session_id) == checkpoint_before
    assert "/history" in _run(cli, "/help").text
    assert "/clear" in _run(cli, "/help").text


def test_history_records_workflow_reply_but_not_failed_run(tmp_path):
    cli = _cli(tmp_path)
    project, session = cli._selected()

    async def fake_run(_project, _session, prompt, _attachments, **_kwargs):
        if prompt == "错误":
            raise RuntimeError("无法完成")
        return RunResult(response="Workflow 等待确认。", model="fake-model")

    cli.agent.run = fake_run
    _run(cli, "写第一章")
    _run(cli, "y")
    with pytest.raises(RuntimeError, match="无法完成"):
        _run(cli, "错误")
    messages = SessionHistoryStore(project.root).recent(session.session_id)
    assert [message.content for message in messages] == [
        "写第一章", "Workflow 等待确认。", "y", "Workflow 等待确认。"
    ]


def test_context_history_keeps_its_separate_small_limit():
    state = SessionState()
    for index in range(20):
        state.remember("user", f"{index}:" + "字" * 1000)
    assert len(state.history) == 12
    assert state.history[0]["content"].startswith("8:")
    assert all(len(message["content"]) == 600 for message in state.history)


def test_history_settings_reject_invalid_limits():
    with pytest.raises(ValueError, match="positive"):
        Settings(session_history_max_chars=0)
    with pytest.raises(ValueError, match="display limit"):
        Settings(session_history_max_messages=2, session_history_display_messages=3)


def test_clear_works_before_project_selection(tmp_path):
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake-model", "https://example.invalid/v1", "none")},
        "fake",
    )
    cli = NovelCLI(tmp_path / "workspace", models=models)
    assert _run(cli, "/clear").clear_screen
    assert cli.project is None and cli.session is None
