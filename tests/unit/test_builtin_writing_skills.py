import pytest

from novel_agent.application import build_artifact_service, build_project_service
from novel_agent.services.skill import SkillService
from novel_agent.services.workflow_skill import WorkflowSkillResolver
from novel_agent.workflow.models import WorkflowRequest
from novel_agent.workflow.skill_context import skill_context

NAMES = (
    "writing-foundations",
    "viewpoint-control",
    "environment-writing",
    "dialogue-writing",
    "antagonist-design",
    "prose-style-review",
)


@pytest.mark.parametrize("name", NAMES)
def test_writing_skill_is_discoverable_and_support_files_survive_snapshot(tmp_path, name):
    project = build_project_service().create_project(tmp_path, "Methods", "Main")
    catalog = SkillService()
    skill = catalog.get(project, name)
    assert skill.ref.source == "builtin"
    assert skill in catalog.enabled(project, project.sessions[0])
    assert skill.activation == ("manual" if name == "prose-style-review" else "auto")
    assert skill.resources == ("references/examples.md",)
    assert "references/sources.md" in skill.files
    assert "IMPORT_NOVEL" not in skill.workflows
    frozen = catalog.load_snapshot(project, catalog.snapshot(project, skill))
    assert frozen.files == skill.files
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="方法测试",
        instruction="创作",
        skill_refs=(skill.ref,),
    )
    used, context = skill_context(catalog, project, request, skill.stages[0])
    assert used == (skill.ref,)
    assert skill.files["references/examples.md"].decode("utf-8") in context
    assert context.endswith(skill.files["references/examples.md"].decode("utf-8"))


@pytest.mark.parametrize(
    "names,stage",
    [
        (("writing-foundations", "dialogue-writing"), "manuscript"),
        (("viewpoint-control", "environment-writing"), "outline"),
        (("writing-foundations", "prose-style-review"), "review"),
    ],
)
def test_two_method_skills_fit_workflow_budget_without_truncating_rules(tmp_path, names, stage):
    project = build_project_service().create_project(tmp_path, "Budget", "Main")
    catalog = SkillService()
    definitions = [catalog.get(project, name) for name in names]
    refs = tuple(catalog.snapshot(project, skill) for skill in definitions)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="预算测试",
        instruction="创作",
        skill_refs=refs,
    )
    used, context = skill_context(catalog, project, request, stage)
    assert used == refs and len(context) <= 3000
    for skill in definitions:
        body = skill.files["SKILL.md"].decode("utf-8").split("---\n", 2)[2]
        assert body in context
        assert skill.files["references/examples.md"].decode("utf-8") in context


def test_style_review_is_only_injected_into_review_stage(tmp_path):
    project = build_project_service().create_project(tmp_path, "Review", "Main")
    catalog = SkillService()
    skill = catalog.get(project, "prose-style-review")
    ref = catalog.snapshot(project, skill)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.sessions[0].session_id,
        title="审阅",
        instruction="审阅文风",
        skill_refs=(ref,),
    )
    assert skill_context(catalog, project, request, "manuscript") == ((), "")
    used, _context = skill_context(catalog, project, request, "review")
    assert used == (ref,)


def test_writing_methods_are_selected_by_task_and_not_injected_into_summary(tmp_path):
    project = build_project_service().create_project(tmp_path, "Selection", "Main")
    catalog = SkillService()

    class UnexpectedSelector:
        def select(self, *args, **kwargs):
            raise AssertionError("Two exact task tags should resolve without a model call")

    resolver = WorkflowSkillResolver(catalog, build_artifact_service(), UnexpectedSelector())
    request = resolver.resolve(
        project,
        project.sessions[0],
        WorkflowRequest(
            project_id=project.project_id,
            session_id=project.sessions[0].session_id,
            title="借款",
            instruction="规划人物动机与对话场景，两位朋友讨论还款。",
        ),
    )
    assert {ref.name for ref in request.skill_refs} == {"writing-foundations", "dialogue-writing"}
    assert skill_context(catalog, project, request, "summary") == ((), "")
    used, context = skill_context(catalog, project, request, "outline")
    assert set(used) == set(request.skill_refs)
    assert "references/examples.md" in context
