"""Independent chapter approvals, downstream invalidation and durable v1 compatibility."""

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from novel_agent.application import build_create_chapter_workflow, build_project_service
from novel_agent.domain.artifact import ArtifactStatus
from novel_agent.domain.chapter import parse_chapter
from novel_agent.domain.errors import GenerationBlocked, InvalidChange
from novel_agent.workflow.models import GateResponse, WorkflowRequest


class Writer:
    def __init__(self):
        self.calls = []
        self.instructions = []
        self.block_second_body = False

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, context, feedback))
        self.instructions.append((stage, instruction))
        count = self.count(stage)
        if stage == "manuscript" and count == 2 and self.block_second_body:
            raise GenerationBlocked("请补充正文条件")
        return f"{stage} 候选 {count} {feedback}".strip()

    def count(self, stage):
        return sum(call[0] == stage for call in self.calls)


def setup(tmp_path):
    project = build_project_service().create_project(tmp_path, "Split", "Main")
    writer = Writer()
    service = build_create_chapter_workflow(writer)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="雨夜",
        instruction="创作简短章节",
    )
    return project, writer, service, request


def reply(service, project, state, action="APPROVE", feedback=None):
    return service.resume(
        project,
        state.workflow_id,
        GateResponse(gate_id=state.gate.gate_id, action=action, feedback=feedback),
    )


def record_and_path(service, project, request):
    record = service.drafts.record(project, request.session_id, request.task_id, request.chapter_id)
    return record, project.root / record["path"]


def edit_section(path, section, content):
    start = f"<!-- novel-agent:{section}:start -->\n"
    end = f"\n<!-- novel-agent:{section}:end -->"
    before, rest = path.read_text(encoding="utf-8").split(start, 1)
    _, after = rest.split(end, 1)
    path.write_text(before + start + content + end + after, encoding="utf-8", newline="\n")


def test_rejection_and_revision_are_scoped_to_the_current_stage(tmp_path):
    project, writer, service, request = setup(tmp_path)
    summary = service.start(project, request)
    revised = reply(service, project, summary, "REVISE", "改成灯塔")
    assert revised.gate.stage == "SUMMARY" and revised.gate.artifact_refs[0].revision == 2
    assert writer.count("summary") == 2 and writer.count("outline") == 0
    with pytest.raises(InvalidChange, match="different Gate"):
        reply(service, project, summary)
    outline = reply(service, project, revised)
    approved_summary = revised.gate.artifact_refs[0]
    assert service.artifacts.status(project, approved_summary).status == ArtifactStatus.APPROVED
    revised_outline = reply(service, project, outline, "REJECT")
    assert revised_outline.gate.stage == "OUTLINE"
    assert revised_outline.gate.artifact_refs[0].revision == 2
    assert writer.count("summary") == 2 and writer.count("outline") == 2
    assert (
        approved_summary
        in service.artifacts.get(project, revised_outline.gate.artifact_refs[0]).dependencies
    )
    body = reply(service, project, revised_outline)
    assert body.gate.stage == "MANUSCRIPT"
    assert writer.count("manuscript") == 1
    assert not build_project_service().list_chapters(project)


