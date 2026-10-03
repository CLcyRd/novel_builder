"""M3.1 live Workflow gate. Run explicitly with RUN_REAL_LLM_TESTS=1."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from novel_agent.application import (
    build_commit_service,
    build_create_chapter_workflow,
    build_project_service,
    build_session_service,
)
from novel_agent.domain.change import SetProjectText
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.workflow.generator import LLMChapterGenerator
from novel_agent.workflow.models import GateResponse, WorkflowRequest

pytestmark = pytest.mark.real_llm


@pytest.fixture
def live(tmp_path):
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    gateway = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    project = build_project_service().create_project(tmp_path, "M3.1 Live", "Main")
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.metadata.initial_session_id,
        title="雨夜来信",
        instruction="写一章简短悬疑小说：林舟在雨夜收到一封没有署名的信。正文控制在约150字。",
    )
    print(f"model={gateway.definition().model_id}")
    return project, request, gateway


def respond(workflow, project, snapshot, action, feedback=None):
    return workflow.resume(
        project,
        snapshot.workflow_id,
        GateResponse(gate_id=snapshot.gate.gate_id, action=action, feedback=feedback),
    )


def test_live_manual_feedback_and_process_reopen(live):
    project, request, gateway = live
    workflow = build_create_chapter_workflow(LLMChapterGenerator(gateway))
    first = workflow.start(project, request)
    assert first.status == "WAITING_APPROVAL" and first.gate.stage == "SUMMARY"
    assert len(first.gate.artifact_refs) == 1
    revised = respond(workflow, project, first, "REVISE", "让信封上出现一枚蓝色火漆印")
    assert revised.status == "WAITING_APPROVAL"
    assert revised.gate.gate_id != first.gate.gate_id
    reopened = build_create_chapter_workflow(LLMChapterGenerator(gateway))
    assert reopened.load_active(project, request.session_id) == revised
    outline = respond(reopened, project, revised, "APPROVE")
    assert outline.gate.stage == "OUTLINE" and len(outline.gate.artifact_refs) == 1
    previous_summary = revised.gate.artifact_refs[0]
    revised_outline = respond(reopened, project, outline, "REVISE", "第二个场景放在钟楼。")
    assert revised_outline.gate.stage == "OUTLINE"
    assert revised_outline.gate.artifact_refs[0].revision == 2
    assert previous_summary in reopened.artifacts.get(
        project, revised_outline.gate.artifact_refs[0]
    ).dependencies
    manuscript = respond(reopened, project, revised_outline, "APPROVE")
    assert manuscript.status == "WAITING_APPROVAL" and manuscript.gate.stage == "MANUSCRIPT"
    done = respond(reopened, project, manuscript, "APPROVE")
    assert done.status == "COMPLETED" and done.result.commit
    chapter = build_project_service().read_chapter(project, request.chapter_id)
    assert chapter.summary.strip() and chapter.outline.strip() and chapter.manuscript.strip()
    assert len(done.result.commit.committed_artifacts) == 3


def test_live_auto_still_pauses_for_freshness_decision(live):
    project, request, gateway = live
    build_session_service().update_settings(project, request.session_id, auto_approval=True)

    class ConcurrentEditGenerator(LLMChapterGenerator):
        changed = False

        def generate(self, stage, instruction, context, *, feedback="", model=None):
            content = super().generate(stage, instruction, context, feedback=feedback, model=model)
            if stage == "review" and not self.changed:
                self.changed = True
                build_commit_service().commit(project, (SetProjectText(text="新的正式设定"),))
            return content

    workflow = build_create_chapter_workflow(ConcurrentEditGenerator(gateway))
    waiting = workflow.start(project, request)
    assert waiting.status == "WAITING_DECISION"
    assert waiting.gate.kind == "DECISION" and "PROJECT.md" in waiting.gate.paths
    assert not build_project_service().list_chapters(project)
    done = respond(workflow, project, waiting, "REGENERATE")
    assert done.status == "COMPLETED" and done.result.commit
    assert build_project_service().read_chapter(project, request.chapter_id).manuscript.strip()


def test_live_exit_preserves_uncommitted_artifacts(live):
    project, request, gateway = live
    workflow = build_create_chapter_workflow(LLMChapterGenerator(gateway))
    waiting = workflow.start(project, request)
    assert waiting.status == "WAITING_APPROVAL"
    cancelled = workflow.exit(project, request.workflow_id)
    assert cancelled.status == "CANCELLED" and cancelled.result.artifact_refs
    assert not build_project_service().list_chapters(project)


def test_live_reject_then_resume_from_independent_process(live):
    project, request, gateway = live
    script = """
import sys
from pathlib import Path
from novel_agent.application import build_create_chapter_workflow, build_project_service
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.workflow.generator import LLMChapterGenerator
from novel_agent.workflow.models import WorkflowRequest
project = build_project_service().open_project(Path(sys.argv[1]))
request = WorkflowRequest.model_validate_json(sys.argv[2])
gateway = ModelGateway.from_env(Path(sys.argv[3]))
snapshot = build_create_chapter_workflow(LLMChapterGenerator(gateway)).start(project, request)
assert snapshot.status == 'WAITING_APPROVAL'
print(snapshot.gate.gate_id)
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(project.root),
            request.model_dump_json(),
            str(Path(__file__).resolve().parents[2] / ".env"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert child.returncode == 0, child.stderr
    workflow = build_create_chapter_workflow(LLMChapterGenerator(gateway))
    restored = workflow.load_active(project, request.session_id)
    assert restored.status == "WAITING_APPROVAL"
    assert str(restored.gate.gate_id) in child.stdout
    revised = respond(workflow, project, restored, "REJECT")
    assert revised.gate.artifact_refs[0].revision == 2
    outline = respond(workflow, project, revised, "APPROVE")
    assert outline.gate.stage == "OUTLINE"
    manuscript = respond(workflow, project, outline, "APPROVE")
    done = respond(workflow, project, manuscript, "APPROVE")
    assert done.status == "COMPLETED" and done.result.commit
    assert [item.source for item in done.result.approvals] == ["HUMAN", "HUMAN", "HUMAN"]
    assert build_project_service().read_chapter(project, request.chapter_id).manuscript.strip()
