"""Real-model smoke for the default chapter-outline structure."""

import asyncio
import os
from pathlib import Path

import pytest

from novel_agent.agent.freeform import FreeformAgent
from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
    build_session_service,
)
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.workflow.generator import LLMChapterGenerator

pytestmark = pytest.mark.real_llm

HEADINGS = (
    "## 背景",
    "## 情节结构",
    "## 出场人物",
    "## 场景规划",
    "## 叙事策略",
    "## 辅助资料",
)


@pytest.fixture
def gateway():
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    return ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")


def assert_default_headings(content: str) -> None:
    positions = [content.index(heading) for heading in HEADINGS]
    assert positions == sorted(positions)


def test_live_workflow_outline_first_chapter_and_continuation(gateway):
    writer = LLMChapterGenerator(gateway)
    first = writer.generate(
        "outline",
        "创作第一章：林舟在雨夜收到一封无署名的信。简短规划即可。",
        "Project 设定：悬疑小说。尚无正式章节，也没有其他参考资料。",
    )
    assert_default_headings(first)
    assert "## 前情提要" not in first

    next_chapter = writer.generate(
        "outline",
        "续写第二章：林舟追查信件来源。简短规划即可。",
        "正式前章正文：第一章中，林舟在雨夜收到一封无署名的信，信中提到钟楼。",
    )
    assert_default_headings(next_chapter)
    assert "## 前情提要" in next_chapter
    print(f"model={gateway.definition().model_id} workflow_outline=structured")


def test_live_freeform_outline_artifact_uses_default_structure(gateway, tmp_path):
    projects = build_project_service()
    project = projects.create_project(tmp_path, "Outline Smoke", "Main")
    agent = FreeformAgent(
        projects,
        build_session_service(),
        build_artifact_service(),
        build_commit_service(),
        gateway,
    )
    result = asyncio.run(
        agent.run(
            project,
            project.sessions[0],
            "请为新章节《雨夜来信》生成一份简短章节大纲，"
            "调用 create_working_artifact，section=outline，chapter=new，title=雨夜来信；"
            "保存为 Artifact，不要提交。当前 Project 尚无前章。",
        )
    )
    assert result.artifacts
    artifact = agent.artifacts.get(project, result.artifacts[-1])
    assert artifact.section == "outline"
    assert_default_headings(artifact.content)
    assert "## 前情提要" not in artifact.content
    assert not projects.list_chapters(project)
    print(f"model={result.model} freeform_outline=structured")
