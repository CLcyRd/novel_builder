import asyncio

import pytest
from langchain_core.messages import AIMessage

from novel_agent.agent import freeform
from novel_agent.application import (
    build_create_chapter_workflow,
    build_import_novel_workflow,
    build_multi_chapter_workflow,
    build_project_service,
)
from novel_agent.domain.artifact import ArtifactType
from novel_agent.domain.errors import DecisionRequired, InvalidChapter, UnsafeProjectPath
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.workflow.models import ChapterTarget, GateResponse, WorkflowRequest


class Generator:
    def __init__(self):
        self.calls = []

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, context))
        return f"{stage} 原稿 {len(self.calls)}"


def project_at(tmp_path):
    return build_project_service().create_project(tmp_path, "Drafts", "Main")


def draft_path(service, project, request, chapter_id=None):
    record = service.drafts.record(
        project, request.session_id, request.task_id, chapter_id or request.chapter_id
    )
    return project.root / record["path"]


def edit(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def respond(service, project, snapshot, action="APPROVE"):
    return service.resume(
        project, snapshot.workflow_id, GateResponse(gate_id=snapshot.gate.gate_id, action=action)
    )


def test_workflow_uses_hand_edited_plan_and_commits_exact_body_after_reopen(tmp_path):
    project = project_at(tmp_path)
    generator = Generator()
    service = build_create_chapter_workflow(generator)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="第一章",
        instruction="创作",
    )
    first = service.start(project, request)
    first = respond(service, project, first)
    path = draft_path(service, project, request)
    original = service.artifacts.get(project, first.gate.artifact_refs[0])
    edit(path, original.content, "人工大纲：只修改这一处。")
    second = respond(service, project, first)
    assert "人工大纲：只修改这一处。" in generator.calls[2][1]
    assert path.exists()
    body = service.artifacts.get(project, second.gate.artifact_refs[0])
    edit(path, body.content, "人工正文，必须逐字保留。")
    reopened = build_create_chapter_workflow(generator)
    done = respond(reopened, project, reopened.load(project, request.workflow_id))
    assert done.status == "COMPLETED"
    assert not path.exists()
    chapter = build_project_service().read_chapter(project, request.chapter_id)
    assert chapter.outline == "人工大纲：只修改这一处。"
    assert chapter.manuscript == "人工正文，必须逐字保留。"
    assert sum(stage == "manuscript" for stage, _ in generator.calls) == 1
    assert sum(stage == "review" for stage, _ in generator.calls) == 2
    assert service.artifacts.get(project, original.ref).content == original.content
    assert not any("working/" in name for name in done.result.project_changes)


def test_changed_approved_plan_requires_explicit_decision_even_under_auto(tmp_path):
    project = project_at(tmp_path)
    service = build_create_chapter_workflow(Generator())
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="章",
        instruction="创作",
    )
    plan = service.start(project, request)
    plan = respond(service, project, plan)
    body = respond(service, project, plan)
    path = draft_path(service, project, request)
    outline = service.artifacts.get(project, plan.gate.artifact_refs[0]).content
    edit(path, outline, "人工修改已经批准的大纲")
    decision = respond(service, project, body)
    assert decision.status == "WAITING_DECISION" and "KEEP" in decision.gate.options
    assert path.exists() and not build_project_service().list_chapters(project)
    service.sessions.update_settings(project, request.session_id, auto_approval=True)
    with pytest.raises(Exception, match="Approval Gate"):
        service.apply_auto(project, request.workflow_id)
    done = respond(service, project, decision, "KEEP")
    assert any(item.stage == "OUTLINE" and item.source == "AUTO" for item in done.approvals)
    assert done.status == "COMPLETED" and not path.exists()


def test_multichapter_manual_body_is_context_for_next_chapter(tmp_path):
    project = project_at(tmp_path)
    generator = Generator()
    service = build_multi_chapter_workflow(generator)
    request = WorkflowRequest(
        kind="MULTI_CHAPTER",
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="两章",
        instruction="创作两章",
        chapters=(ChapterTarget(title="一"), ChapterTarget(title="二")),
    )
    state = service.start(project, request)
    for _ in range(3):
        state = respond(service, project, state)
    assert state.gate.stage == "MANUSCRIPT"
    first_id = request.chapters[0].chapter_id
    path = draft_path(service, project, request, first_id)
    body = service.artifacts.get(project, state.gate.artifact_refs[0])
    edit(path, body.content, "人工前章：铜钥匙已交给林舟。")
    state = respond(service, project, state)
    assert not path.exists()
    assert "人工前章：铜钥匙已交给林舟。" in next(
        context for stage, context in reversed(generator.calls) if stage == "manuscript"
    )
    done = respond(service, project, state)
    assert done.status == "COMPLETED"


