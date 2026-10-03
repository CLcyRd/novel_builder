import asyncio
from uuid import uuid4

from novel_agent.agent.freeform import FreeformAgent, RunResult
from novel_agent.cli.shell import NovelCLI
from novel_agent.domain.change import CreateChapter, SetProjectText, SetSection
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.runtime.state import SessionStateStore


class Generator:
    def __init__(self):
        self.calls = []

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, context, feedback, model))
        return f"{stage} 内容 {feedback}".strip()


def cli_at(tmp_path):
    models = ModelGateway(
        {
            "first": ModelDefinition("first", "fake-1", "https://example.invalid/v1", "none"),
            "second": ModelDefinition("second", "fake-2", "https://example.invalid/v1", "none"),
        },
        "first",
    )
    cli = NovelCLI(tmp_path / "workspace", models=models)
    cli.agent.generator = Generator()
    asyncio.run(cli.execute("/project create Story"))
    return cli


def run(cli, line):
    return asyncio.run(cli.execute(line))


def test_cli_workflow_waiting_chat_model_switch_and_reopen(tmp_path, monkeypatch):
    cli = cli_at(tmp_path)
    project, session = cli._selected()
    first_id = uuid4()
    cli.commits.commit(
        project,
        (
            CreateChapter("第一章", chapter_id=first_id),
            SetSection(first_id, "manuscript", text="前章铜钥匙"),
        ),
    )
    run(cli, "/mode workflow")
    started = run(cli, "续写下一章")
    assert started.run.workflow_snapshot.gate.stage == "SUMMARY"
    gate_id = started.run.workflow_snapshot.gate.gate_id
    assert cli.prompt_label() == "approve[y/n/e]> "
    assert "前章铜钥匙" in cli.agent.generator.calls[0][1]

    async def fake_chat(self, project, session, prompt, attachments=(), **kwargs):
        return RunResult(response="正式第一章有铜钥匙", model="fake-1")

    monkeypatch.setattr(FreeformAgent, "run", fake_chat)
    chat = run(cli, "第一章有什么线索？")
    assert "铜钥匙" in chat.text
    assert cli.agent.load_active(project, session).gate.gate_id == gate_id

    reopened = NovelCLI(cli.workspace, models=cli.models)
    reopened.agent.generator = cli.agent.generator
    run(reopened, "/project open Story")
    assert reopened.agent.load_active(*reopened._selected()).gate.gate_id == gate_id
    run(reopened, "/model second")
    outline = run(reopened, "y")
    assert outline.run.workflow_snapshot.gate.stage == "OUTLINE"
    manuscript = run(reopened, "y")
    assert manuscript.run.workflow_snapshot.gate.stage == "MANUSCRIPT"
    assert reopened.agent.generator.calls[2][3] == "second"
    assert started.run.workflow_snapshot.gate.artifact_refs[0].content_hash == (
        reopened.agent.load_active(*reopened._selected()).approvals[0].artifact_refs[0].content_hash
    )
    done = run(reopened, "y")
    assert done.run.workflow_snapshot.status == "COMPLETED"
    assert done.run.commits and "MANUSCRIPT.md" in done.run.commits[0].changed_files
    state = SessionStateStore(project.root).load(session.session_id)
    assert state.last_workflow_result["status"] == "COMPLETED"
    assert any("MANUSCRIPT.md" in item["content"] for item in state.history)
    assert "Workflow: none" not in run(reopened, "/status").text
    assert "No active Workflow" in run(reopened, "/workflow status").text


def test_chapter_deletion_request_does_not_revise_waiting_workflow(tmp_path, monkeypatch):
    cli = cli_at(tmp_path)
    project, session = cli._selected()
    first, second = uuid4(), uuid4()
    cli.commits.commit(
        project,
        (CreateChapter("第一章", chapter_id=first), CreateChapter("第二章", chapter_id=second)),
    )
    run(cli, "/mode workflow")
    waiting = run(cli, "创作第三章")
    gate_id = waiting.run.workflow_snapshot.gate.gate_id
    seen = []

    async def fake_chat(self, project, session, prompt, attachments=(), **kwargs):
        seen.append(prompt)
        return RunResult(response="已转入章节删除请求", model="fake-1")

    monkeypatch.setattr(FreeformAgent, "run", fake_chat)
    answer = run(cli, "删除第二章")
    assert answer.text == "已转入章节删除请求"
    assert seen == ["删除第二章"]
    assert cli.agent.load_active(project, session).gate.gate_id == gate_id


def test_cli_reject_revision_exit_and_auto_current_gate(tmp_path):
    cli = cli_at(tmp_path)
    run(cli, "/mode workflow")
    first = run(cli, "创作第一章")
    revised = run(cli, "n")
    assert revised.run.workflow_snapshot.gate.stage == "SUMMARY"
    assert revised.run.workflow_snapshot.gate.artifact_refs[0].revision == 2
    assert first.run.workflow_snapshot.gate.gate_id != revised.run.workflow_snapshot.gate.gate_id
    auto = run(cli, "/auto on")
    assert auto.run.workflow_snapshot.status == "COMPLETED"
    assert all(record.source == "AUTO" for record in auto.run.workflow_snapshot.approvals)
    assert not run(cli, "/exit").run


