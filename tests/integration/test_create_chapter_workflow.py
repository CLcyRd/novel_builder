import subprocess
import sys
from uuid import uuid4

import pytest

from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_create_chapter_workflow,
    build_project_service,
    build_session_service,
)
from novel_agent.domain.artifact import ArtifactStatus
from novel_agent.domain.change import SetProjectText
from novel_agent.domain.errors import InvalidChange
from novel_agent.runtime.state import SessionStateStore
from novel_agent.workflow.models import GateResponse, WorkflowRequest


class FakeGenerator:
    def __init__(self):
        self.calls = []

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, feedback, model))
        return f"{stage} 内容 {feedback}".strip()


def setup(tmp_path, generator=None):
    project = build_project_service().create_project(tmp_path, "雨夜", "创作")
    generator = generator or FakeGenerator()
    workflow = build_create_chapter_workflow(generator)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.metadata.initial_session_id,
        title="第一章",
        instruction="写一章雨夜相遇",
    )
    return project, generator, workflow, request


def answer(workflow, project, snapshot, action, feedback=None):
    assert snapshot.gate is not None
    return workflow.resume(
        project,
        snapshot.workflow_id,
        GateResponse(gate_id=snapshot.gate.gate_id, action=action, feedback=feedback),
    )


def test_manual_three_approvals_and_exact_commit(tmp_path):
    project, generator, workflow, request = setup(tmp_path)
    plan = workflow.start(project, request)
    assert plan.status == "WAITING_APPROVAL" and plan.gate.stage == "SUMMARY"
    assert [call[0] for call in generator.calls] == ["summary"]
    assert len(plan.gate.artifact_refs) == 1
    assert not build_project_service().list_chapters(project)
    assert workflow.load_active(project, request.session_id) == plan
    outline = answer(workflow, project, plan, "APPROVE")
    assert outline.gate.stage == "OUTLINE" and len(outline.gate.artifact_refs) == 1
    assert build_artifact_service().status(project, plan.gate.artifact_refs[0]).status == (
        ArtifactStatus.APPROVED
    )
    manuscript = answer(workflow, project, outline, "APPROVE")
    assert manuscript.status == "WAITING_APPROVAL" and manuscript.gate.stage == "MANUSCRIPT"
    assert not build_project_service().list_chapters(project)
    result = answer(workflow, project, manuscript, "APPROVE")
    assert result.status == "COMPLETED" and result.result.commit is not None
    chapter = build_project_service().read_chapter(project, request.chapter_id)
    assert chapter.summary == "summary 内容"
    assert chapter.outline == "outline 内容"
    assert chapter.manuscript == "manuscript 内容"
    assert "manuscript 内容" in (project.root / "MANUSCRIPT.md").read_text(encoding="utf-8")
    assert all(
        build_artifact_service().status(project, ref).status == ArtifactStatus.COMMITTED
        for ref in result.result.artifact_refs[:3]
    )
    assert SessionStateStore(project.root).load(request.session_id).active_workflow_id is None
    assert workflow.load_active(project, request.session_id) is None
    assert [call[0] for call in generator.calls] == ["summary", "outline", "manuscript", "review"]
    assert [item.stage for item in result.approvals] == ["SUMMARY", "OUTLINE", "MANUSCRIPT"]


def test_revision_reopen_and_exit_do_not_mutate_project(tmp_path):
    project, generator, workflow, request = setup(tmp_path)
    first = workflow.start(project, request)
    revised = answer(workflow, project, first, "REVISE", "增加悬念")
    assert revised.status == "WAITING_APPROVAL" and revised.gate.gate_id != first.gate.gate_id
    assert any(call[1] == "增加悬念" for call in generator.calls)
    assert (
        build_artifact_service().get(project, first.gate.artifact_refs[0]).content == "summary 内容"
    )
    restored = build_create_chapter_workflow(generator).load_active(project, request.session_id)
    assert restored == revised
    cancelled = build_create_chapter_workflow(generator).exit(project, request.workflow_id)
    assert cancelled.status == "CANCELLED"
    assert not build_project_service().list_chapters(project)
    assert (
        SessionStateStore(project.root).load(request.session_id).last_workflow_result["status"]
        == "CANCELLED"
    )


def test_auto_approval_and_decision_gate_still_waits(tmp_path):
    project, generator, workflow, request = setup(tmp_path)
    build_session_service().update_settings(project, request.session_id, auto_approval=True)

    class MutatingGenerator(FakeGenerator):
        def generate(self, stage, instruction, context, *, feedback="", model=None):
            result = super().generate(stage, instruction, context, feedback=feedback, model=model)
            if stage == "review" and not getattr(self, "changed", False):
                self.changed = True
                build_commit_service().commit(project, (SetProjectText(text="外部修改"),))
            return result

    generator = MutatingGenerator()
    workflow = build_create_chapter_workflow(generator)
    waiting = workflow.start(project, request)
    assert waiting.status == "WAITING_DECISION" and waiting.gate.kind == "DECISION"
    assert not build_project_service().list_chapters(project)
    assert waiting.gate.paths == ("PROJECT.md",)
    with pytest.raises(InvalidChange):
        answer(workflow, project, waiting, "APPROVE")
    done = answer(workflow, project, waiting, "REGENERATE")
    assert done.status == "COMPLETED"
    assert build_project_service().read_chapter(project, request.chapter_id).manuscript