def test_import_preserves_source_and_saves_user_edits_without_regenerating_body(tmp_path):
    project = project_at(tmp_path)
    source = project.root / "sources" / "novel.txt"
    source.write_text("第一章 开始\n源文件正文。", encoding="utf-8")
    service = build_import_novel_workflow(Generator())
    request = WorkflowRequest(
        kind="IMPORT_NOVEL",
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="导入",
        instruction="导入",
        source_path="sources/novel.txt",
    )
    state = service.start(project, request)
    chapter_id = next(
        service.artifacts.get(project, ref).chapter_id
        for ref in state.artifact_refs
        if service.artifacts.get(project, ref).section == "manuscript"
    )
    path = draft_path(service, project, request, chapter_id)
    edit(path, "源文件正文。", "用户修改导入正文。")
    state = respond(service, project, state)
    done = respond(service, project, state)
    assert done.status == "COMPLETED" and not path.exists()
    assert source.read_text(encoding="utf-8") == "第一章 开始\n源文件正文。"
    assert (
        build_project_service().read_chapter(project, chapter_id).manuscript == "用户修改导入正文。"
    )


def test_freeform_manual_confirm_delete_and_later_commit(monkeypatch, tmp_path):
    from novel_agent.cli.shell import NovelCLI

    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake-model", "https://example.invalid/v1", "none")},
        "fake",
    )
    cli = NovelCLI(tmp_path / "workspace", models=models)
    asyncio.run(cli.execute("/project create Draft"))
    project, session = cli._selected()
    chapter_id = None

    def create_agent(**kwargs):
        tools = {item.name: item for item in kwargs["tools"]}

        class FakeAgent:
            async def astream(self, payload, **_kwargs):
                nonlocal chapter_id
                section = "outline" if chapter_id is None else "manuscript"
                tools["create_working_artifact"].invoke(
                    {
                        "content": "大纲原稿" if section == "outline" else "正文原稿",
                        "section": section,
                        "chapter": "new" if chapter_id is None else str(chapter_id),
                        "title": "新章",
                    }
                )
                yield "updates", {"agent": {"messages": [AIMessage(content="已生成")]}}

        return FakeAgent()

    monkeypatch.setattr(freeform, "build_chat_model", lambda *_: object())
    monkeypatch.setattr(freeform, "create_deep_agent", create_agent)
    outline = asyncio.run(cli.execute("生成大纲，不要提交"))
    assert not outline.artifact_text and outline.run.draft_paths
    chapter_id = cli.artifacts.get(project, outline.run.artifacts[0]).chapter_id
    path = project.root / outline.run.draft_paths[0]
    edit(path, "大纲原稿", "手动大纲")
    asyncio.run(cli.execute("y"))
    assert path.exists()
    body = asyncio.run(cli.execute("生成正文，不要提交"))
    assert body.run.draft_paths[0] == outline.run.draft_paths[0]
    edit(path, "正文原稿", "手动正文")
    approved = asyncio.run(cli.execute("y"))
    assert "正文 draft 已删除" in approved.text and not path.exists()
    assert not cli.projects.list_chapters(project)
    asyncio.run(cli.execute("/commit current"))
    chapter = cli.projects.read_chapter(project, chapter_id)
    assert chapter.manuscript == "手动正文" and chapter.outline == "手动大纲"


def test_invalid_draft_never_approves_or_writes_formal_project(tmp_path):
    project = project_at(tmp_path)
    service = build_create_chapter_workflow(Generator())
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="章",
        instruction="创作",
    )
    state = service.start(project, request)
    path = draft_path(service, project, request)
    edit(path, "<!-- novel-agent:outline:end -->", "破坏边界")
    with pytest.raises(InvalidChapter):
        respond(service, project, state)
    assert service.load(project, request.workflow_id).gate.gate_id == state.gate.gate_id
    assert not build_project_service().list_chapters(project)


def test_auto_does_not_approve_manual_edits_and_exit_preserves_file(tmp_path):
    project = project_at(tmp_path)
    service = build_create_chapter_workflow(Generator())
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="章",
        instruction="创作",
    )
    plan = service.start(project, request)
    path = draft_path(service, project, request)
    summary = service.artifacts.get(project, plan.gate.artifact_refs[0]).content
    edit(path, summary, "自动审批不能代替确认这处编辑")
    service.sessions.update_settings(project, request.session_id, auto_approval=True)
    decision = service.apply_auto(project, request.workflow_id)
    assert decision.status == "WAITING_DECISION"
    assert not build_project_service().list_chapters(project)
    exited = respond(service, project, decision, "EXIT")
    assert exited.status == "CANCELLED" and path.exists()


