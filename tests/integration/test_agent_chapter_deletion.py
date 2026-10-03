from uuid import uuid4

import pytest

from novel_agent.agent.freeform import (
    FreeformAgent,
    _chapter_delete_baselines,
    _requested_chapter_deletion,
)
from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
    build_session_service,
)
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.domain.errors import DecisionRequired, InvalidChange
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway


def _agent_and_project(tmp_path):
    projects = build_project_service()
    project = projects.create_project(tmp_path, "Deletion", "Main")
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake", "https://example.invalid", "unused")},
        "fake",
    )
    agent = FreeformAgent(
        projects, build_session_service(), build_artifact_service(), build_commit_service(), models
    )
    first, second, third = uuid4(), uuid4(), uuid4()
    agent.commits.commit(
        project,
        (
            CreateChapter("第一章", chapter_id=first),
            SetSection(first, "manuscript", text="前章正文。"),
            CreateChapter("第三章", chapter_id=third),
            SetSection(third, "manuscript", text="后章正文。"),
            CreateChapter("第二章", position=1, chapter_id=second),
            SetSection(second, "manuscript", text="待删除正文。"),
        ),
    )
    return agent, agent.projects.open_project(project.root), (first, second, third)


def test_delete_second_chapter_uses_order_and_rebuilds_derived_view(tmp_path):
    agent, project, (first, second, third) = _agent_and_project(tmp_path)
    assert project.metadata.chapters[str(second)].filename == "0002.md"
    assert _requested_chapter_deletion("删除第二章", project, agent.projects) == second
    baselines = _chapter_delete_baselines(project)
    commit, title, filename = agent.commit_chapter_deletion(project, second, baselines)

    current = agent.projects.open_project(project.root)
    assert (title, filename) == ("第二章", "0002.md")
    assert current.metadata.chapter_order == (first, third)
    assert current.metadata.chapters[str(third)].filename == "0002.md"
    assert not (project.root / "chapters/0003.md").exists()
    assert agent.projects.read_chapter(current, third).manuscript == "后章正文。"
    manuscript = (project.root / "MANUSCRIPT.md").read_text(encoding="utf-8")
    assert "待删除正文" not in manuscript
    assert "前章正文" in manuscript and "后章正文" in manuscript
    assert f"chapters/{filename}" in commit.changed_files
    assert "MANUSCRIPT.md" in commit.changed_files
    assert not commit.committed_artifacts


def test_delete_rejects_stale_project_without_removing_chapter(tmp_path):
    agent, project, (first, second, _) = _agent_and_project(tmp_path)
    baselines = _chapter_delete_baselines(project)
    agent.commits.commit(project, (SetSection(first, "manuscript", text="外部更新。"),))

    with pytest.raises(DecisionRequired, match="Project changed"):
        agent.commit_chapter_deletion(project, second, baselines)
    current = agent.projects.open_project(project.root)
    assert second in current.metadata.chapter_order
    assert (project.root / "chapters/0002.md").exists()


def test_delete_intent_must_identify_a_whole_chapter(tmp_path):
    agent, project, (_, second, _) = _agent_and_project(tmp_path)

    def resolve(prompt):
        return _requested_chapter_deletion(prompt, project, agent.projects)

    assert resolve("把第二章删掉，并告诉我结果？") == second
    assert resolve("删除章节《第二章》") == second
    assert resolve(f"删除章节 {second}") == second
    assert resolve("不要删除第二章") is None
    assert resolve("删除第二章，先给方案，不要执行") is None
    assert resolve("删除第二章中的一段文字") is None
    assert resolve("请问可以删除第二章吗？") is None
    with pytest.raises(InvalidChange, match="does not exist"):
        resolve("删除第四章")