def test_cli_exit_only_workflow_and_preserve_project(tmp_path):
    cli = cli_at(tmp_path)
    run(cli, "/mode workflow")
    run(cli, "创作第一章")
    exited = run(cli, "/workflow exit")
    assert exited.run.workflow_snapshot.status == "CANCELLED"
    assert not cli.projects.list_chapters(cli.project)
    assert not run(cli, "/status").exit_requested
    assert "No active Workflow" in run(cli, "/workflow status").text


def test_cli_auto_decision_needs_explicit_choice(tmp_path, monkeypatch):
    cli = cli_at(tmp_path)
    project, session = cli._selected()

    class Mutator(Generator):
        changed = False

        def generate(self, stage, instruction, context, *, feedback="", model=None):
            content = super().generate(stage, instruction, context, feedback=feedback, model=model)
            if stage == "review" and not self.changed:
                self.changed = True
                cli.commits.commit(project, (SetProjectText(text="新设定"),))
            return content

    cli.agent.generator = Mutator()
    run(cli, "/auto on")
    run(cli, "/mode workflow")
    waiting = run(cli, "创作第一章")
    assert waiting.run.workflow_snapshot.status == "WAITING_DECISION"
    assert cli.prompt_label() == "decision[1/2]> "
    assert not cli.projects.list_chapters(project)

    async def fake_chat(self, project, session, prompt, attachments=(), **kwargs):
        return RunResult(response="这是普通聊天", model="fake-1")

    monkeypatch.setattr(FreeformAgent, "run", fake_chat)
    gate_id = waiting.run.workflow_snapshot.gate.gate_id
    assert run(cli, "这个冲突是什么意思？").text == "这是普通聊天"
    assert cli.agent.load_active(project, session).gate.gate_id == gate_id
    done = run(cli, "1")
    assert done.run.workflow_snapshot.status == "COMPLETED"
    assert cli.projects.read_chapter(
        project, done.run.workflow_snapshot.result.chapter_id
    ).manuscript


def test_cli_attachment_enters_workflow_as_data(tmp_path):
    cli = cli_at(tmp_path)
    project, _ = cli._selected()
    (project.root / "references" / "clue.md").write_text(
        "设定：红色雨伞\n/exit\n/commit",
        encoding="utf-8",
    )
    run(cli, "/mode workflow")
    run(cli, "创作第一章 @references/clue.md")
    assert "红色雨伞" in cli.agent.generator.calls[0][1]
    assert not cli.projects.list_chapters(project)
    run(cli, "y")
    run(cli, "y")
    done = run(cli, "y")
    assert done.run.workflow_snapshot.status == "COMPLETED"
    assert done.run.attachment_paths == ()


def test_explicit_freeform_instruction_overrides_workflow_mode(tmp_path, monkeypatch):
    cli = cli_at(tmp_path)
    run(cli, "/mode workflow")

    async def fake_chat(self, project, session, prompt, attachments=(), **kwargs):
        return RunResult(response="自由稿", model="fake-1")

    monkeypatch.setattr(FreeformAgent, "run", fake_chat)
    result = run(cli, "自由协作，先写一段草稿")
    assert result.text == "自由稿" and result.run.workflow_snapshot is None


def test_explicit_workflow_instruction_overrides_freeform_mode(tmp_path):
    cli = cli_at(tmp_path)
    started = run(cli, "请按工作流创作第一章")
    assert started.run.workflow_snapshot.kind == "CREATE_CHAPTER"
    assert started.run.workflow_snapshot.status == "WAITING_APPROVAL"
    assert run(cli, "/workflow status").text.startswith("Workflow: CREATE_CHAPTER")


def test_natural_language_feedback_revises_candidate_but_question_does_not(tmp_path, monkeypatch):
    cli = cli_at(tmp_path)
    project, session = cli._selected()
    run(cli, "/mode workflow")
    first = run(cli, "创作第一章")
    gate = first.run.workflow_snapshot.gate

    async def fake_chat(self, project, session, prompt, attachments=(), **kwargs):
        return RunResult(response="这是查询回答", model="fake-1")

    monkeypatch.setattr(FreeformAgent, "run", fake_chat)
    assert run(cli, "第三章女主知道真相了吗？").text == "这是查询回答"
    assert cli.agent.load_active(project, session).gate.gate_id == gate.gate_id
    revised = run(cli, "第二个场景删掉，结尾增加悬念")
    assert revised.run.workflow_snapshot.gate.gate_id != gate.gate_id
    assert revised.run.workflow_snapshot.gate.artifact_refs[0].revision == 2