def test_dirty_draft_during_generation_requires_choice_and_keeps_user_text(tmp_path):
    project = project_at(tmp_path)
    generator = Generator()
    service = build_create_chapter_workflow(generator)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="章",
        instruction="创作",
    )
    plan = service.start(project, request)
    plan = respond(service, project, plan)
    path = draft_path(service, project, request)
    old = service.artifacts.get(project, plan.gate.artifact_refs[0]).content
    original_generate = generator.generate

    def concurrent_edit(stage, *args, **kwargs):
        if stage == "outline":
            edit(path, old, "模型生成期间的人工修改")
        return original_generate(stage, *args, **kwargs)

    generator.generate = concurrent_edit
    decision = respond(service, project, plan, "REJECT")
    assert decision.status == "WAITING_DECISION"
    assert "模型生成期间的人工修改" in path.read_text(encoding="utf-8")
    generator.generate = original_generate
    body = respond(service, project, decision, "KEEP")
    done = respond(service, project, body)
    assert (
        build_project_service().read_chapter(project, request.chapter_id).outline
        == "模型生成期间的人工修改"
    )
    assert done.status == "COMPLETED"


def test_missing_draft_cannot_be_used_for_confirmation(tmp_path):
    project = project_at(tmp_path)
    service = build_create_chapter_workflow(Generator())
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="章",
        instruction="创作",
    )
    state = service.start(project, request)
    path = draft_path(service, project, request)
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(Exception, match="工作稿文件缺失"):
        respond(service, project, state)
    path.write_bytes(original)
    assert respond(service, project, state).gate.stage == "OUTLINE"


def freeform_candidate(tmp_path):
    from uuid import uuid4

    from novel_agent.cli.shell import NovelCLI

    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake-model", "https://example.invalid/v1", "none")},
        "fake",
    )
    cli = NovelCLI(tmp_path / "workspace", models=models)
    asyncio.run(cli.execute("/project create Draft"))
    project, session = cli._selected()
    chapter_id = uuid4()
    artifact = cli.artifacts.create(
        project,
        session.session_id,
        ArtifactType.MANUSCRIPT,
        "SECTION",
        "正文原稿",
        chapter_id=chapter_id,
        section="manuscript",
    )
    store, state = cli.agent._state(project, session)
    state.draft_titles[str(chapter_id)] = "新章"
    path = cli.agent._export_freeform_draft(project, session, state, artifact.ref)
    state.display(artifact.ref)
    store.save(session.session_id, state)
    return cli, project, session, artifact, project.root / path


def test_freeform_direct_commit_syncs_file_without_confirming_first(tmp_path):
    cli, project, _session, artifact, path = freeform_candidate(tmp_path)
    edit(path, "正文原稿", "手动修改，/exit 只是正文。")
    result = asyncio.run(cli.execute("/commit current"))
    assert "completed" in result.text and not path.exists()
    assert cli.projects.read_chapter(project, artifact.chapter_id).manuscript == (
        "手动修改，/exit 只是正文。"
    )
    assert cli.artifacts.get(project, artifact.ref).content == "正文原稿"


def test_freeform_historical_commit_never_silently_substitutes_dirty_file(tmp_path):
    cli, project, _session, artifact, path = freeform_candidate(tmp_path)
    edit(path, "正文原稿", "不能替换精确指定版本")
    with pytest.raises(DecisionRequired, match="指定 revision"):
        asyncio.run(cli.execute(f"/commit {artifact.artifact_id}@1"))
    assert path.exists() and not cli.projects.list_chapters(project)
    asyncio.run(cli.execute("/commit current"))
    assert cli.projects.read_chapter(project, artifact.chapter_id).manuscript == (
        "不能替换精确指定版本"
    )


def test_freeform_exit_keeps_manual_revision_and_draft(tmp_path):
    cli, project, session, artifact, path = freeform_candidate(tmp_path)
    edit(path, "正文原稿", "退出前手动保存的版本")
    result = asyncio.run(cli.execute("e"))
    assert "已保留" in result.text and path.exists()
    state = cli.agent._state(project, session)[1]
    assert state.pending_draft is None
    assert cli.artifacts.get(project, state.current_ref).content == "退出前手动保存的版本"
    assert not cli.projects.list_chapters(project)


