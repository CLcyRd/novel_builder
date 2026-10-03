"""Real-model Workflow Skill Resolver and stage generation checks."""

import asyncio
import os
from pathlib import Path

import pytest

from novel_agent.cli.shell import NovelCLI
from novel_agent.infrastructure.models.gateway import ModelGateway

pytestmark = pytest.mark.real_llm


@pytest.fixture
def live(tmp_path):
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    gateway = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    cli = NovelCLI(tmp_path / "workspace", models=gateway)
    asyncio.run(cli.execute("/project create SkillAuto"))
    print(f"model={gateway.definition().model_id}")
    return cli


def add_skill(project, name, description, tags, stages, body):
    root = project.root / "skills" / name
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nactivation: auto\n"
        f"stages: {stages}\ntags: {tags}\n---\n{body}",
        encoding="utf-8",
    )


def test_real_model_semantically_selects_relevant_auto_skill(live):
    cli = live
    project, _ = cli._selected()
    add_skill(
        project, "clue-method",
        "侦探追查神秘信件的悬疑章节，用于梳理人物所知事实和调查进展。",
        "时序", "summary, outline", "每条调查发现应对应人物当时掌握的信息。",
    )
    add_skill(
        project, "football-method", "足球比赛的战术讲解方法。", "球赛",
        "summary, outline", "解释球员阵型。",
    )
    asyncio.run(cli.execute("/skill enable clue-method"))
    asyncio.run(cli.execute("/skill enable football-method"))
    asyncio.run(cli.execute("/mode workflow"))

    class Generator:
        def generate(self, stage, instruction, context, *, feedback="", model=None):
            return f"{stage} 候选"

    cli.agent.generator = Generator()
    result = asyncio.run(cli.execute("创作第一章，侦探追查一封神秘的匿名信。"))
    assert result.run.workflow_snapshot.kind == "CREATE_CHAPTER"
    assert [ref.name for ref in result.run.skill_refs] == ["clue-method"]
    assert not cli.projects.list_chapters(project)


def test_real_model_generation_receives_auto_skill_at_matching_stage(live):
    cli = live
    project, _ = cli._selected()
    add_skill(
        project, "clue-method", "悬疑线索写作方法。", "线索", "summary, outline",
        "让线索出现有因果，保持人物知情顺序。",
    )
    asyncio.run(cli.execute("/skill enable clue-method"))
    asyncio.run(cli.execute("/mode workflow"))
    events = []
    result = asyncio.run(
        cli.execute("创作第一章，侦探发现一条关键线索，简短规划即可。", on_event=events.append)
    )
    gate = result.run.workflow_snapshot.gate
    assert gate.stage == "SUMMARY"
    assert gate.artifact_refs
    assert all(cli.artifacts.get(project, ref).content.strip() for ref in gate.artifact_refs)
    assert all(
        [skill.name for skill in cli.artifacts.get(project, ref).skill_refs] == ["clue-method"]
        for ref in gate.artifact_refs
    )
    assert any(event.kind == "Reading" and "clue-method" in event.detail for event in events)
    assert not cli.projects.list_chapters(project)
