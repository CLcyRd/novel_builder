import pytest

from novel_agent.application import (
    build_create_chapter_workflow,
    build_multi_chapter_workflow,
    build_project_service,
    build_session_service,
)
from novel_agent.domain.errors import GenerationBlocked
from novel_agent.workflow.models import ChapterTarget, GateResponse, WorkflowRequest


class Writer:
    def __init__(self, blocked_stage, blocked_call=1):
        self.stage = blocked_stage
        self.blocked_call = blocked_call
        self.calls = []
        self.instructions = []

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, feedback))
        self.instructions.append((stage, instruction))
        count = sum(call[0] == stage for call in self.calls)
        if stage == self.stage and count == self.blocked_call:
            raise GenerationBlocked("请明确角色身份冲突")
        return f"{stage} 生成 {count} {feedback}"


def reply(service, project, state, action="APPROVE", feedback=None):
    return service.resume(
        project,
        state.workflow_id,
        GateResponse(
            gate_id=state.gate.gate_id,
            action=action,
            feedback=feedback,
        ),
    )


@pytest.mark.parametrize("batch", [False, True])
def test_generation_decision_persists_under_auto_and_never_becomes_novel_content(tmp_path, batch):
    projects = build_project_service()
    project = projects.create_project(tmp_path, "Decision", "Main")
    session = project.sessions[0]
    build_session_service().update_settings(project, session.session_id, auto_approval=True)
    writer = Writer("summary", 2 if batch else 1)
    factory = build_multi_chapter_workflow if batch else build_create_chapter_workflow
    service = factory(writer)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=session.session_id,
        kind="MULTI_CHAPTER" if batch else "CREATE_CHAPTER",
        title="章",
        instruction="创作",
        chapters=(ChapterTarget(title="一"), ChapterTarget(title="二")) if batch else (),
    )
    waiting = service.start(project, request)
    assert waiting.status == "WAITING_DECISION" and waiting.gate.generation_blocked
    assert not projects.list_chapters(project)
    refs = waiting.artifact_refs
    with pytest.raises(Exception, match="Approval Gate"):
        service.apply_auto(project, request.workflow_id)
    reopened = factory(writer)
    assert reopened.load(project, request.workflow_id).gate == waiting.gate
    done = reply(reopened, project, waiting, "REGENERATE", "确定为守塔人，重新生成")
    assert done.status == "COMPLETED"
    assert "确定为守塔人" in writer.calls[2 if batch else 1][1]
    assert all(
        "确定为守塔人" in instruction
        for stage, instruction in writer.instructions
        if stage in {"outline", "manuscript", "review"}
    )
    if batch:
        assert sum(stage == "summary" for stage, _ in writer.calls) == 3
        assert all(ref in done.result.artifact_refs for ref in refs)
    assert all(
        "NEEDS_DECISION" not in chapter.manuscript for _, chapter in projects.list_chapters(project)
    )


@pytest.mark.parametrize("batch", [False, True])
def test_review_retry_does_not_regenerate_manuscript(tmp_path, batch):
    project = build_project_service().create_project(tmp_path, "Review", "Main")
    writer = Writer("review")
    factory = build_multi_chapter_workflow if batch else build_create_chapter_workflow
    service = factory(writer)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        kind="MULTI_CHAPTER" if batch else "CREATE_CHAPTER",
        title="章",
        instruction="创作",
        chapters=(ChapterTarget(title="一"), ChapterTarget(title="二")) if batch else (),
    )
    state = service.start(project, request)
    while state.status == "WAITING_APPROVAL":
        state = reply(service, project, state)
    assert state.gate.generation_blocked
    body_refs = [
        ref
        for ref in state.artifact_refs
        if service.artifacts.get(project, ref).section == "manuscript"
    ]
    assert len(body_refs) == 1
    next_gate = reply(service, project, state, "REGENERATE", "按现有大纲审阅")
    assert next_gate.gate.stage == "MANUSCRIPT"
    assert sum(stage == "manuscript" for stage, _ in writer.calls) == 1
    assert body_refs[0] in next_gate.artifact_refs
    exited = reply(service, project, next_gate, "EXIT")
    assert exited.status == "CANCELLED"