def test_enable_auto_while_waiting_and_wrong_gate_rejected(tmp_path):
    project, _, workflow, request = setup(tmp_path)
    workflow.start(project, request)
    with pytest.raises(InvalidChange):
        workflow.resume(
            project, request.workflow_id, GateResponse(gate_id=uuid4(), action="APPROVE")
        )
    build_session_service().update_settings(project, request.session_id, auto_approval=True)
    done = workflow.apply_auto(project, request.workflow_id)
    assert done.status == "COMPLETED"


def test_manuscript_feedback_revises_exact_candidate(tmp_path):
    project, generator, workflow, request = setup(tmp_path)
    plan = workflow.start(project, request)
    plan = answer(workflow, project, plan, "APPROVE")
    first = answer(workflow, project, plan, "APPROVE")
    old_ref = first.gate.artifact_refs[0]
    revised = answer(workflow, project, first, "REVISE", "让结尾更紧张")
    assert revised.status == "WAITING_APPROVAL"
    assert revised.gate.artifact_refs[0].artifact_id == old_ref.artifact_id
    assert revised.gate.artifact_refs[0].revision == old_ref.revision + 1
    assert build_artifact_service().get(project, old_ref).content == "manuscript 内容"
    done = answer(workflow, project, revised, "APPROVE")
    assert done.status == "COMPLETED"
    assert build_project_service().read_chapter(project, request.chapter_id).manuscript == (
        "manuscript 内容 让结尾更紧张"
    )


def test_reject_generates_new_revision_and_records_manual_vs_auto(tmp_path):
    project, _, workflow, request = setup(tmp_path)
    first = workflow.start(project, request)
    rejected = answer(workflow, project, first, "REJECT")
    assert rejected.gate.stage == "SUMMARY"
    assert rejected.gate.artifact_refs[0].revision == 2
    assert rejected.approvals == ()
    outline = answer(workflow, project, rejected, "APPROVE")
    assert outline.approvals[0].source == "HUMAN"
    build_session_service().update_settings(project, request.session_id, auto_approval=True)
    done = workflow.apply_auto(project, request.workflow_id)
    assert done.status == "COMPLETED"
    assert [item.source for item in done.result.approvals] == ["HUMAN", "AUTO", "AUTO"]


def test_task_dependency_blocks_until_prerequisite_commits(tmp_path):
    project, generator, workflow, first = setup(tmp_path)
    dependent = WorkflowRequest(
        project_id=project.project_id,
        session_id=first.session_id,
        title="第二章",
        instruction="续写第二章",
        depends_on=(first.task_id,),
    )
    with pytest.raises(InvalidChange, match="incomplete dependencies"):
        workflow.start(project, dependent)
    plan = workflow.start(project, first)
    plan = answer(workflow, project, plan, "APPROVE")
    manuscript = answer(workflow, project, plan, "APPROVE")
    done = answer(workflow, project, manuscript, "APPROVE")
    assert done.status == "COMPLETED"
    assert (
        first.task_id in SessionStateStore(project.root).load(first.session_id).completed_task_ids
    )
    next_plan = workflow.start(project, dependent)
    assert next_plan.status == "WAITING_APPROVAL"


def test_resume_waiting_gate_in_new_python_process(tmp_path):
    project, generator, workflow, request = setup(tmp_path)
    first = workflow.start(project, request)
    script = """
import sys
from pathlib import Path
from uuid import UUID
from novel_agent.application import build_create_chapter_workflow, build_project_service
from novel_agent.workflow.models import GateResponse
class Generator:
    def generate(self, stage, instruction, context, *, feedback='', model=None):
        return stage + ' 内容'
project = build_project_service().open_project(Path(sys.argv[1]))
workflow = build_create_chapter_workflow(Generator())
snapshot = workflow.load(project, UUID(sys.argv[2]))
assert snapshot.status == 'WAITING_APPROVAL'
next_snapshot = workflow.resume(
    project, snapshot.workflow_id,
    GateResponse(gate_id=snapshot.gate.gate_id, action='APPROVE'),
)
assert next_snapshot.status == 'WAITING_APPROVAL'
assert next_snapshot.gate.stage == 'OUTLINE'
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(project.root), str(request.workflow_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    restored = workflow.load_active(project, request.session_id)
    assert restored.gate.stage == "OUTLINE"
    restored = answer(workflow, project, restored, "APPROVE")
    done = answer(workflow, project, restored, "APPROVE")
    assert done.status == "COMPLETED"
    assert first.gate.gate_id != restored.gate.gate_id
