"""Real stage generation must pause on an explicitly non-inferable prerequisite."""

import os
from pathlib import Path

import pytest

from novel_agent.application import build_create_chapter_workflow, build_project_service
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.workflow.generator import LLMChapterGenerator
from novel_agent.workflow.models import GateResponse, WorkflowRequest

pytestmark = pytest.mark.real_llm


def test_live_missing_condition_pauses_under_auto_and_resumes_with_explicit_feedback(tmp_path):
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    gateway = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    print(f"generation_decision_model={gateway.definition().model_id}")
    projects = build_project_service()
    project = projects.create_project(tmp_path, "MissingCondition", "Main")
    def factory():
        return build_create_chapter_workflow(LLMChapterGenerator(gateway))

    service = factory()
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="雨夜",
        instruction=(
            "创作第一章，主角林舟，正文不超过80字，概述与大纲简短。"
            "必要条件：概述必须明确林舟的真实职业，且该职业必须来自 Project 已有正式正文；"
            "不允许自行创作职业，也不能省略、代称或写待定。"
            "若没有该正式事实，按公共规则请求 Decision，等待用户补充后才能继续。"
        ),
        auto_approval=True,
    )
    waiting = service.start(project, request)
    assert waiting.status == "WAITING_DECISION" and waiting.gate.generation_blocked
    assert waiting.gate.reason.strip() and not waiting.artifact_refs
    assert not projects.list_chapters(project)
    reopened = factory()
    assert reopened.load_active(project, request.session_id).gate == waiting.gate
    done = reopened.resume(
        project,
        request.workflow_id,
        GateResponse(
            gate_id=waiting.gate.gate_id,
            action="REGENERATE",
            feedback=(
                "补充条件：采用林舟是守塔人的新设定，允许创作这个职业；"
                "取消职业必须来自旧正文的要求。重新生成并继续。"
            ),
        ),
    )
    assert done.status == "COMPLETED" and done.result.commit
    chapter = projects.read_chapter(project, request.chapter_id)
    assert chapter.manuscript.strip()
    assert "NEEDS_DECISION" not in chapter.manuscript and "待定：" not in chapter.manuscript
