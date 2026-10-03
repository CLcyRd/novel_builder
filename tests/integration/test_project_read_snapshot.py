"""Linear chapter reads, fresh operation snapshots and preserved validation."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from novel_agent.agent import freeform
from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
    build_rewrite_chapter_workflow,
    build_session_service,
)
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.domain.chapter import ChapterDocument, serialize_chapter
from novel_agent.domain.errors import ChapterNotFound, DecisionRequired, InvalidProject
from novel_agent.infrastructure.filesystem import project_repository
from novel_agent.infrastructure.filesystem.artifact_repository import FileArtifactRepository
from novel_agent.infrastructure.filesystem.project_repository import FileProjectRepository
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.services.commit import CommitService
from novel_agent.workflow.models import WorkflowRequest


def _seed(tmp_path, count=10):
    projects = build_project_service()
    project = projects.create_project(tmp_path, "Snapshot", "Main")
    ids = tuple(uuid4() for _ in range(count))
    metadata = project.metadata.model_dump(mode="json")
    # Order differs from file order: readers must use the persisted chapter order.
    metadata["chapter_order"] = [str(chapter_id) for chapter_id in reversed(ids)]
    metadata["chapters"] = {
        str(chapter_id): {"chapter_id": str(chapter_id), "filename": f"{index:04d}.md"}
        for index, chapter_id in enumerate(ids, 1)
    }
    for index in range(1, count + 1):
        document = ChapterDocument(
            f"Chapter {index}",
            "needle summary",
            "needle outline",
            f"first line\nneedle body {index}",
        )
        (project.root / f"chapters/{index:04d}.md").write_bytes(
            serialize_chapter(document).encode("utf-8")
        )
    (project.root / ".novel-agent/project.json").write_text(json.dumps(metadata), encoding="utf-8")
    return projects, project, ids


@pytest.fixture
def parse_calls(monkeypatch):
    calls = []
    original = project_repository.parse_chapter

    def counted(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(project_repository, "parse_chapter", counted)
    return calls


@pytest.mark.parametrize("count", [0, 1, 10, 100])
def test_list_search_and_single_read_parse_each_chapter_once(tmp_path, parse_calls, count):
    projects, project, ids = _seed(tmp_path, count)
    for directory in ("references", "sources"):
        for index in range(3):
            (project.root / directory / f"{index}.txt").write_bytes(
                b"first\r\nneedle reference\r\n"
            )
    (project.root / "references/ignored.bin").write_bytes(b"\xff")

    parse_calls.clear()
    chapters = projects.list_chapters(project)
    assert tuple(chapter_id for chapter_id, _ in chapters) == tuple(reversed(ids))
    assert len(parse_calls) == count

    parse_calls.clear()
    hits = projects.search(project, "needle")
    assert len(parse_calls) == count
    assert len(hits) == count * 3 + 6
    assert [hit.section for hit in hits[: count * 3]] == [
        "manuscript",
        "outline",
        "summary",
    ] * count
    assert [(hit.path, hit.line) for hit in hits[count * 3 :]] == [
        (f"{directory}/{index}.txt", 2)
        for directory in ("references", "sources")
        for index in range(3)
    ]
    if ids:
        assert hits[0].path == f"chapters/{count:04d}.md" and hits[0].line == 2
        parse_calls.clear()
        assert projects.read_section(project, ids[0], "manuscript").endswith("needle body 1")
        assert len(parse_calls) == count
    parse_calls.clear()
    assert projects.read_project_text(project) == "# Snapshot\n"
    assert len(parse_calls) == count


def test_snapshot_is_local_to_operation_and_next_read_observes_external_edit(tmp_path):
    projects, project, ids = _seed(tmp_path, 2)
    path = project.root / "chapters/0001.md"
    old = projects.read_snapshot(project)
    changed = replace(old.chapter(ids[0]), manuscript="external edit")
    path.write_bytes(serialize_chapter(changed).encode("utf-8"))
    (project.root / "PROJECT.md").write_bytes(b"# Changed\n")
    new = build_project_service().read_snapshot(project)
    assert old.chapter(ids[0]).manuscript == "first line\nneedle body 1"
    assert old.project_text == "# Snapshot\n"
    assert new.chapter(ids[0]).manuscript == "external edit" and new.project_text == "# Changed\n"
    with pytest.raises(TypeError):
        old.chapters[ids[0]] = changed
    with pytest.raises(TypeError):
        old.text_files["other.md"] = "change"
    with pytest.raises(ChapterNotFound):
        old.chapter(uuid4())


def test_commit_apply_reuses_current_parse_and_preserves_old_snapshot_and_freshness(
    tmp_path, parse_calls
):
    projects, project, ids = _seed(tmp_path, 10)
    old = projects.read_snapshot(project)
    commits = build_commit_service()
    parse_calls.clear()
    prepared = commits.prepare(project, (SetSection(ids[0], "manuscript", text="committed"),))
    assert len(parse_calls) == 10
    parse_calls.clear()
    commits.apply(prepared)
    assert len(parse_calls) == 10
    assert old.chapter(ids[0]).manuscript.endswith("needle body 1")
    assert projects.read_snapshot(project).chapter(ids[0]).manuscript == "committed"

    prepared = commits.prepare(project, (SetSection(ids[0], "manuscript", text="stale"),))
    commits.commit(project, (SetSection(ids[0], "manuscript", text="newer"),))
    with pytest.raises(DecisionRequired):
        commits.apply(prepared)
    assert projects.read_chapter(project, ids[0]).manuscript == "newer"


@pytest.mark.parametrize("damage", ["missing", "format", "unregistered", "session"])
def test_all_read_paths_still_reject_damage_to_unrelated_project_data(tmp_path, damage):
    projects, project, ids = _seed(tmp_path, 2)
    if damage == "missing":
        (project.root / "chapters/0002.md").unlink()
    elif damage == "format":
        (project.root / "chapters/0002.md").write_bytes(b"# malformed\n")
    elif damage == "unregistered":
        (project.root / "chapters/0003.md").write_bytes(b"unknown")
    else:
        session_file = f".novel-agent/sessions/{project.metadata.initial_session_id}.json"
        (project.root / session_file).unlink()
    for operation in (
        lambda: projects.open_project(project.root),
        lambda: projects.read_snapshot(project),
        lambda: projects.read_chapter(project, ids[0]),
        lambda: projects.list_chapters(project),
        lambda: projects.search(project, "needle"),
    ):
        with pytest.raises(InvalidProject):
            operation()


def test_bulk_reads_check_project_identity_and_unknown_chapter_and_section(tmp_path):
    projects, project, ids = _seed(tmp_path, 1)
    wrong = replace(project, metadata=project.metadata.model_copy(update={"project_id": uuid4()}))
    for operation in (projects.read_snapshot, projects.list_chapters, projects.read_project_text):
        with pytest.raises(InvalidProject, match="identity"):
            operation(wrong)
    with pytest.raises(InvalidProject, match="identity"):
        projects.search(wrong, "needle")
    with pytest.raises(ChapterNotFound):
        projects.read_chapter(project, uuid4())
    with pytest.raises(ValueError, match="Unknown chapter section"):
        projects.read_section(project, ids[0], "unknown")


@pytest.mark.parametrize("path", ["../outside.md", ".novel-agent/project.json", "references/no.md"])
def test_snapshot_batch_attachments_preserve_path_validation(tmp_path, path):
    projects, project, _ = _seed(tmp_path, 1)
    with pytest.raises(ValueError):
        projects.read_snapshot(project, attachments=(path,))
    with pytest.raises(ValueError):
        projects.read_snapshot(project, reference_directories=("..",))


def test_search_and_batch_attachment_reject_link_outside_project(tmp_path):
    projects, project, _ = _seed(tmp_path, 1)
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret", encoding="utf-8")
    linked = project.root / "references/linked.md"
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    for operation in (
        lambda: projects.search(project, "secret"),
        lambda: projects.read_snapshot(project, attachments=("references/linked.md",)),
    ):
        with pytest.raises(ValueError, match="outside"):
            operation()


def test_bulk_snapshot_recovers_interrupted_commit_before_loading_chapters(tmp_path):
    projects, project, _ = _seed(tmp_path, 1)
    chapter_id = uuid4()

    def stop(*args):
        raise RuntimeError("interrupted")

    commits = CommitService(FileProjectRepository(), FileArtifactRepository(), stop)
    with pytest.raises(RuntimeError, match="interrupted"):
        commits.commit(
            project,
            (
                CreateChapter("New", chapter_id=chapter_id),
                SetSection(chapter_id, "manuscript", text="recovered"),
            ),
        )
    snapshot = projects.read_snapshot(project)
    assert snapshot.chapter(chapter_id).manuscript == "recovered"
    assert len(snapshot.chapters) == 2


@pytest.mark.parametrize("operation", ["blocks", "context", "legacy_context"])
def test_workflow_context_parses_once_and_keeps_full_formal_evidence(
    tmp_path, parse_calls, operation
):
    projects, project, ids = _seed(tmp_path, 10)
    (project.root / "references/characters.md").write_text("character notes", encoding="utf-8")
    (project.root / "references/attachment.md").write_text("attachment notes", encoding="utf-8")
    # Unselected references must not become new mandatory inputs to generation.
    (project.root / "references/ignored.md").write_bytes(b"\xff")
    workflow = build_rewrite_chapter_workflow(None)
    request = WorkflowRequest(
        project_id=project.project_id,
        session_id=project.metadata.initial_session_id,
        kind="REWRITE_CHAPTER",
        chapter_id=ids[4],
        title="Chapter 5",
        instruction="rewrite",
        attachments=("references/attachment.md",),
    )
    parse_calls.clear()
    if operation == "legacy_context":
        files = workflow._formal_context(project, request)
        assert [path for path, _ in files] == [
            "chapters/0006.md",
            "chapters/0005.md",
            "chapters/0004.md",
            "PROJECT.md",
            "references/characters.md",
        ]
    else:
        blocks = (
            workflow._formal_blocks(project, request)
            if operation == "blocks"
            else workflow._context(project, request, {}).blocks
        )
        target = next(block for block in blocks if block.kind == "formal_target_manuscript")
        assert target.content == "first line\nneedle body 5" and target.source == "chapters/0005.md"
        assert any(
            block.kind == "reference" and block.content == "character notes" for block in blocks
        )
        if operation == "context":
            assert any(
                block.kind == "attachment" and block.content == "attachment notes"
                for block in blocks
            )
    assert len(parse_calls) == 10


def test_freeform_read_tools_parse_once_per_call_and_return_matching_sources(
    tmp_path, monkeypatch, parse_calls
):
    projects, project, ids = _seed(tmp_path, 10)
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake", "https://example.invalid", "unused")}, "fake"
    )
    agent = freeform.FreeformAgent(
        projects, build_session_service(), build_artifact_service(), build_commit_service(), models
    )
    monkeypatch.setattr(freeform, "build_chat_model", lambda *args: object())
    outputs = {}

    def fake_agent(**kwargs):
        tools = {tool.name: tool for tool in kwargs["tools"]}

        class Graph:
            async def astream(self, *args, **kwargs):
                for name, arguments in (
                    ("project_overview", {}),
                    ("read_chapter", {"chapter_id": str(ids[0]), "section": "manuscript"}),
                    ("search_project", {"needle": "needle"}),
                ):
                    parse_calls.clear()
                    outputs[name] = tools[name].invoke(arguments)
                    assert len(parse_calls) == 10
                yield "updates", {"model": {"messages": [AIMessage(content="Read complete")]}}

        return Graph()

    monkeypatch.setattr(freeform, "create_deep_agent", fake_agent)
    result = asyncio.run(agent.run(project, project.sessions[0], "不要使用Skill。查询项目。"))
    assert not result.commits and result.response == "Read complete"
    assert f"1. Chapter 10 | chapter_id={ids[-1]}" in outputs["project_overview"]
    assert "来源：chapters/0001.md#manuscript\nfirst line\nneedle body 1" == outputs["read_chapter"]
    assert "chapters/0010.md#manuscript:2:" in outputs["search_project"]


def test_title_lookup_uses_one_parse_pass_and_reports_ambiguity(tmp_path, parse_calls):
    projects, project, ids = _seed(tmp_path, 10)
    parse_calls.clear()
    target = freeform._requested_chapter_deletion("删除章节《Chapter 1》", project, projects)
    assert target == ids[0]
    assert len(parse_calls) == 10
    path = project.root / "chapters/0002.md"
    path.write_bytes(serialize_chapter(ChapterDocument("Chapter 1", "", "", "")).encode("utf-8"))
    with pytest.raises(ValueError, match="ambiguous"):
        freeform._requested_chapter_deletion("删除章节《Chapter 1》", project, projects)