def test_freeform_reject_snapshots_edits_before_replacing_same_file(monkeypatch, tmp_path):
    cli, project, session, artifact, path = freeform_candidate(tmp_path)
    edit(path, "正文原稿", "人工改动基线")

    def create_agent(**kwargs):
        tools = {item.name: item for item in kwargs["tools"]}

        class FakeAgent:
            async def astream(self, _payload, **_kwargs):
                ref = cli.agent._state(project, session)[1].current_ref
                assert cli.artifacts.get(project, ref).content == "人工改动基线"
                assert f"{ref.artifact_id}@{ref.revision}" in _payload["messages"][-1].content
                tools["revise_working_artifact"].invoke(
                    {
                        "artifact_id": str(ref.artifact_id),
                        "revision": ref.revision,
                        "content": "重新生成正文",
                    }
                )
                yield "updates", {"agent": {"messages": [AIMessage(content="已修订")]}}

        return FakeAgent()

    monkeypatch.setattr(freeform, "build_chat_model", lambda *_: object())
    monkeypatch.setattr(freeform, "create_deep_agent", create_agent)
    revised = asyncio.run(cli.execute("n"))
    assert revised.run.draft_paths == [path.relative_to(project.root).as_posix()]
    assert cli_text(path) == "重新生成正文"
    assert cli.artifacts.get_by_id(project, artifact.artifact_id, 2).content == "人工改动基线"
    assert not cli.projects.list_chapters(project)


def test_draft_sync_preserves_bom_crlf_and_content_whitespace(tmp_path):
    cli, project, session, artifact, path = freeform_candidate(tmp_path)
    content = "  保留缩进\n\n尾部空行\n"
    edit(path, "正文原稿", content)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
    asyncio.run(cli.execute("y"))
    current = cli.agent._state(project, session)[1].current_ref
    assert cli.artifacts.get(project, current).content == content
    asyncio.run(cli.execute("/commit current"))
    assert cli.projects.read_chapter(project, artifact.chapter_id).manuscript == content


def test_freeform_reopen_recovers_draft_and_manual_revision(tmp_path):
    from novel_agent.cli.shell import NovelCLI

    cli, project, session, artifact, path = freeform_candidate(tmp_path)
    edit(path, "正文原稿", "重开后确认的正文")
    reopened = NovelCLI(cli.workspace, models=cli.models)
    asyncio.run(reopened.execute("/project open Draft"))
    asyncio.run(reopened.execute("/session open Main"))
    asyncio.run(reopened.execute("y"))
    current = reopened.agent._state(project, session)[1].current_ref
    assert current.revision == 2 and not path.exists()
    asyncio.run(reopened.execute("/commit current"))
    assert reopened.projects.read_chapter(project, artifact.chapter_id).manuscript == (
        "重开后确认的正文"
    )


def test_draft_sync_replay_does_not_create_duplicate_revision(tmp_path):
    cli, project, session, artifact, path = freeform_candidate(tmp_path)
    edit(path, "正文原稿", "一次人工修改")
    _store, state = cli.agent._state(project, session)
    first = cli.agent._sync_freeform_draft(project, session, state)
    second = cli.agent._sync_freeform_draft(project, session, state)
    assert first.refs == second.refs and not second.changed
    assert cli.artifacts.get_by_id(project, artifact.artifact_id, 2).content == "一次人工修改"


def test_draft_record_cannot_redirect_to_file_outside_project(tmp_path):
    import json
    from uuid import UUID

    cli, project, session, _artifact, path = freeform_candidate(tmp_path)
    state = cli.agent._state(project, session)[1]
    pending = state.pending_draft
    metadata = project.root / cli.agent.drafts._meta(
        session.session_id, UUID(pending["task_id"]), UUID(pending["chapter_id"])
    )
    record = json.loads(metadata.read_text(encoding="utf-8"))
    outside = tmp_path / "outside.md"
    outside.write_text("禁止修改外部文件", encoding="utf-8")
    record["path"] = "../../outside.md"
    metadata.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(UnsafeProjectPath):
        asyncio.run(cli.execute("y"))
    assert path.exists() and not cli.projects.list_chapters(project)
    assert outside.read_text(encoding="utf-8") == "禁止修改外部文件"


def test_cleanup_keeps_edits_saved_during_review(tmp_path):
    project = project_at(tmp_path)
    generator = Generator()
    service = build_create_chapter_workflow(generator)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="章",
        instruction="创作",
    )
    outline = respond(service, project, service.start(project, request))
    body = respond(service, project, outline)
    path = draft_path(service, project, request)
    original = service.artifacts.get(project, body.gate.artifact_refs[0]).content
    edit(path, original, "确认时的正文")
    original_generate = generator.generate

    def concurrent_edit(stage, *args, **kwargs):
        if stage == "review":
            edit(path, "确认时的正文", "确认后新增编辑，必须保留文件")
        return original_generate(stage, *args, **kwargs)

    generator.generate = concurrent_edit
    done = respond(service, project, body)
    assert done.status == "COMPLETED" and path.exists() and done.result.warnings
    assert cli_text(path) == "确认后新增编辑，必须保留文件"
    assert build_project_service().read_chapter(project, request.chapter_id).manuscript == (
        "确认时的正文"
    )


def cli_text(path):
    return (
        path.read_text(encoding="utf-8")
        .split("<!-- novel-agent:manuscript:start -->\n", 1)[1]
        .split("\n<!-- novel-agent:manuscript:end -->", 1)[0]
    )
