import asyncio
import os
from pathlib import Path

import pytest

from novel_agent.application import build_create_chapter_workflow, build_project_service
from novel_agent.cli.shell import NovelCLI
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.workflow.generator import LLMChapterGenerator
from novel_agent.workflow.models import GateResponse, WorkflowRequest

pytestmark = pytest.mark.real_llm


@pytest.fixture
def models():
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    gateway = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    print(f"draft_model={gateway.definition().model_id}")
    return gateway


def edit_section(path, section, text):
    start = f"<!-- novel-agent:{section}:start -->\n"
    end = f"\n<!-- novel-agent:{section}:end -->"
    content = path.read_text(encoding="utf-8")
    before, remainder = content.split(start, 1)
    _old, after = remainder.split(end, 1)
    path.write_text(before + start + text + end + after, encoding="utf-8", newline="\n")


def test_live_workflow_manual_edits_resume_and_exact_commit(models, tmp_path):
    project = build_project_service().create_project(tmp_path, "LiveDraft", "Main")
    workflow = build_create_chapter_workflow(LLMChapterGenerator(models))
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="雨夜",
        instruction="创作简短悬疑章节，正文不超过100字，概述与大纲精简。",
    )
    first = workflow.start(project, request)
    assert first.gate.stage == "SUMMARY" and len(first.artifact_refs) == 1
    record = workflow.drafts.record(
        project, request.session_id, request.task_id, request.chapter_id
    )
    path = project.root / record["path"]
    summary = "林舟在雨夜找到蓝色铜钥匙，决定离开钟楼。"
    edit_section(path, "summary", summary)
    first = workflow.resume(
        project, request.workflow_id, GateResponse(gate_id=first.gate.gate_id, action="APPROVE")
    )
    assert first.gate.stage == "OUTLINE" and path.exists()
    outline_candidate = workflow.artifacts.get(project, first.gate.artifact_refs[0])
    assert any(
        workflow.artifacts.get(project, ref).content == summary
        for ref in outline_candidate.dependencies
    )
    outline = "## 场景规划\n林舟在雨夜取走蓝色铜钥匙，随后离开钟楼。"
    edit_section(path, "outline", outline)
    second = workflow.resume(
        project, request.workflow_id, GateResponse(gate_id=first.gate.gate_id, action="APPROVE")
    )
    assert second.gate.stage == "MANUSCRIPT" and path.exists()
    body = workflow.artifacts.get(project, second.gate.artifact_refs[0])
    assert body.content.strip()
    assert any(workflow.artifacts.get(project, ref).content == outline for ref in body.dependencies)
    manual_body = "林舟拿起蓝色铜钥匙。雨落下来，他离开钟楼。"
    edit_section(path, "manuscript", manual_body)
    reopened = build_create_chapter_workflow(LLMChapterGenerator(models))
    done = reopened.resume(
        project, request.workflow_id, GateResponse(gate_id=second.gate.gate_id, action="APPROVE")
    )
    assert done.status == "COMPLETED" and not path.exists()
    chapter = build_project_service().read_chapter(project, request.chapter_id)
    assert (
        chapter.summary == summary
        and chapter.outline == outline
        and chapter.manuscript == manual_body
    )


def test_live_freeform_manual_confirm_and_explicit_commit(models, tmp_path):
    cli = NovelCLI(tmp_path / "workspace", models=models)
    asyncio.run(cli.execute("/project create FreeformDraft"))
    first = asyncio.run(cli.execute("为新章节《雨夜》生成精简大纲，保存为 Artifact，不要提交。"))
    assert first.run.artifacts and first.run.draft_paths and not first.artifact_text
    project, _session = cli._selected()
    path = project.root / first.run.draft_paths[0]
    edit_section(path, "outline", "林舟拿走蓝色铜钥匙后离开钟楼。")
    asyncio.run(cli.execute("y"))
    second = asyncio.run(
        cli.execute(
            "根据刚确认的大纲，为同一个 chapter_id 写不超过80字的正文，保存工作稿，不要提交。"
        )
    )
    assert second.run.artifacts and second.run.draft_paths == first.run.draft_paths
    edit_section(path, "manuscript", "人工正文：林舟拿走蓝色铜钥匙，离开钟楼。")
    approved = asyncio.run(cli.execute("y"))
    assert "正文 draft 已删除" in approved.text and not path.exists()
    assert not cli.projects.list_chapters(project)
    asyncio.run(cli.execute("/commit current"))
    current = cli.projects.open_project(project.root)
    chapter = cli.projects.read_chapter(current, current.metadata.chapter_order[0])
    assert chapter.outline == "林舟拿走蓝色铜钥匙后离开钟楼。"
    assert chapter.manuscript == "人工正文：林舟拿走蓝色铜钥匙，离开钟楼。"
