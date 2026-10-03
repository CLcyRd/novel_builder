import asyncio

from langchain_core.messages import AIMessage

from novel_agent.agent import freeform
from novel_agent.application import (
    build_create_chapter_workflow,
    build_import_novel_workflow,
    build_project_service,
    build_session_service,
)
from novel_agent.cli.shell import NovelCLI
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.services.skill import SkillService
from novel_agent.workflow.models import GateResponse, WorkflowRequest


def _models():
    return ModelGateway(
        {"fake": ModelDefinition("fake", "fake-model", "https://example.invalid/v1", "none")},
        "fake",
    )


def test_cli_skill_settings_and_freeform_virtual_files(monkeypatch, tmp_path):
    cli = NovelCLI(tmp_path / "workspace", models=_models())
    asyncio.run(cli.execute("/project create SkillProject"))
    project, session = cli._selected()
    skill_root = project.root / "skills" / "quiet-scenes"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: quiet-scenes\ndescription: 安静场景\n---\n少用旁白。", encoding="utf-8"
    )
    assert "disabled" in asyncio.run(cli.execute("/skill list")).text
    asyncio.run(cli.execute("/skill enable quiet-scenes"))
    asyncio.run(cli.execute("/skill use quiet-scenes"))
    assert cli.sessions.load(project, session.session_id).next_skill == "quiet-scenes"
    captured = []

    class FakeAgent:
        async def astream(self, payload, **_kwargs):
            captured.append(payload)
            yield "updates", {"agent": {"messages": [AIMessage(content="回答")]}}

    def fake_create(**kwargs):
        captured.append(kwargs)
        return FakeAgent()

    monkeypatch.setattr(freeform, "build_chat_model", lambda *_: object())
    monkeypatch.setattr(freeform, "create_deep_agent", fake_create)
    assert asyncio.run(cli.execute("你好")).text == "回答"
    assert captured[0]["skills"] == ["/skills/"]
    assert "/skills/quiet-scenes/SKILL.md" in captured[1]["files"]
    assert cli.sessions.load(project, session.session_id).next_skill is None
    assert not build_project_service().list_chapters(project)


def test_explicit_no_skill_instruction_overrides_next_skill(monkeypatch, tmp_path):
    cli = NovelCLI(tmp_path / "workspace", models=_models())
    asyncio.run(cli.execute("/project create NoSkill"))
    project, session = cli._selected()
    root = project.root / "skills" / "marker-style"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: marker-style\ndescription: 固定开头。\n---\nSKILL_MARKER",
        encoding="utf-8",
    )
    asyncio.run(cli.execute("/skill enable marker-style"))
    asyncio.run(cli.execute("/skill use marker-style"))
    captured = []

    class FakeAgent:
        async def astream(self, payload, **_kwargs):
            captured.append(payload)
            yield "updates", {"agent": {"messages": [AIMessage(content="可以协助写作。")]}}

    def fake_create(**kwargs):
        captured.append(kwargs)
        return FakeAgent()

    monkeypatch.setattr(freeform, "build_chat_model", lambda *_: object())
    monkeypatch.setattr(freeform, "create_deep_agent", fake_create)
    answer = asyncio.run(cli.execute("本次明确不要使用 Skill 的固定开头。可以协助写作吗？"))
    assert answer.text == "可以协助写作。"
    assert captured[0]["skills"] is None
    assert "files" not in captured[1]
    assert cli.sessions.load(project, session.session_id).next_skill is None


def test_workflow_uses_frozen_skill_after_source_changes(tmp_path):
    project = build_project_service().create_project(tmp_path, "WorkflowSkill", "Main")
    root = project.root / "skills" / "quiet-scenes"
    root.mkdir(parents=True)
    file = root / "SKILL.md"
    file.write_text(
        "---\nname: quiet-scenes\ndescription: 安静场景\n---\n少用旁白。", encoding="utf-8"
    )
    skills = SkillService()
    ref = skills.snapshot(project, skills.get(project, "quiet-scenes"))

    class Generator:
        contexts = []

        def generate(self, stage, instruction, context, *, feedback="", model=None):
            self.contexts.append((stage, context))
            return f"{stage} 内容"

    generator = Generator()
    workflow = build_create_chapter_workflow(generator)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="第一章",
        instruction="创作第一章",
        skill_refs=(ref,),
    )
    waiting = workflow.start(project, request)
    file.write_text(
        "---\nname: quiet-scenes\ndescription: 安静场景\n---\n增加旁白。", encoding="utf-8"
    )
    reopened = build_create_chapter_workflow(generator)
    next_gate = reopened.resume(
        project, request.workflow_id, GateResponse(gate_id=waiting.gate.gate_id, action="APPROVE")
    )
    assert next_gate.gate.stage == "OUTLINE"
    next_gate = reopened.resume(
        project, request.workflow_id, GateResponse(gate_id=next_gate.gate.gate_id, action="APPROVE")
    )
    assert next_gate.gate.stage == "MANUSCRIPT"
    assert all(
        "少用旁白" in context and "增加旁白" not in context for _, context in generator.contexts
    )
    artifact = reopened.artifacts.get(project, next_gate.gate.artifact_refs[0])
    assert artifact.skill_refs == (ref,)


def test_import_skill_never_changes_source_manuscript(tmp_path):
    project = build_project_service().create_project(tmp_path, "ImportSkill", "Main")
    source = project.root / "sources" / "novel.txt"
    source.write_text("第一章\n雨停后，林舟收起伞。", encoding="utf-8")
    root = project.root / "skills" / "quiet-scenes"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: quiet-scenes\ndescription: 安静场景\n---\n增加环境细节。",
        encoding="utf-8",
    )
    skill = SkillService()
    ref = skill.snapshot(project, skill.get(project, "quiet-scenes"))
    build_session_service().update_settings(
        project, project.sessions[0].session_id, auto_approval=True
    )

    class Generator:
        def generate(self, stage, instruction, context, *, feedback="", model=None):
            assert "增加环境细节" in context
            return f"{stage} 内容"

    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        kind="IMPORT_NOVEL",
        title="导入小说",
        instruction="导入小说",
        source_path="sources/novel.txt",
        skill_refs=(ref,),
    )
    done = build_import_novel_workflow(Generator()).start(project, request)
    assert done.status == "COMPLETED"
    chapter = build_project_service().read_chapter(project, done.result.chapter_ids[0])
    assert chapter.manuscript == "雨停后，林舟收起伞。"
