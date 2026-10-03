import asyncio

import pytest

from novel_agent.cli.shell import NovelCLI, parse_attachments
from novel_agent.domain.change import CreateChapter
from novel_agent.domain.errors import InvalidAttachment, UnsafeProjectPath
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway


def _cli(tmp_path):
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake-model", "https://example.invalid/v1", "none")},
        "fake",
    )
    return NovelCLI(tmp_path / "workspace", models=models)


def _run(cli, line):
    return asyncio.run(cli.execute(line))


def test_attachment_parser_only_consumes_user_line():
    prompt, files = parse_attachments('比较 @"references/a b.md" 与 @sources/c.txt 的线索')
    assert "比较" in prompt and "线索" in prompt and "@" not in prompt
    assert files == ("references/a b.md", "sources/c.txt")
    with pytest.raises(ValueError):
        parse_attachments("@references/a.md")


def test_cli_project_session_settings_and_no_model_call(tmp_path):
    cli = _cli(tmp_path)
    assert "No Projects" in _run(cli, "/project list").text
    assert "Project created" in _run(cli, "/project create Test Novel").text
    assert "Test Novel" in _run(cli, "/project list").text
    assert "Session created" in _run(cli, "/session create Revision").text
    assert "Revision" in _run(cli, "/session list").text
    assert "FREEFORM" in _run(cli, "/status").text
    assert "True" in _run(cli, "/auto on").text
    assert "WORKFLOW" in _run(cli, "/mode workflow").text
    with pytest.raises(Exception, match="Continuation requires a previous Chapter"):
        _run(cli, "写下一章")
    assert "FREEFORM" in _run(cli, "/mode freeform").text
    assert "fake-model" in _run(cli, "/model list").text
    assert _run(cli, "/exit").exit_requested


def test_cli_attachment_preflight_blocks_model_and_commands_in_file(tmp_path):
    cli = _cli(tmp_path)
    _run(cli, "/project create Safe")
    project = cli.project
    assert project is not None
    (project.root / "references" / "safe.md").write_text(
        "/exit\n/commit\njust data", encoding="utf-8"
    )
    assert parse_attachments("read @references/safe.md")[1] == ("references/safe.md",)
    with pytest.raises(UnsafeProjectPath):
        _run(cli, "read @../outside.md")
    with pytest.raises(InvalidAttachment):
        _run(cli, "read @references/missing.md")
    assert not _run(cli, "/help").exit_requested


def test_cli_exact_artifact_commit_and_reopen(tmp_path):
    cli = _cli(tmp_path)
    _run(cli, "/project create Drafts")
    project, session = cli._selected()
    from uuid import uuid4

    chapter_id = uuid4()
    cli.commits.commit(project, (CreateChapter("一", chapter_id=chapter_id),))
    first = cli.artifacts.create(
        project,
        session.session_id,
        "OUTLINE",
        "SECTION",
        "初稿",
        chapter_id=chapter_id,
        section="outline",
    )
    second = cli.artifacts.revise(project, first.ref, "修订稿")
    assert "初稿" in _run(cli, f"/artifact show {first.artifact_id}@1").text
    assert "修订稿" in _run(cli, f"/artifact show {first.artifact_id}@2").text
    with pytest.raises(Exception, match="Specify a displayed exact"):
        _run(cli, f"/commit {first.artifact_id}")
    committed = _run(cli, f"/commit {first.artifact_id}@1")
    assert "chapters/" in committed.text
    assert cli.projects.read_chapter(project, chapter_id).outline == first.content
    reopened = _cli(tmp_path)
    _run(reopened, "/project open Drafts")
    assert "Drafts" in _run(reopened, "/status").text
    assert second.revision == 2


def test_commit_current_uses_displayed_artifact_revision(tmp_path):
    cli = _cli(tmp_path)
    _run(cli, "/project create Current")
    project, session = cli._selected()
    from uuid import uuid4

    chapter_id = uuid4()
    cli.commits.commit(project, (CreateChapter("一", chapter_id=chapter_id),))
    draft = cli.artifacts.create(
        project, session.session_id, "OUTLINE", "SECTION", "当前大纲",
        chapter_id=chapter_id, section="outline",
    )
    _run(cli, f"/artifact show {draft.artifact_id}@1")
    with pytest.raises(ValueError, match="alone"):
        _run(cli, f"/commit current {draft.artifact_id}@1")
    result = _run(cli, "/commit current")
    assert "chapters/0001.md" in result.text
    assert cli.projects.read_chapter(project, chapter_id).outline == "当前大纲"
