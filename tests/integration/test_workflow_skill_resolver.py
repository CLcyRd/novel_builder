import asyncio

import pytest

from novel_agent.agent.workflow_intent import CreativePlan
from novel_agent.cli.shell import NovelCLI
from novel_agent.domain.errors import InvalidChange
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.services.workflow_skill import SkillChoice, SkillChoices
from novel_agent.workflow.models import WorkflowRequest
from novel_agent.workflow.skill_context import skill_context


class Generator:
    def __init__(self, *, outline="outline 内容"):
        self.calls = []
        self.outline = outline

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, instruction, context))
        return self.outline if stage == "outline" else f"{stage} 内容"


class AbstainingSelector:
    def select(self, instruction, chapter_briefs, candidates, *, model=None):
        return SkillChoices()


class Plan:
    def plan(self, prompt, *, model=None):
        return CreativePlan(operation="MULTI_CHAPTER")


def make_cli(tmp_path):
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake", "https://example.invalid/v1", "none")},
        "fake",
    )
    cli = NovelCLI(tmp_path / "workspace", models=models)
    cli.agent.generator = Generator()
    cli.agent.skill_resolver.selector = AbstainingSelector()
    asyncio.run(cli.execute("/project create Story"))
    return cli


def write_skill(
    project, name, *, activation="auto", stages="", tags="", workflows="", body="方法A"
):
    root = project.root / "skills" / name
    root.mkdir(parents=True)
    file = root / "SKILL.md"
    file.write_text(
        f"---\nname: {name}\ndescription: {name} 写作方法\n"
        f"activation: {activation}\nworkflows: {workflows}\n"
        f"stages: {stages}\ntags: {tags}\n---\n{body}",
        encoding="utf-8",
    )
    return file


def test_auto_skill_uses_frozen_version_only_at_manuscript_stage(tmp_path):
    cli = make_cli(tmp_path)
    project, _ = cli._selected()
    file = write_skill(
        project, "clue-style", stages="manuscript", tags="线索", body="旧版：线索须有回响。"
    )
    asyncio.run(cli.execute("/skill enable clue-style"))
    asyncio.run(cli.execute("/mode workflow"))
    events = []
    first = asyncio.run(cli.execute("创作第一章，埋下线索。", on_event=events.append))
    assert first.run.workflow_snapshot.gate.stage == "SUMMARY"
    assert all("旧版" not in call[2] for call in cli.agent.generator.calls)
    assert not any(event.kind == "Reading" for event in events)
    file.write_text(file.read_text(encoding="utf-8").replace("旧版", "新版"), encoding="utf-8")

    reopened = NovelCLI(cli.workspace, models=cli.models)
    reopened.agent.generator = cli.agent.generator
    reopened.agent.skill_resolver.selector = AbstainingSelector()
    asyncio.run(reopened.execute("/project open Story"))
    later = []
    outline_gate = asyncio.run(reopened.execute("y", on_event=later.append))
    assert outline_gate.run.workflow_snapshot.gate.stage == "OUTLINE"
    next_gate = asyncio.run(reopened.execute("y", on_event=later.append))
    manuscript_ref = next_gate.run.workflow_snapshot.gate.artifact_refs[0]
    manuscript = reopened.artifacts.get(project, manuscript_ref)
    assert [ref.name for ref in manuscript.skill_refs] == ["clue-style"]
    assert "旧版" in reopened.agent.generator.calls[-2][2]
    assert "新版" not in reopened.agent.generator.calls[-2][2]
    assert any(event.kind == "Reading" and "clue-style" in event.detail for event in later)


def test_explicit_skill_excludes_unrequested_auto_skill(tmp_path):
    cli = make_cli(tmp_path)
    project, _ = cli._selected()
    write_skill(project, "manual-style", activation="manual", body="手选写法。")
    write_skill(project, "auto-clue", tags="线索", body="自动写法。")
    asyncio.run(cli.execute("/skill enable manual-style"))
    asyncio.run(cli.execute("/skill enable auto-clue"))
    asyncio.run(cli.execute("/skill use manual-style"))
    asyncio.run(cli.execute("/mode workflow"))
    result = asyncio.run(cli.execute("创作第一章，埋下线索。"))
    assert [ref.name for ref in result.run.skill_refs] == ["manual-style"]
    assert "手选写法" in cli.agent.generator.calls[0][2]
    assert "自动写法" not in cli.agent.generator.calls[0][2]


def test_current_no_skill_instruction_overrides_explicit_selection(tmp_path):
    cli = make_cli(tmp_path)
    project, session = cli._selected()
    write_skill(project, "manual-style", activation="manual", body="手选写法。")
    asyncio.run(cli.execute("/skill enable manual-style"))
    asyncio.run(cli.execute("/skill use manual-style"))
    asyncio.run(cli.execute("/mode workflow"))
    result = asyncio.run(cli.execute("创作第一章，但本次不要使用 Skill。"))
    assert not result.run.skill_refs
    assert all("手选写法" not in call[2] for call in cli.agent.generator.calls)
    assert cli.sessions.load(project, session.session_id).next_skill is None


