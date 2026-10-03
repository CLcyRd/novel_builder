"""Explicit real-model smoke for Skill reading and frozen Workflow use."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from novel_agent.application import (
    build_commit_service,
    build_continue_chapter_workflow,
    build_import_novel_workflow,
    build_multi_chapter_workflow,
    build_session_service,
)
from novel_agent.cli.shell import NovelCLI
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.services.skill import SkillService
from novel_agent.workflow.generator import LLMChapterGenerator
from novel_agent.workflow.models import ChapterTarget, GateResponse, WorkflowRequest

pytestmark = pytest.mark.real_llm


@pytest.fixture
def live(tmp_path):
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    gateway = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    cli = NovelCLI(tmp_path / "workspace", models=gateway)
    asyncio.run(cli.execute("/project create SkillLive"))
    return cli, gateway


def test_live_skill_freeform_artifact_and_workflow(live):
    cli, gateway = live
    project, session = cli._selected()
    source = project.root / "skills" / "rain-scene"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: rain-scene\ndescription: 用于雨夜小说场景，强调钟声作为环境线索。\n---\n"
        "写作时让钟声成为场景线索，但不要改变正式 Project 事实。",
        encoding="utf-8",
    )
    asyncio.run(cli.execute("/skill enable rain-scene"))
    asyncio.run(cli.execute("/skill use rain-scene"))
    freeform = asyncio.run(
        cli.execute(
            "请读取本次指定的 rain-scene Skill，并为新章节《雨夜》创作简短大纲。"
            "调用 create_working_artifact，section=outline，chapter=new，title=雨夜；"
            "保存工作稿，不要提交。"
        )
    )
    assert freeform.run.artifacts and not freeform.run.commits
    draft = cli.artifacts.get(project, freeform.run.artifacts[-1])
    assert draft.section == "outline"
    assert any(ref.name == "rain-scene" for ref in draft.skill_refs)
    assert not cli.projects.list_chapters(project)

    asyncio.run(cli.execute("/skill use rain-scene"))
    asyncio.run(cli.execute("/mode workflow"))
    waiting = asyncio.run(cli.execute("创作第一章，写一个简短的雨夜悬疑故事。"))
    assert waiting.run.workflow_snapshot.gate.stage == "SUMMARY"
    waiting = asyncio.run(cli.execute("y"))
    assert waiting.run.workflow_snapshot.gate.stage == "OUTLINE"
    first = cli.artifacts.get(project, waiting.run.workflow_snapshot.gate.artifact_refs[0])
    assert any(ref.name == "rain-scene" for ref in first.skill_refs)
    (source / "SKILL.md").write_text(
        "---\nname: rain-scene\ndescription: 新版本\n---\n不要使用钟声。",
        encoding="utf-8",
    )
    reopened = NovelCLI(cli.workspace, models=gateway)
    asyncio.run(reopened.execute("/project open SkillLive"))
    assert reopened.prompt_label() == "approve[y/n/e]> "
    manuscript = asyncio.run(reopened.execute("y"))
    assert manuscript.run.workflow_snapshot.gate.stage == "MANUSCRIPT"
    manuscript_artifact = reopened.artifacts.get(
        project, manuscript.run.workflow_snapshot.gate.artifact_refs[0]
    )
    assert manuscript_artifact.skill_refs == first.skill_refs
    print(f"model={gateway.definition().model_id} skill=rain-scene")


def test_live_explicit_user_instruction_overrides_skill_style(live):
    cli, gateway = live
    project, _ = cli._selected()
    root = project.root / "skills" / "marker-style"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: marker-style\ndescription: 用于指定简短答复的开头风格。\n---\n"
        "每次答复都以 SKILL_MARKER 开头。",
        encoding="utf-8",
    )
    asyncio.run(cli.execute("/skill enable marker-style"))
    asyncio.run(cli.execute("/skill use marker-style"))
    reply = asyncio.run(
        cli.execute(
            "本次明确不要使用 Skill 的固定开头，也不要提及它。只用一句话回答你可以协助写作吗？"
        )
    )
    assert reply.run.response.strip()
    assert "SKILL_MARKER" not in reply.run.response
    assert not cli.projects.list_chapters(project)
    print(f"model={gateway.definition().model_id} override=honored")


def test_live_skill_multi_and_import_keep_source_exact(live):
    cli, gateway = live
    project, session = cli._selected()
    root = project.root / "skills" / "scene-order"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: scene-order\ndescription: 用于多章场景顺序规划。\n---\n"
        "每章场景须有行动、阻力和结果，不改变正式 Project 事实。",
        encoding="utf-8",
    )
    catalog = SkillService()
    ref = catalog.snapshot(project, catalog.get(project, "scene-order"))
    writer = LLMChapterGenerator(gateway)
    multi = build_multi_chapter_workflow(writer)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=session.session_id,
        kind="MULTI_CHAPTER",
        title="两章",
        instruction="连续创作两章简短悬疑小说，每章约60字。",
        chapters=(ChapterTarget(title="第一章"), ChapterTarget(title="第二章")),
        skill_refs=(ref,),
    )
    summaries = multi.start(project, request)
    assert summaries.gate.stage == "SUMMARIES"
    first_outline = multi.resume(
        project,
        request.workflow_id,
        GateResponse(gate_id=summaries.gate.gate_id, action="APPROVE"),
    )
    assert first_outline.gate.stage == "OUTLINES"
    second_outline = multi.resume(
        project,
        request.workflow_id,
        GateResponse(gate_id=first_outline.gate.gate_id, action="APPROVE"),
    )
    assert second_outline.gate.stage == "OUTLINES"
    second = cli.artifacts.get(project, second_outline.gate.artifact_refs[0])
    assert second.skill_refs == (ref,)
    multi.exit(project, request.workflow_id)

    source = project.root / "sources" / "original.txt"
    source.write_text("雨后，林舟在钟楼门口找到一封信。", encoding="utf-8")
    build_session_service().update_settings(project, session.session_id, auto_approval=True)
    imported = build_import_novel_workflow(writer).start(
        project,
        WorkflowRequest(
            project_id=project.project_id,
            session_id=session.session_id,
            kind="IMPORT_NOVEL",
            title="导入小说",
            instruction="导入现有小说，生成简短概述和大纲。",
            source_path="sources/original.txt",
            skill_refs=(ref,),
        ),
    )
    assert imported.status == "COMPLETED"
    chapter = cli.projects.read_chapter(project, imported.result.chapter_ids[0])
    assert chapter.manuscript == source.read_text(encoding="utf-8")
    assert chapter.outline.strip()
    print(f"model={gateway.definition().model_id} multi_skill=used import_source=exact")


def test_live_skill_continuation_uses_formal_previous_chapter(live):
    cli, gateway = live
    project, session = cli._selected()
    chapter_id = uuid4()
    build_commit_service().commit(
        project,
        (
            CreateChapter("第一章", chapter_id=chapter_id),
            SetSection(chapter_id, "manuscript", text="林舟在雨夜收到一封信，信中提到钟楼。"),
        ),
    )
    root = project.root / "skills" / "continuity"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: continuity\ndescription: 续写时核对前章线索。\n---\n"
        "先读取前章正文，保持既定人物和线索一致。",
        encoding="utf-8",
    )
    catalog = SkillService()
    ref = catalog.snapshot(project, catalog.get(project, "continuity"))
    workflow = build_continue_chapter_workflow(LLMChapterGenerator(gateway))
    waiting = workflow.start(
        project,
        WorkflowRequest(
            project_id=project.project_id,
            session_id=session.session_id,
            kind="CONTINUE_CHAPTER",
            title="第二章",
            instruction="续写下一章，简短规划并承接前章线索。",
            skill_refs=(ref,),
        ),
    )
    assert waiting.gate.stage == "SUMMARY"
    waiting = workflow.resume(
        project,
        waiting.workflow_id,
        GateResponse(gate_id=waiting.gate.gate_id, action="APPROVE"),
    )
    assert waiting.gate.stage == "OUTLINE"
    outline = cli.artifacts.get(project, waiting.gate.artifact_refs[0])
    assert outline.skill_refs == (ref,)
    assert not any(chapter.title == "第二章" for _, chapter in cli.projects.list_chapters(project))
    print(f"model={gateway.definition().model_id} continuation_skill=used")
