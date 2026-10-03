import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from novel_agent.application import build_project_service
from novel_agent.domain.chapter import ChapterDocument, serialize_chapter
from novel_agent.domain.errors import (
    ChapterNotFound,
    InvalidAttachment,
    InvalidProject,
    InvalidProjectName,
    ProjectAlreadyExists,
    UnsafeProjectPath,
)


def _register_existing_chapter(project_root: Path, title="第一章"):
    """Prepare an existing formal Project fixture; product writes arrive in CommitService."""
    metadata_path = project_root / ".novel-agent/project.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    chapter_id = str(uuid4())
    metadata["chapter_order"] = [chapter_id]
    metadata["chapters"] = {chapter_id: {"chapter_id": chapter_id, "filename": "0001.md"}}
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    chapter = ChapterDocument(title, "概述", "- 场景一", "雨夜里，林舟捡起一枚铜钥匙。")
    (project_root / "chapters/0001.md").write_text(
        serialize_chapter(chapter), encoding="utf-8", newline="\n"
    )
    return chapter_id, chapter


def test_create_reopen_and_read_registered_chapter_in_separate_service(tmp_path):
    workspace = tmp_path / "projects"
    first = build_project_service()
    created = first.create_project(workspace, "深海回声", "初始会话")
    assert created.root.name == "深海回声"
    assert created.sessions[0].project_id == created.project_id
    assert created.sessions[0].name == "初始会话"
    assert created.sessions[0].auto_approval is False
    assert (created.root / "PROJECT.md").read_text(encoding="utf-8") == "# 深海回声\n"
    assert (created.root / "MANUSCRIPT.md").read_text(encoding="utf-8") == ""
    for directory in ("chapters", "sources", "references", ".novel-agent/drafts"):
        assert (created.root / directory).is_dir()

    chapter_id, expected = _register_existing_chapter(created.root)
    reopened = build_project_service().open_project(created.root)
    assert reopened.project_id == created.project_id
    assert first.read_chapter(created, reopened.metadata.chapter_order[0]) == expected
    assert (
        build_project_service().read_chapter(reopened, reopened.metadata.chapter_order[0])
        == expected
    )
    assert reopened.metadata.chapter_order[0] == UUID(chapter_id)
    assert len(first.list_projects(workspace)) == 1
    assert first.read_project_text(reopened) == "# 深海回声\n"


def test_creation_rejects_unsafe_names_and_does_not_overwrite(tmp_path):
    service = build_project_service()
    for name in ("../escape", "CON", "bad/name", "trailing. ", "", "bad\nname"):
        with pytest.raises(InvalidProjectName):
            service.create_project(tmp_path, name, "session")
    project = service.create_project(tmp_path, "正常项目", "session")
    original = (project.root / "PROJECT.md").read_bytes()
    with pytest.raises(ProjectAlreadyExists):
        service.create_project(tmp_path, "正常项目", "another")
    assert (project.root / "PROJECT.md").read_bytes() == original
    with pytest.raises(InvalidProjectName):
        service.create_project(tmp_path, "another", "COM1")


@pytest.mark.parametrize(
    "damage", ["missing_chapter", "unregistered", "bad_section", "bad_session"]
)
def test_open_rejects_invalid_project_structure(tmp_path, damage):
    service = build_project_service()
    project = service.create_project(tmp_path, "测试", "初始")
    chapter_id, _ = _register_existing_chapter(project.root)
    if damage == "missing_chapter":
        (project.root / "chapters/0001.md").unlink()
    elif damage == "unregistered":
        (project.root / "chapters/0002.md").write_text("junk", encoding="utf-8")
    elif damage == "bad_section":
        (project.root / "chapters/0001.md").write_text("# 残缺", encoding="utf-8")
    else:
        session_file = (
            project.root / f".novel-agent/sessions/{project.metadata.initial_session_id}.json"
        )
        session_file.unlink()
    with pytest.raises(InvalidProject):
        service.open_project(project.root)
    assert chapter_id


def test_chapter_id_must_be_registered(tmp_path):
    service = build_project_service()
    project = service.create_project(tmp_path, "项目", "会话")
    with pytest.raises(ChapterNotFound):
        service.read_chapter(project, uuid4())


@pytest.mark.parametrize("damage", ["schema", "chapter_index", "session_project_id"])
def test_open_rejects_corrupt_metadata(tmp_path, damage):
    service = build_project_service()
    project = service.create_project(tmp_path, "项目", "会话")
    project_json = project.root / ".novel-agent/project.json"
    session_json = (
        project.root / f".novel-agent/sessions/{project.metadata.initial_session_id}.json"
    )
    if damage == "session_project_id":
        data = json.loads(session_json.read_text(encoding="utf-8"))
        data["project_id"] = str(uuid4())
        session_json.write_text(json.dumps(data), encoding="utf-8")
    else:
        data = json.loads(project_json.read_text(encoding="utf-8"))
        if damage == "schema":
            data["schema_version"] = 2
        else:
            data["chapter_order"] = [str(uuid4())]
        project_json.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InvalidProject):
        service.open_project(project.root)


def test_open_rejects_project_directory_link(tmp_path):
    service = build_project_service()
    project = service.create_project(tmp_path, "项目", "会话")
    references = project.root / "references"
    references.rmdir()
    external = tmp_path / "external"
    external.mkdir()
    try:
        references.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlink creation unavailable on this machine: {exc}")
    with pytest.raises(InvalidProject):
        service.open_project(project.root)


def test_attachment_path_safety_and_file_validation(tmp_path):
    service = build_project_service()
    project = service.create_project(tmp_path, "项目", "会话")
    reference = project.root / "references/带 空格.md"
    reference.write_text("/exit\n只是资料", encoding="utf-8")
    assert service.resolve_attachment(project, "references/带 空格.md") == reference.resolve()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    for path in ("", "../outside.md", outside, ".novel-agent/project.json"):
        with pytest.raises(UnsafeProjectPath):
            service.resolve_attachment(project, path)
    with pytest.raises(InvalidAttachment):
        service.resolve_attachment(project, "references/missing.md")
    empty = project.root / "references/empty.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(InvalidAttachment):
        service.resolve_attachment(project, "references/empty.txt")
    with pytest.raises(InvalidAttachment):
        service.resolve_attachment(project, "PROJECT.md.json")


def test_attachment_symlink_to_outside_is_rejected(tmp_path):
    service = build_project_service()
    project = service.create_project(tmp_path, "项目", "会话")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = project.root / "references/link.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation unavailable on this machine: {exc}")
    with pytest.raises(UnsafeProjectPath):
        service.resolve_attachment(project, "references/link.md")