def test_manual_versions_are_approved_before_downstream_model_calls_and_reopen(tmp_path):
    project, writer, service, request = setup(tmp_path)
    service.start(project, request)
    _, path = record_and_path(service, project, request)
    edit_section(path, "summary", "人工概述：林舟发现钥匙。")
    service = build_create_chapter_workflow(writer)
    outline = reply(service, project, service.load_active(project, request.session_id))
    block = next(block for block in writer.calls[-1][1].blocks if block.kind == "current_summary")
    assert block.content == "人工概述：林舟发现钥匙。"
    assert block.status == "APPROVED" and block.revision == 2
    assert path.exists()
    edit_section(path, "outline", "人工大纲：拿走钥匙，离开钟楼。")
    service = build_create_chapter_workflow(writer)
    body = reply(service, project, service.load_active(project, request.session_id))
    body_context = next(context for stage, context, _ in writer.calls if stage == "manuscript")
    block = next(block for block in body_context.blocks if block.kind == "current_outline")
    assert block.content == "人工大纲：拿走钥匙，离开钟楼。" and block.status == "APPROVED"
    assert path.exists() and outline.gate.stage == "OUTLINE"
    edit_section(path, "manuscript", "人工正文，逐字保留。")
    done = reply(service, project, body)
    assert done.status == "COMPLETED" and not path.exists()
    chapter = build_project_service().read_chapter(project, request.chapter_id)
    assert chapter.summary == "人工概述：林舟发现钥匙。"
    assert chapter.outline == "人工大纲：拿走钥匙，离开钟楼。"
    assert chapter.manuscript == "人工正文，逐字保留。"


@pytest.mark.parametrize(
    "stage,section", [("OUTLINE", "summary"), ("MANUSCRIPT", "summary"), ("MANUSCRIPT", "outline")]
)
@pytest.mark.parametrize("action", ["KEEP", "REGENERATE"])
def test_upstream_edits_return_to_separate_approval_and_prune_stale_bindings(
    tmp_path, stage, section, action
):
    project, writer, service, request = setup(tmp_path)
    state = reply(service, project, service.start(project, request))
    if stage == "MANUSCRIPT":
        state = reply(service, project, state)
    old_refs = state.artifact_refs
    _, path = record_and_path(service, project, request)
    edit_section(path, section, "人工修改上游")
    decision = reply(service, project, state)
    assert decision.status == "WAITING_DECISION"
    with pytest.raises(InvalidChange, match="explicit choice"):
        reply(service, project, decision)
    reconfirm = reply(service, project, decision, action)
    assert reconfirm.gate.stage == section.upper()
    assert service.artifacts.get(project, reconfirm.gate.artifact_refs[0]).content == "人工修改上游"
    if action == "REGENERATE":
        record, _ = record_and_path(service, project, request)
        expected = {"summary"} if section == "summary" else {"summary", "outline"}
        assert set(record["refs"]) == expected
        draft = parse_chapter(
            path.read_text(encoding="utf-8")
            .replace("## 章节概述\n", "")
            .replace("## 章节大纲\n", "")
            .replace("## 章节正文\n", "")
        )
        assert not draft.manuscript
        if section == "summary":
            assert not draft.outline
    assert all(service.artifacts.get(project, ref).content for ref in old_refs)
    service = build_create_chapter_workflow(writer)
    state = service.load_active(project, request.session_id)
    while state.status == "WAITING_APPROVAL":
        state = reply(service, project, state)
    assert state.status == "COMPLETED"
    assert writer.count("summary") == 1
    if action == "KEEP":
        assert writer.count("outline") == 1 and writer.count("manuscript") == 1
    else:
        assert writer.count("manuscript") == (2 if stage == "MANUSCRIPT" else 1)


def test_concurrent_summary_edit_during_outline_requires_reconfirmation_without_body_generation(
    tmp_path,
):
    project, writer, service, request = setup(tmp_path)
    summary = service.start(project, request)
    _, path = record_and_path(service, project, request)
    original = writer.generate

    def generate(stage, *args, **kwargs):
        if stage == "outline":
            edit_section(path, "summary", "并发修改的概述")
        return original(stage, *args, **kwargs)

    writer.generate = generate
    waiting = reply(service, project, summary)
    assert waiting.status == "WAITING_DECISION" and writer.count("manuscript") == 0
    reconfirm = reply(service, project, waiting, "KEEP")
    assert reconfirm.gate.stage == "SUMMARY"
    outline = reply(service, project, reconfirm)
    assert outline.gate.stage == "OUTLINE" and writer.count("outline") == 1
    assert (
        reconfirm.gate.artifact_refs[0]
        in service.artifacts.get(project, outline.gate.artifact_refs[0]).dependencies
    )


