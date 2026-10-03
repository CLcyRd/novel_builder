"""Real-model gate for explicit formal Chapter deletion."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from novel_agent.agent.main import MainAgent
from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
    build_session_service,
)
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.infrastructure.models.gateway import ModelGateway

pytestmark = pytest.mark.real_llm


def test_live_agent_deletes_second_formal_chapter(tmp_path):
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    models = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    projects = build_project_service()
    project = projects.create_project(tmp_path, "Delete Live", "Main")
    first, second, third = uuid4(), uuid4(), uuid4()
    commits = build_commit_service()
    commits.commit(
        project,
        (
            CreateChapter("第一章", chapter_id=first),
            SetSection(first, "manuscript", text="第一章的正式正文。"),
            CreateChapter("第二章", chapter_id=second),
            SetSection(second, "manuscript", text="第二章的正式正文。"),
            CreateChapter("第三章", chapter_id=third),
            SetSection(third, "manuscript", text="第三章的正式正文。"),
        ),
    )
    agent = MainAgent(
        projects, build_session_service(), build_artifact_service(), commits, models
    )
    session = project.sessions[0]
    result = asyncio.run(agent.run(project, session, "删除第二章"))

    current = projects.open_project(project.root)
    assert "delete_formal_chapter" in result.tool_calls
    assert len(result.commits) == 1
    assert str(second) in result.response and str(result.commits[0].commit_id) in result.response
    assert "chapters/0002.md" in result.response
    assert current.metadata.chapter_order == (first, third)
    assert current.metadata.chapters[str(third)].filename == "0002.md"
    assert not (project.root / "chapters/0003.md").exists()
    assert "第二章的正式正文" not in (project.root / "MANUSCRIPT.md").read_text(encoding="utf-8")
    assert projects.read_chapter(current, first).manuscript == "第一章的正式正文。"
    assert projects.read_chapter(current, third).manuscript == "第三章的正式正文。"
    assert not agent.artifacts.list(current, session.session_id)
    print(f"model={result.model} tools={result.tool_calls} commit={result.commits[0].commit_id}")