def test_auto_skill_matches_only_relevant_chapter_and_stage(tmp_path):
    cli = make_cli(tmp_path)
    cli.agent.intent_planner = Plan()
    project, _ = cli._selected()
    write_skill(project, "tower-style", stages="summary", tags="钟楼", body="钟楼场景方法。")
    asyncio.run(cli.execute("/skill enable tower-style"))
    asyncio.run(cli.execute("/mode workflow"))
    result = asyncio.run(
        cli.execute("写一篇悬疑小说。第一章：找到钥匙。第二章：调查钟楼。")
    )
    summaries = [call for call in cli.agent.generator.calls if call[0] == "summary"]
    assert "钟楼场景方法" not in summaries[0][2]
    assert "钟楼场景方法" in summaries[1][2]
    refs = result.run.workflow_snapshot.gate.artifact_refs
    assert not cli.artifacts.get(project, refs[0]).skill_refs
    assert [ref.name for ref in cli.artifacts.get(project, refs[1]).skill_refs] == [
        "tower-style"
    ]


def test_frozen_candidate_can_activate_from_approved_outline(tmp_path):
    cli = make_cli(tmp_path)
    cli.agent.generator = Generator(outline="大纲：在第二场揭示线索。")
    project, _ = cli._selected()
    file = write_skill(
        project, "clue-style", stages="manuscript", tags="线索", body="旧版线索方法。"
    )
    asyncio.run(cli.execute("/skill enable clue-style"))
    asyncio.run(cli.execute("/mode workflow"))
    first = asyncio.run(cli.execute("创作第一章，描写侦探进入房间。"))
    assert not first.run.skill_refs
    file.write_text(file.read_text(encoding="utf-8").replace("旧版", "新版"), encoding="utf-8")
    asyncio.run(cli.execute("y"))
    next_gate = asyncio.run(cli.execute("y"))
    manuscript = cli.artifacts.get(project, next_gate.run.workflow_snapshot.gate.artifact_refs[0])
    assert [ref.name for ref in manuscript.skill_refs] == ["clue-style"]
    assert "旧版线索方法" in cli.agent.generator.calls[-2][2]
    assert "新版线索方法" not in cli.agent.generator.calls[-2][2]


def test_disabled_or_wrong_workflow_auto_skill_is_not_considered(tmp_path):
    cli = make_cli(tmp_path)
    project, _ = cli._selected()
    write_skill(project, "disabled-style", tags="线索", body="不应注入")
    write_skill(
        project, "rewrite-only", workflows="REWRITE_CHAPTER", tags="线索", body="不应注入"
    )
    asyncio.run(cli.execute("/skill enable rewrite-only"))
    asyncio.run(cli.execute("/mode workflow"))
    result = asyncio.run(cli.execute("创作第一章，埋下线索。"))
    assert not result.run.skill_refs
    assert all("不应注入" not in call[2] for call in cli.agent.generator.calls)


def test_selector_cannot_invent_skill_or_chapter_index(tmp_path):
    cli = make_cli(tmp_path)
    project, session = cli._selected()
    write_skill(project, "quiet-style", tags="安静")
    asyncio.run(cli.execute("/skill enable quiet-style"))
    session = cli._selected()[1]
    request = WorkflowRequest(
        project_id=project.project_id, session_id=session.session_id,
        title="第一章", instruction="创作第一章，描写孤独感。",
    )

    class BadSelector:
        def __init__(self, choice):
            self.choice = choice

        def select(self, *args, **kwargs):
            return SkillChoices(selections=(self.choice,))

    resolver = cli.agent.skill_resolver
    resolver.selector = BadSelector(SkillChoice(name="invented", chapter_indexes=(0,)))
    with pytest.raises(InvalidChange, match="unavailable"):
        resolver.resolve(project, session, request)
    resolver.selector = BadSelector(SkillChoice(name="quiet-style", chapter_indexes=(2,)))
    with pytest.raises(InvalidChange, match="Chapter index"):
        resolver.resolve(project, session, request)


def test_skill_context_budget_and_named_reference(tmp_path):
    cli = make_cli(tmp_path)
    project, session = cli._selected()
    file = write_skill(project, "long-style", activation="manual", body="甲" * 9000)
    reference = file.parent / "references" / "detail.md"
    reference.parent.mkdir()
    reference.write_text("参考细节", encoding="utf-8")
    file.write_text(
        file.read_text(encoding="utf-8").replace(
            "tags: \n", "tags: \nresources: references/detail.md\n"
        ),
        encoding="utf-8",
    )
    asyncio.run(cli.execute("/skill enable long-style"))
    asyncio.run(cli.execute("/skill use long-style"))
    session = cli._selected()[1]
    request = WorkflowRequest(
        project_id=project.project_id, session_id=session.session_id,
        title="第一章", instruction="创作第一章。",
    )
    bound = cli.agent.skill_resolver.resolve(project, session, request)
    refs, context = skill_context(cli.agent.skills, project, bound, "summary")
    assert len(refs) == 1
    assert len(context) <= 3000
    assert "参考细节" in context