def test_regenerated_body_failure_cannot_restore_invalidated_body_from_draft(tmp_path):
    project, writer, service, request = setup(tmp_path)
    state = service.start(project, request)
    state = reply(service, project, reply(service, project, state))
    old_body = state.gate.artifact_refs[0]
    _, path = record_and_path(service, project, request)
    edit_section(path, "outline", "人工新大纲")
    decision = reply(service, project, state)
    outline = reply(service, project, decision, "REGENERATE")
    writer.block_second_body = True
    blocked = reply(service, project, outline)
    assert blocked.gate.generation_blocked and blocked.gate.stage == "MANUSCRIPT"
    record, _ = record_and_path(service, project, request)
    assert "manuscript" not in record["refs"]
    assert old_body not in blocked.artifact_refs
    assert service.artifacts.get(project, old_body).content
    writer.block_second_body = False
    regenerated = reply(service, project, blocked, "REGENERATE", "按新大纲生成")
    assert regenerated.gate.stage == "MANUSCRIPT"
    assert regenerated.gate.artifact_refs[0] != old_body


def test_revision_feedback_survives_return_to_upstream_confirmation(tmp_path):
    project, writer, service, request = setup(tmp_path)
    summary = service.start(project, request)
    body = reply(service, project, reply(service, project, summary))
    _, path = record_and_path(service, project, request)
    edit_section(path, "outline", "人工修改大纲")
    outline = reply(service, project, body, "REVISE", "结尾改为日出")
    assert outline.gate.stage == "OUTLINE"
    next_body = reply(service, project, outline)
    assert next_body.gate.stage == "MANUSCRIPT"
    assert "结尾改为日出" in next(
        instruction for stage, instruction in reversed(writer.instructions) if stage == "manuscript"
    )


@pytest.mark.parametrize("position", ["PLAN", "MANUSCRIPT", "GENERATION"])
def test_unversioned_legacy_checkpoint_reopens_with_original_graph(tmp_path, position):
    project, writer, service, request = setup(tmp_path)
    if position == "GENERATION":
        original = writer.generate

        def blocked(*args, **kwargs):
            raise GenerationBlocked("缺条件")

        writer.generate = blocked
    path = project.root / ".novel-agent" / "workflows.sqlite"
    config = service._config(request.workflow_id)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph = service._build(project, saver, workflow_version=1)
        graph.invoke({"request": request.model_dump(mode="json"), "feedback": ""}, config)
        first = service._snapshot(request.workflow_id, graph.get_state(config).values)
    service._sync_session(project, request.session_id, first)
    if position == "MANUSCRIPT":
        first = reply(service, project, first)
    reopened = build_create_chapter_workflow(writer)
    restored = reopened.load_active(project, request.session_id)
    assert restored == first
    if position == "GENERATION":
        writer.generate = original
        restored = reply(reopened, project, restored, "REGENERATE", "明确条件")
    if restored.gate.stage == "PLAN":
        assert len(restored.gate.artifact_refs) == 2
        restored = reply(reopened, project, restored)
    assert restored.gate.stage == "MANUSCRIPT"
    done = reply(reopened, project, restored)
    assert done.status == "COMPLETED"
    assert [record.stage for record in done.approvals] == ["PLAN", "MANUSCRIPT"]
    assert writer.count("manuscript") == 1


@pytest.mark.parametrize("stage", ["SUMMARY", "OUTLINE", "MANUSCRIPT"])
def test_exit_preserves_candidates_at_each_new_gate(tmp_path, stage):
    project, _, service, request = setup(tmp_path)
    state = service.start(project, request)
    while state.gate.stage != stage:
        state = reply(service, project, state)
    refs = state.artifact_refs
    cancelled = service.exit(project, request.workflow_id)
    assert cancelled.status == "CANCELLED" and cancelled.artifact_refs == refs
    assert not build_project_service().list_chapters(project)
