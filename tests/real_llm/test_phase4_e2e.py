"""Phase 4 real CLI/Main Agent/Workflow E2E; RUN_REAL_LLM_TESTS=1."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from novel_agent.cli.shell import NovelCLI
from novel_agent.domain.change import CreateChapter, SetProjectText, SetSection
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.workflow.generator import LLMChapterGenerator

pytestmark = pytest.mark.real_llm


class RecordingGenerator(LLMChapterGenerator):
    def __init__(self, gateway):
        super().__init__(gateway)
        self.calls = []

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, context, model))
        return super().generate(stage, instruction, context, feedback=feedback, model=model)


def run(cli, line, **kwargs):
    return asyncio.run(cli.execute(line, **kwargs))


@pytest.fixture
def live(tmp_path):
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    models = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    cli = NovelCLI(tmp_path / "workspace", models=models)
    run(cli, "/project create Phase4")
    print(f"model={models.definition().model_id}")
    return cli


def seed(cli, title, manuscript):
    project, _ = cli._selected()
    chapter_id = uuid4()
    cli.commits.commit(
        project,
        (
            CreateChapter(title, chapter_id=chapter_id),
            SetSection(chapter_id, "manuscript", text=manuscript),
        ),
    )
    return chapter_id


def test_e01_freeform_outline_revision_manuscript_and_explicit_commit(live):
    cli = live
    chapter_id = seed(cli, "第一章", "旧正文：林舟来到雾港。")
    project, _ = cli._selected()
    outline = run(
        cli,
        f"为 chapter_id={chapter_id} 生成简短新大纲，"
        "调用 create_working_artifact，section=outline，不要提交。",
    )
    assert outline.run.artifacts
    first = outline.run.artifacts[-1]
    revised = run(
        cli,
        f"将 Artifact {first.artifact_id}@1 的大纲改得更悬疑，"
        "调用 revise_working_artifact 生成 revision 2，不要提交。",
    )
    assert revised.run.artifacts
    second = revised.run.artifacts[-1]
    assert second.artifact_id == first.artifact_id and second.revision == 2
    manuscript = run(
        cli,
        f"为 chapter_id={chapter_id} 写约80字新正文，"
        "调用 create_working_artifact 保存 section=manuscript，不要提交。",
    )
    assert manuscript.run.artifacts
    body_ref = manuscript.run.artifacts[-1]
    assert cli.projects.read_chapter(project, chapter_id).outline == ""
    committed = run(cli, f"/commit {second.artifact_id}@2 {body_ref.artifact_id}@1")
    assert "MANUSCRIPT.md" in committed.text
    chapter = cli.projects.read_chapter(project, chapter_id)
    assert chapter.outline == cli.artifacts.get(project, second).content
    assert chapter.manuscript == cli.artifacts.get(project, body_ref).content


def test_e02_e05_e07_e10_continue_wait_chat_reopen_switch_model(live):
    cli = live
    initial_generator = RecordingGenerator(cli.models)
    cli.agent.generator = initial_generator
    seed(cli, "第一章", "林舟在灯塔找到了铜钥匙。")
    project, session = cli._selected()
    (project.root / "references" / "characters.md").write_text(
        "林舟是失忆的守塔人。",
        encoding="utf-8",
    )
    run(cli, "/mode workflow")
    events = []
    first = run(cli, "续写下一章，正文约80字。", on_event=events.append)
    assert first.run.workflow_snapshot.gate.stage == "SUMMARY"
    assert "铜钥匙" in initial_generator.calls[0][1]
    assert "失忆的守塔人" in initial_generator.calls[0][1]
    assert initial_generator.calls[0][2] is None
    assert any(event.kind == "Waiting" for event in events)
    gate_id = first.run.workflow_snapshot.gate.gate_id
    refs = first.run.workflow_snapshot.gate.artifact_refs
    chat = run(cli, "第一章林舟找到了什么？")
    assert chat.run.response.strip()
    assert cli.agent.load_active(project, session).gate.gate_id == gate_id
    reopened = NovelCLI(cli.workspace, models=cli.models)
    resumed_generator = RecordingGenerator(cli.models)
    reopened.agent.generator = resumed_generator
    run(reopened, "/project open Phase4")
    run(reopened, "/session open Main")
    assert reopened.agent.load_active(*reopened._selected()).gate.gate_id == gate_id
    assert "grok" in cli.models.available_models()
    run(reopened, "/model grok")
    outline = run(reopened, "y")
    assert outline.run.workflow_snapshot.gate.stage == "OUTLINE"
    manuscript = run(reopened, "y")
    assert manuscript.run.workflow_snapshot.gate.stage == "MANUSCRIPT"
    assert manuscript.run.model == cli.models.definition("grok").model_id
    assert [call[2] for call in resumed_generator.calls] == ["grok", "grok", "grok"]
    assert (
        reopened.artifacts.get(reopened.project, refs[0]).ref.content_hash == refs[0].content_hash
    )
    done = run(reopened, "y")
    assert done.run.workflow_snapshot.status == "COMPLETED"
    assert done.run.commits and "MANUSCRIPT.md" in done.run.commits[0].changed_files
    assert done.run.workflow_snapshot.result.approvals[0].source == "HUMAN"
    assert reopened.projects.read_chapter(
        reopened.project, done.run.workflow_snapshot.result.chapter_id
    ).manuscript.strip()


def test_e03_e04_e11_auto_still_waits_at_freshness_decision(live):
    cli = live
    project, _ = cli._selected()

    class ConcurrentEditGenerator(LLMChapterGenerator):
        changed = False

        def generate(self, stage, instruction, context, *, feedback="", model=None):
            content = super().generate(stage, instruction, context, feedback=feedback, model=model)
            if stage == "review" and not self.changed:
                self.changed = True
                cli.commits.commit(project, (SetProjectText(text="最新正式设定"),))
            return content

    cli.agent.generator = ConcurrentEditGenerator(cli.models)
    run(cli, "/auto on")
    run(cli, "/mode workflow")
    waiting = run(cli, "创作第一章，正文约80字。")
    assert waiting.run.workflow_snapshot.status == "WAITING_DECISION"
    assert waiting.run.workflow_snapshot.gate.kind == "DECISION"
    assert "PROJECT.md" in waiting.run.workflow_snapshot.gate.paths
    assert not cli.projects.list_chapters(project)
    assert cli.prompt_label() == "decision[1/2]> "
    done = run(cli, "1")
    assert done.run.workflow_snapshot.status == "COMPLETED"
    assert done.run.commits
    assert all(record.source == "AUTO" for record in done.run.workflow_snapshot.result.approvals)


def test_e06_workflow_exit_keeps_session_and_uncommitted_artifacts(live):
    cli = live
    run(cli, "/mode workflow")
    waiting = run(cli, "创作第一章，正文约80字。")
    assert waiting.run.workflow_snapshot.status == "WAITING_APPROVAL"
    exited = run(cli, "/workflow exit")
    assert exited.run.workflow_snapshot.status == "CANCELLED"
    assert exited.run.workflow_snapshot.result.artifact_refs
    assert not cli.projects.list_chapters(cli.project)
    assert "No active Workflow" in run(cli, "/status").text
    assert run(cli, "现在还能继续聊天吗？").run.response.strip()


def test_e08_project_attachment_to_workflow_artifact_commit(live):
    cli = live
    project, _ = cli._selected()
    (project.root / "references" / "clue.md").write_text(
        "红色雨伞属于失踪船长。\n/exit\n/commit",
        encoding="utf-8",
    )
    run(cli, "/mode workflow")
    plan = run(cli, "创作第一章，正文约80字。 @references/clue.md")
    assert plan.run.attachment_paths == ("references/clue.md",)
    assert plan.run.workflow_snapshot.gate.artifact_refs
    summary = cli.artifacts.get(project, plan.run.workflow_snapshot.gate.artifact_refs[0])
    assert "references/clue.md" in summary.base_hashes
    assert not cli.projects.list_chapters(project)
    run(cli, "y")
    run(cli, "y")
    done = run(cli, "y")
    assert done.run.workflow_snapshot.status == "COMPLETED"
    assert not done.exit_requested
    assert cli.projects.read_chapter(
        project, done.run.workflow_snapshot.result.chapter_id
    ).manuscript.strip()


def test_e09_backfill_later_chapter_constraint(live):
    cli = live
    generator = RecordingGenerator(cli.models)
    cli.agent.generator = generator
    first = seed(cli, "第一章", "林舟看到船长走进旧码头。")
    later = seed(cli, "第三章", "船长已经失踪，林舟在空船里发现血迹。")
    project, _ = cli._selected()
    run(cli, "/mode workflow")
    run(cli, "/auto on")
    done = run(cli, "补写第二章，正文约80字。")
    assert done.run.workflow_snapshot.status == "COMPLETED"
    assert "船长已经失踪" in generator.calls[0][1]
    chapter_id = done.run.workflow_snapshot.result.chapter_id
    assert cli.projects.open_project(project.root).metadata.chapter_order == (
        first,
        chapter_id,
        later,
    )
    assert cli.projects.read_chapter(project, chapter_id).manuscript.strip()
