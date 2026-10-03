import asyncio
import os
from pathlib import Path

import pytest

from novel_agent.cli.shell import NovelCLI
from novel_agent.infrastructure.models.gateway import ModelGateway

pytestmark = pytest.mark.real_llm


@pytest.fixture
def cli(tmp_path):
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    gateway = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    instance = NovelCLI(tmp_path / "workspace", models=gateway)
    asyncio.run(instance.execute("/project create WritingMethods"))
    print(f"model={gateway.definition().model_id}")
    return instance


def test_live_builtin_dialogue_skill_is_read_and_saved_with_freeform_artifact(cli):
    asyncio.run(cli.execute("/skill use dialogue-writing"))
    result = asyncio.run(
        cli.execute(
            "先用 read_file 读取 /skills/dialogue-writing/SKILL.md 及其 examples.md 参考文件，"
            "为新章节《借款》写精简大纲：两位朋友讨论还款，人物可以直接说诉求。"
            "调用 create_working_artifact 保存 outline，不要提交。"
        )
    )
    assert "read_file" in result.run.tool_calls
    assert result.run.artifacts and result.run.draft_paths and not result.run.commits
    project, _session = cli._selected()
    artifact = cli.artifacts.get(project, result.run.artifacts[-1])
    assert artifact.content.strip() and artifact.section == "outline"
    assert any(ref.name == "dialogue-writing" for ref in artifact.skill_refs)
    assert not cli.projects.list_chapters(project)


def test_live_builtin_viewpoint_skill_uses_stage_scope_and_survives_reopen(cli):
    asyncio.run(cli.execute("/mode workflow"))
    asyncio.run(cli.execute("/skill use viewpoint-control"))
    first = asyncio.run(
        cli.execute(
            "创作第一章，明确使用全知视角，正文约80字：两人在桥两侧等待，"
            "叙述者可以交代双方的想法。概述和大纲保持精简。"
        )
    )
    project, session = cli._selected()
    snapshot = first.run.workflow_snapshot
    assert snapshot.gate.stage == "SUMMARY" and not cli.projects.list_chapters(project)
    summary = cli.artifacts.get(project, snapshot.gate.artifact_refs[0])
    assert not any(ref.name == "viewpoint-control" for ref in summary.skill_refs)
    outline_gate = asyncio.run(cli.execute("y"))
    assert outline_gate.run.workflow_snapshot.gate.stage == "OUTLINE"
    outline = cli.artifacts.get(project, outline_gate.run.workflow_snapshot.gate.artifact_refs[0])
    used = next(ref for ref in outline.skill_refs if ref.name == "viewpoint-control")
    reopened = NovelCLI(cli.workspace, models=cli.models)
    asyncio.run(reopened.execute("/project open WritingMethods"))
    asyncio.run(reopened.execute("/session open Main"))
    second = asyncio.run(reopened.execute("y"))
    assert second.run.workflow_snapshot.gate.stage == "MANUSCRIPT"
    body = reopened.artifacts.get(project, second.run.workflow_snapshot.gate.artifact_refs[0])
    assert body.content.strip() and used in body.skill_refs
    assert reopened.agent.load_active(project, session).workflow_id == snapshot.workflow_id
    done = asyncio.run(reopened.execute("y"))
    assert done.run.workflow_snapshot.status == "COMPLETED"
    assert (
        reopened.projects.read_chapter(
            project, done.run.workflow_snapshot.result.chapter_id
        ).manuscript
        == body.content
    )
