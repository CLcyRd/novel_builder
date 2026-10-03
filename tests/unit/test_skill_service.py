import pytest

from novel_agent.application import build_project_service, build_session_service
from novel_agent.domain.errors import InvalidChange, UnsafeProjectPath
from novel_agent.services.skill import SkillService


def _project(tmp_path):
    return build_project_service().create_project(tmp_path, "Skills", "Main")


def _write_skill(project, name="quiet-scenes", description="安静场景写作", body="少用旁白。"):
    root = project.root / "skills" / name
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}", encoding="utf-8"
    )
    return root


def test_project_skill_is_disabled_until_enabled_and_snapshot_survives_edit(tmp_path):
    project = _project(tmp_path)
    root = _write_skill(project)
    service = SkillService()
    found = service.get(project, "quiet-scenes")
    assert found.ref.source == "project"
    assert found not in service.enabled(project, project.sessions[0])
    session = build_session_service().update_settings(
        project, project.sessions[0].session_id, enabled_skills=("quiet-scenes",)
    )
    assert found in service.enabled(project, session)
    ref = service.snapshot(project, found)
    (root / "SKILL.md").write_text(
        "---\nname: quiet-scenes\ndescription: 安静场景写作\n---\n\n增加动作。",
        encoding="utf-8",
    )
    assert "少用旁白" in service.load_snapshot(project, ref).files["SKILL.md"].decode("utf-8")
    assert service.get(project, "quiet-scenes").ref.content_hash != ref.content_hash


def test_skill_catalog_rejects_invalid_frontmatter_and_links(tmp_path):
    project = _project(tmp_path)
    root = _write_skill(project)
    (root / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")
    with pytest.raises(InvalidChange):
        SkillService().list(project)
    (root / "SKILL.md").unlink()
    outside = tmp_path / "outside.md"
    outside.write_text("秘密", encoding="utf-8")
    try:
        (root / "SKILL.md").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(UnsafeProjectPath):
        SkillService().list(project)


def test_workflow_auto_metadata_is_explicit_and_validated(tmp_path):
    project = _project(tmp_path)
    root = _write_skill(project)
    skill = SkillService().get(project, "quiet-scenes")
    assert skill.activation == "manual"
    (root / "references").mkdir()
    (root / "references" / "notes.md").write_text("节奏参考", encoding="utf-8")
    (root / "SKILL.md").write_text(
        "---\nname: quiet-scenes\ndescription: 安静场景写作\nactivation: auto\n"
        "workflows: CREATE_CHAPTER, MULTI_CHAPTER\nstages: outline, manuscript\n"
        "tags: 安静, 独处\nresources: references/notes.md\n---\n少用旁白。",
        encoding="utf-8",
    )
    skill = SkillService().get(project, "quiet-scenes")
    assert skill.activation == "auto"
    assert skill.workflows == ("CREATE_CHAPTER", "MULTI_CHAPTER")
    assert skill.stages == ("outline", "manuscript")
    assert skill.tags == ("安静", "独处")
    assert skill.resources == ("references/notes.md",)
    (root / "SKILL.md").write_text(
        "---\nname: quiet-scenes\ndescription: 安静场景\nactivation: automatic\n---\n内容",
        encoding="utf-8",
    )
    with pytest.raises(InvalidChange, match="activation"):
        SkillService().get(project, "quiet-scenes")
