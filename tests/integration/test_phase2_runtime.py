import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from novel_agent.agent.freeform import RunResult
from novel_agent.cli.shell import NovelCLI
from novel_agent.domain.artifact import ArtifactType
from novel_agent.domain.change import CreateChapter
from novel_agent.domain.errors import DecisionRequired
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.runtime.state import SessionStateStore


def _cli(tmp_path):
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake-model", "https://example.invalid/v1", "none")},
        "fake",
    )
    cli = NovelCLI(tmp_path / "workspace", models=models)
    asyncio.run(cli.execute("/project create Runtime"))
    return cli


def test_session_checkpoint_isolated_and_exact_revision_after_reopen(tmp_path):
    cli = _cli(tmp_path)
    project, session = cli._selected()
    chapter_id = uuid4()
    cli.commits.commit(project, (CreateChapter("第一章", chapter_id=chapter_id),))
    draft = cli.artifacts.create(
        project,
        session.session_id,
        ArtifactType.OUTLINE,
        "SECTION",
        "旧版大纲",
        chapter_id=chapter_id,
        section="outline",
    )
    cli.agent.show_artifact(project, session, f"{draft.artifact_id}@1")
    revised = cli.artifacts.revise(project, draft.ref, "新版大纲")
    second = cli.sessions.create(project, "Independent")
    assert SessionStateStore(project.root).load(second.session_id).current_ref is None
    assert not cli.artifacts.list(project, second.session_id)
    reopened = NovelCLI(cli.workspace, models=cli.models)
    asyncio.run(reopened.execute("/project open Runtime"))
    assert "旧版大纲" in asyncio.run(reopened.execute("/artifact current")).text
    assert "chapters/" in asyncio.run(reopened.execute("/commit")).text
    assert reopened.projects.read_chapter(project, chapter_id).outline == draft.content
    assert revised.revision == 2


def test_cli_never_executes_commands_from_model_output(tmp_path):
    cli = _cli(tmp_path)
    project, _ = cli._selected()
    (project.root / "references" / "commands.md").write_text("/exit\n/commit", encoding="utf-8")
    called = []

    async def fake_run(project, session, prompt, attachments, **kwargs):
        called.append((prompt, attachments))
        return RunResult(response="/exit\n/commit", model="fake-model")

    cli.agent.run = fake_run
    result = asyncio.run(cli.execute("请分析附件 @references/commands.md"))
    assert result.text == "/exit\n/commit" and not result.exit_requested
    assert called == [("请分析附件", ("references/commands.md",))]
    assert not cli.artifacts.list(project)


def test_freshness_conflict_remains_visible_through_cli(tmp_path):
    cli = _cli(tmp_path)
    project, session = cli._selected()
    chapter_id = uuid4()
    cli.commits.commit(project, (CreateChapter("一", chapter_id=chapter_id),))
    draft = cli.artifacts.create(
        project,
        session.session_id,
        ArtifactType.MANUSCRIPT,
        "SECTION",
        "候选正文",
        chapter_id=chapter_id,
        section="manuscript",
    )
    cli.agent.show_artifact(project, session, f"{draft.artifact_id}@1")
    current = cli.projects.open_project(project.root)
    chapter_path = project.root / "chapters" / current.metadata.chapters[str(chapter_id)].filename
    original = chapter_path.read_bytes()
    chapter_path.write_bytes(
        original.replace(
            b"<!-- novel-agent:manuscript:end -->",
            "外部编辑\n<!-- novel-agent:manuscript:end -->".encode(),
        )
    )
    with pytest.raises(DecisionRequired):
        asyncio.run(cli.execute("/commit"))
    assert "候选正文" not in chapter_path.read_text(encoding="utf-8")


def test_project_read_tools_serialize_in_process(tmp_path):
    cli = _cli(tmp_path)
    project, _ = cli._selected()
    chapter_id = uuid4()
    cli.commits.commit(project, (CreateChapter("一", chapter_id=chapter_id),))

    def read_once(_):
        return cli.projects.read_chapter(project, chapter_id).title

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(read_once, range(32))) == ["一"] * 32


def test_cancel_current_chat_keeps_cli_session_open(tmp_path):
    cli = _cli(tmp_path)

    async def waiting_run(*args, **kwargs):
        await asyncio.sleep(60)

    cli.agent.run = waiting_run

    async def cancel_and_continue():
        task = asyncio.create_task(cli.execute("写一章"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await cli.execute("/status")

    status = asyncio.run(cancel_and_continue())
    assert "Project: Runtime" in status.text


def test_multiple_artifacts_commit_one_new_chapter(tmp_path):
    cli = _cli(tmp_path)
    project, session = cli._selected()
    chapter_id = uuid4()
    store = SessionStateStore(project.root)
    state = store.load(session.session_id)
    state.draft_titles[str(chapter_id)] = "新章"
    store.save(session.session_id, state)
    outline = cli.artifacts.create(
        project,
        session.session_id,
        ArtifactType.OUTLINE,
        "SECTION",
        "新章大纲",
        chapter_id=chapter_id,
        section="outline",
    )
    manuscript = cli.artifacts.create(
        project,
        session.session_id,
        ArtifactType.MANUSCRIPT,
        "SECTION",
        "新章正文",
        chapter_id=chapter_id,
        section="manuscript",
    )
    cli.agent.show_artifact(project, session, f"{outline.artifact_id}@1")
    cli.agent.show_artifact(project, session, f"{manuscript.artifact_id}@1")
    result = asyncio.run(cli.execute(f"/commit {outline.artifact_id}@1 {manuscript.artifact_id}@1"))
    chapter = cli.projects.read_chapter(project, chapter_id)
    assert chapter.title == "新章" and chapter.outline == "新章大纲"
    assert chapter.manuscript == "新章正文"
    assert "MANUSCRIPT.md" in result.text