def test_generation_decision_exit_before_first_artifact(tmp_path):
    project = build_project_service().create_project(tmp_path, "Exit", "Main")
    service = build_create_chapter_workflow(Writer("summary"))
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="章",
        instruction="创作",
    )
    state = service.start(project, request)
    done = reply(service, project, state, "EXIT")
    assert done.status == "CANCELLED" and not done.result.artifact_refs


@pytest.mark.parametrize("batch", [False, True])
def test_manual_edit_at_generation_decision_is_read_on_retry(tmp_path, batch):
    project = build_project_service().create_project(tmp_path, "ManualRetry", "Main")
    writer = Writer("review")
    contexts = []
    original = writer.generate

    def recording(stage, instruction, context, **kwargs):
        contexts.append((stage, context))
        return original(stage, instruction, context, **kwargs)

    writer.generate = recording
    factory = build_multi_chapter_workflow if batch else build_create_chapter_workflow
    service = factory(writer)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        kind="MULTI_CHAPTER" if batch else "CREATE_CHAPTER",
        title="章",
        instruction="创作",
        chapters=(ChapterTarget(title="一"), ChapterTarget(title="二")) if batch else (),
    )
    state = service.start(project, request)
    while state.status == "WAITING_APPROVAL":
        state = reply(service, project, state)
    assert state.gate.generation_blocked
    chapter_id = request.chapters[0].chapter_id if batch else request.chapter_id
    record = service.drafts.record(project, request.session_id, request.task_id, chapter_id)
    path = project.root / record["path"]
    start = "<!-- novel-agent:manuscript:start -->\n"
    end = "\n<!-- novel-agent:manuscript:end -->"
    before, remaining = path.read_text(encoding="utf-8").split(start, 1)
    _, after = remaining.split(end, 1)
    manual = "只改变最后一句的人工正文"
    path.write_text(before + start + manual + end + after, encoding="utf-8")
    reopened = factory(writer)
    next_gate = reply(reopened, project, state, "REGENERATE", "采用手动正文重新审阅")
    assert next_gate.gate.stage == "MANUSCRIPT"
    assert manual in contexts[-1][1]
    assert sum(stage == "manuscript" for stage, _ in writer.calls) == 1
    body = reopened.artifacts.get(project, next_gate.gate.artifact_refs[0])
    assert body.content == manual and body.ref.revision == 2


def test_generation_gate_needs_explicit_choice_and_question_cannot_resume(tmp_path):
    from novel_agent.agent.main import MainAgent

    project = build_project_service().create_project(tmp_path, "Choice", "Main")
    service = build_create_chapter_workflow(Writer("summary"))
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="章",
        instruction="创作",
    )
    state = service.start(project, request)
    assert MainAgent.gate_response(state, "y") is None
    assert MainAgent.gate_response(state, "为什么需要补充角色身份？") is None
    answer = MainAgent.gate_response(state, "采用守塔人身份，重新生成")
    assert answer.action == "REGENERATE" and answer.feedback


def test_model_decision_payload_is_not_saved_as_artifact(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from novel_agent.workflow.generator import LLMChapterGenerator

    replies = iter(
        ['{"status":"NEEDS_DECISION","reason":"缺少必要职业"}', "概述", "大纲", "正文", "报告"]
    )
    fake = SimpleNamespace(invoke=lambda prompt: SimpleNamespace(content=next(replies)))
    monkeypatch.setattr("novel_agent.workflow.generator.build_chat_model", lambda *args: fake)
    project = build_project_service().create_project(tmp_path, "Protocol", "Main")
    service = build_create_chapter_workflow(LLMChapterGenerator(None))
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="章",
        instruction="创作",
        auto_approval=True,
    )
    waiting = service.start(project, request)
    assert waiting.gate.reason == "缺少必要职业" and waiting.gate.generation_blocked
    assert not waiting.artifact_refs and not build_project_service().list_chapters(project)
    done = reply(service, project, waiting, "REGENERATE", "采用守塔人")
    assert done.status == "COMPLETED"
    assert build_project_service().read_chapter(project, request.chapter_id).manuscript == "正文"
