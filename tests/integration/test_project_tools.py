from uuid import uuid4

from novel_agent.application import (
    build_commit_service,
    build_project_service,
    build_session_service,
)
from novel_agent.domain.change import CreateChapter, DeleteText, InsertText, SetSection


def test_sessions_are_project_local_and_persist_settings(tmp_path):
    projects = build_project_service()
    sessions = build_session_service()
    first = projects.create_project(tmp_path, "甲项目", "初始")
    second = projects.create_project(tmp_path, "乙项目", "另一个初始")
    new = sessions.create(first, "续写")
    assert new.auto_approval is False
    assert new.execution_mode == "FREEFORM"
    updated = sessions.update_settings(
        first, new.session_id, execution_mode="WORKFLOW", auto_approval=True, current_model="grok"
    )
    assert updated.current_model == "grok"
    reopened = build_project_service().open_project(first.root)
    assert sessions.load(reopened, new.session_id).auto_approval is True
    assert len(sessions.list(reopened)) == 2
    assert len(sessions.list(second)) == 1
    assert new.session_id not in {session.session_id for session in sessions.list(second)}


def test_search_count_and_insert_delete_are_deterministic(tmp_path):
    service = build_project_service()
    project = service.create_project(tmp_path, "检索", "会话")
    chapter_id = uuid4()
    build_commit_service().commit(
        project,
        (
            CreateChapter("第一章", chapter_id=chapter_id),
            SetSection(chapter_id, "summary", text="林舟得到钥匙"),
            SetSection(chapter_id, "outline", text="林舟计划开门"),
            SetSection(chapter_id, "manuscript", text="林舟拿起钥匙。\n门开了。"),
        ),
    )
    reference = project.root / "references/人物.md"
    reference.write_text("林舟怕水。", encoding="utf-8")
    hits = service.search(project, "林舟")
    assert [hit.section for hit in hits] == ["manuscript", "outline", "summary", "reference"]
    assert service.count_characters(project, chapter_id) == len("林舟拿起钥匙。门开了。")
    build_commit_service().commit(
        project,
        (
            InsertText(chapter_id, "manuscript", "钥匙。", "那枚", "before"),
            DeleteText(chapter_id, "manuscript", "门开了。"),
        ),
    )
    manuscript = service.read_section(project, chapter_id, "manuscript")
    assert manuscript == "林舟拿起那枚钥匙。\n"
    assert "那枚钥匙" in (project.root / "MANUSCRIPT.md").read_text(encoding="utf-8")
