import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
)
from novel_agent.domain.artifact import ArtifactStatus, ArtifactType
from novel_agent.domain.change import (
    CreateChapter,
    DeleteChapter,
    MergeChapters,
    MoveChapter,
    RenameChapter,
    ReplaceText,
    SetSection,
    SplitChapter,
)
from novel_agent.domain.errors import DecisionRequired, InvalidChange, RecoveryConflict
from novel_agent.infrastructure.filesystem.artifact_repository import FileArtifactRepository
from novel_agent.infrastructure.filesystem.project_repository import FileProjectRepository
from novel_agent.services.artifact import ArtifactService
from novel_agent.services.commit import CommitService, CommitStatus


def _project(tmp_path):
    return build_project_service().create_project(tmp_path, "雨夜", "创作")


def _create_chapter(project, title="第一章", manuscript="正文"):
    chapter_id = uuid4()
    build_commit_service().commit(
        project,
        (
            CreateChapter(title, chapter_id=chapter_id),
            SetSection(chapter_id, "manuscript", manuscript),
        ),
    )
    return chapter_id


def test_exact_old_revision_commits_without_regeneration(tmp_path):
    project = _project(tmp_path)
    chapter_id = _create_chapter(project)
    artifacts = build_artifact_service()
    first = artifacts.create(
        project,
        project.metadata.initial_session_id,
        ArtifactType.OUTLINE,
        "SECTION",
        "第一版大纲",
        chapter_id=chapter_id,
        section="outline",
    )
    second = artifacts.revise(project, first.ref, "第二版大纲")
    assert artifacts.get(project, first.ref).content == "第一版大纲"
    assert artifacts.status(project, first.ref).status == ArtifactStatus.SUPERSEDED
    assert artifacts.get(project, second.ref).content == "第二版大纲"
    assert build_project_service().read_chapter(project, chapter_id).outline == ""

    result = build_commit_service().commit(
        project, (SetSection(chapter_id, "outline", artifact=first.ref),)
    )
    chapter = build_project_service().read_chapter(project, chapter_id)
    assert chapter.outline == "第一版大纲"
    assert "第二版" not in (project.root / "chapters/0001.md").read_text(encoding="utf-8")
    assert result.committed_artifacts == (first.ref,)
    assert artifacts.status(project, first.ref).status == ArtifactStatus.COMMITTED
    assert artifacts.status(project, second.ref).status == ArtifactStatus.CANDIDATE
    assert "MANUSCRIPT.md" not in result.changed_files


def test_artifact_revision_recovers_index_and_both_statuses(tmp_path):
    project = _project(tmp_path)
    chapter_id = _create_chapter(project)
    artifacts = build_artifact_service()
    first = artifacts.create(
        project,
        project.metadata.initial_session_id,
        ArtifactType.OUTLINE,
        "SECTION",
        "第一版",
        chapter_id=chapter_id,
        section="outline",
    )

    def interrupt(index, _path):
        if index == 0:
            raise RuntimeError("revision write interrupted")

    with pytest.raises(RuntimeError):
        ArtifactService(FileArtifactRepository(interrupt)).revise(project, first.ref, "第二版")
    reopened = build_project_service().open_project(project.root)
    index = artifacts.list(reopened)[0]
    assert index.latest_revision == 2
    assert artifacts.status(reopened, first.ref).status == ArtifactStatus.SUPERSEDED
    from novel_agent.domain.artifact import ArtifactRef
    from novel_agent.infrastructure.filesystem.artifact_repository import content_hash

    second_ref = ArtifactRef(
        artifact_id=first.artifact_id, revision=2, content_hash=content_hash("第二版")
    )
    assert artifacts.get(reopened, second_ref).content == "第二版"
    assert artifacts.status(reopened, second_ref).status == ArtifactStatus.CANDIDATE


def test_freshness_detects_manual_edit_and_direct_prepare_conflict(tmp_path):
    project = _project(tmp_path)
    chapter_id = _create_chapter(project)
    artifacts = build_artifact_service()
    candidate = artifacts.create(
        project,
        project.metadata.initial_session_id,
        ArtifactType.MANUSCRIPT,
        "SECTION",
        "候选正文",
        chapter_id=chapter_id,
        section="manuscript",
    )
    prepared = build_commit_service().prepare(
        project, (SetSection(chapter_id, "manuscript", text="直接编辑"),)
    )
    chapter_file = project.root / "chapters/0001.md"
    original_stat = chapter_file.stat()
    chapter_file.write_text(
        chapter_file.read_text(encoding="utf-8").replace("正文", "手工正文"),
        encoding="utf-8",
        newline="\n",
    )
    os.utime(chapter_file, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    with pytest.raises(DecisionRequired):
        build_commit_service().apply(prepared)
    with pytest.raises(DecisionRequired):
        build_commit_service().commit(
            project, (SetSection(chapter_id, "manuscript", artifact=candidate.ref),)
        )
    assert artifacts.status(project, candidate.ref).status == ArtifactStatus.CANDIDATE
    assert "手工正文" in chapter_file.read_text(encoding="utf-8")


def test_multi_file_commit_recovers_forward_and_retries_idempotently(tmp_path):
    project = _project(tmp_path)
    chapter_id = uuid4()
    commit_id = uuid4()
    changes = (
        CreateChapter("第一章", chapter_id=chapter_id),
        SetSection(chapter_id, "summary", text="概述"),
        SetSection(chapter_id, "manuscript", text="雨落了下来。"),
    )

    def fail_after_first(index, _path):
        if index == 0:
            raise RuntimeError("simulated process interruption")

    service = CommitService(FileProjectRepository(), FileArtifactRepository(), fail_after_first)
    with pytest.raises(RuntimeError):
        service.commit(project, changes, commit_id=commit_id)
    reopened = build_project_service().open_project(project.root)
    assert build_project_service().read_chapter(reopened, chapter_id).manuscript == "雨落了下来。"
    assert "雨落了下来。" in (project.root / "MANUSCRIPT.md").read_text(encoding="utf-8")
    first_result = build_commit_service().commit(project, changes, commit_id=commit_id)
    assert first_result.status == "COMPLETED"
    assert first_result.commit_id == commit_id
    assert len(reopened.metadata.chapter_order) == 1
    with pytest.raises(InvalidChange):
        build_commit_service().commit(project, (CreateChapter("不同"),), commit_id=commit_id)


def test_new_process_recovers_commit_after_hard_exit(tmp_path):
    project = _project(tmp_path)
    chapter_id = uuid4()
    child = """
import os, sys
from pathlib import Path
from uuid import UUID
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.infrastructure.filesystem.artifact_repository import FileArtifactRepository
from novel_agent.infrastructure.filesystem.project_repository import FileProjectRepository
from novel_agent.services.commit import CommitService

project = FileProjectRepository().open(Path(sys.argv[1]))
chapter_id = UUID(sys.argv[2])
def hard_exit(index, path):
    if index == 0:
        os._exit(73)
CommitService(FileProjectRepository(), FileArtifactRepository(), hard_exit).commit(
    project,
    (CreateChapter("第一章", chapter_id=chapter_id),
     SetSection(chapter_id, "manuscript", text="进程退出后保留。")),
)
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(project.root), str(chapter_id)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 73, result.stderr
    reopened = build_project_service().open_project(project.root)
    assert reopened.metadata.chapter_order == (chapter_id,)
    assert (
        build_project_service().read_chapter(reopened, chapter_id).manuscript == "进程退出后保留。"
    )
    assert "进程退出后保留。" in (project.root / "MANUSCRIPT.md").read_text(encoding="utf-8")


def test_recovery_stops_on_unexpected_external_change(tmp_path):
    project = _project(tmp_path)
    chapter_id = uuid4()

    def interrupt(index, _path):
        if index == 0:
            raise RuntimeError("interrupted")

    changes = (CreateChapter("第一章", chapter_id=chapter_id),)
    with pytest.raises(RuntimeError):
        CommitService(FileProjectRepository(), FileArtifactRepository(), interrupt).commit(
            project, changes
        )
    manuscript = project.root / "MANUSCRIPT.md"
    manuscript.write_text("external divergent content", encoding="utf-8")
    with pytest.raises(RecoveryConflict):
        build_project_service().open_project(project.root)
    assert manuscript.read_text(encoding="utf-8") == "external divergent content"


def test_chapter_structure_operations_and_derived_view(tmp_path):
    project = _project(tmp_path)
    first, second, third = uuid4(), uuid4(), uuid4()
    build_commit_service().commit(
        project,
        (
            CreateChapter("一", chapter_id=first),
            SetSection(first, "manuscript", text="甲。"),
            CreateChapter("二", chapter_id=second),
            SetSection(second, "manuscript", text="乙。"),
            CreateChapter("三", chapter_id=third, position=1),
            SetSection(third, "manuscript", text="丙。"),
        ),
    )
    current = build_project_service().open_project(project.root)
    assert current.metadata.chapter_order == (first, third, second)
    moved = build_commit_service().commit(project, (MoveChapter(second, 0),))
    assert "MANUSCRIPT.md" in moved.changed_files
    assert build_project_service().open_project(project.root).metadata.chapter_order == (
        second,
        first,
        third,
    )
    build_commit_service().commit(project, (MergeChapters(second, first),))
    merged = build_project_service().read_chapter(project, second)
    assert merged.manuscript == "乙。\n\n甲。"
    split_id = uuid4()
    build_commit_service().commit(
        project, (SplitChapter(second, "甲。", "新章", new_chapter_id=split_id),)
    )
    assert build_project_service().read_chapter(project, split_id).manuscript == "甲。"
    build_commit_service().commit(
        project, (RenameChapter(split_id, "重命名"), DeleteChapter(third))
    )
    final = build_project_service().open_project(project.root)
    assert final.metadata.chapter_order == (second, split_id)
    manuscript = (project.root / "MANUSCRIPT.md").read_text(encoding="utf-8")
    assert "# 重命名" in manuscript
    assert "丙。" not in manuscript


def test_structural_commits_keep_filenames_aligned_with_chapter_order(tmp_path):
    project = _project(tmp_path)
    first, second, third, inserted = (uuid4() for _ in range(4))
    commits = build_commit_service()
    projects = build_project_service()
    commits.commit(
        project,
        (
            CreateChapter("一", chapter_id=first),
            SetSection(first, "manuscript", text="甲。"),
            CreateChapter("二", chapter_id=second),
            SetSection(second, "manuscript", text="乙。"),
            CreateChapter("三", chapter_id=third),
            SetSection(third, "manuscript", text="丙。"),
        ),
    )
    deleted = commits.commit(project, (DeleteChapter(second),))
    current = projects.open_project(project.root)
    assert current.metadata.chapter_order == (first, third)
    assert current.metadata.chapters[str(third)].filename == "0002.md"
    assert current.metadata.next_chapter_number == 3
    assert projects.read_chapter(current, third).manuscript == "丙。"
    assert not (project.root / "chapters/0003.md").exists()
    assert "chapters/0002.md" in deleted.changed_files
    assert "chapters/0003.md" in deleted.changed_files

    commits.commit(current, (CreateChapter("前传", position=0, chapter_id=inserted),))
    current = projects.open_project(project.root)
    assert current.metadata.chapter_order == (inserted, first, third)
    filenames = [
        current.metadata.chapters[str(chapter_id)].filename
        for chapter_id in current.metadata.chapter_order
    ]
    assert filenames == [
        "0001.md", "0002.md", "0003.md"
    ]
    assert projects.read_chapter(current, first).manuscript == "甲。"
    assert projects.read_chapter(current, third).manuscript == "丙。"

    commits.commit(current, (MoveChapter(third, 0),))
    current = projects.open_project(project.root)
    assert current.metadata.chapter_order == (third, inserted, first)
    titles = [
        projects.read_chapter(current, chapter_id).title
        for chapter_id in current.metadata.chapter_order
    ]
    assert titles == [
        "三", "前传", "一"
    ]
    assert current.metadata.chapters[str(third)].filename == "0001.md"
    assert current.metadata.chapters[str(first)].filename == "0003.md"


def test_next_structural_commit_normalizes_legacy_filename_gap(tmp_path):
    project = _project(tmp_path)
    first, second = uuid4(), uuid4()
    commits = build_commit_service()
    projects = build_project_service()
    commits.commit(
        project,
        (
            CreateChapter("一", chapter_id=first),
            CreateChapter("二", chapter_id=second),
            SetSection(second, "manuscript", text="保留的正文。"),
        ),
    )
    # Emulate a Project written by the earlier monotonic filename policy.
    old_path = project.root / "chapters/0002.md"
    old_path.rename(project.root / "chapters/0003.md")
    metadata_path = project.root / ".novel-agent/project.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["chapters"][str(second)]["filename"] = "0003.md"
    metadata["next_chapter_number"] = 4
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    legacy = projects.open_project(project.root)
    result = commits.commit(legacy, (MoveChapter(second, 1),))
    current = projects.open_project(project.root)
    assert current.metadata.chapters[str(second)].filename == "0002.md"
    assert current.metadata.next_chapter_number == 3
    assert projects.read_chapter(current, second).manuscript == "保留的正文。"
    assert not (project.root / "chapters/0003.md").exists()
    assert {"chapters/0002.md", "chapters/0003.md"} <= set(result.changed_files)


def test_renumbering_recovers_after_interrupted_commit(tmp_path):
    project = _project(tmp_path)
    first, second, third = (uuid4() for _ in range(3))
    build_commit_service().commit(
        project,
        (
            CreateChapter("一", chapter_id=first),
            CreateChapter("二", chapter_id=second),
            SetSection(second, "manuscript", text="乙。"),
            CreateChapter("三", chapter_id=third),
            SetSection(third, "manuscript", text="丙。"),
        ),
    )

    def interrupt(index, _path):
        if index == 1:
            raise RuntimeError("interrupted during renumbering")

    with pytest.raises(RuntimeError, match="renumbering"):
        CommitService(FileProjectRepository(), FileArtifactRepository(), interrupt).commit(
            project, (DeleteChapter(first),)
        )
    reopened = build_project_service().open_project(project.root)
    assert reopened.metadata.chapter_order == (second, third)
    filenames = [
        reopened.metadata.chapters[str(chapter_id)].filename
        for chapter_id in (second, third)
    ]
    assert filenames == ["0001.md", "0002.md"]
    assert build_project_service().read_chapter(reopened, second).manuscript == "乙。"
    assert build_project_service().read_chapter(reopened, third).manuscript == "丙。"
    assert not (project.root / "chapters/0003.md").exists()


def test_text_anchor_is_unique_and_no_uncommitted_writes(tmp_path):
    project = _project(tmp_path)
    chapter_id = _create_chapter(project, manuscript="雨声。雨声。")
    original = (project.root / "chapters/0001.md").read_bytes()
    with pytest.raises(DecisionRequired):
        build_commit_service().commit(
            project, (ReplaceText(chapter_id, "manuscript", "雨声", "风声"),)
        )
    assert (project.root / "chapters/0001.md").read_bytes() == original
    build_commit_service().commit(
        project, (ReplaceText(chapter_id, "manuscript", "雨声。雨声。", "风声。"),)
    )
    assert build_project_service().read_chapter(project, chapter_id).manuscript == "风声。"


def test_overlapping_section_changes_are_rejected_before_write(tmp_path):
    project = _project(tmp_path)
    chapter_id = _create_chapter(project)
    before = (project.root / "chapters/0001.md").read_bytes()
    with pytest.raises(InvalidChange, match="same Chapter section"):
        build_commit_service().commit(
            project,
            (
                SetSection(chapter_id, "outline", text="大纲甲"),
                SetSection(chapter_id, "outline", text="大纲乙"),
            ),
        )
    assert (project.root / "chapters/0001.md").read_bytes() == before


def test_prepared_commit_lifecycle_and_cancel(tmp_path):
    project = _project(tmp_path)
    chapter_id = uuid4()
    service = build_commit_service()
    prepared = service.prepare(project, (CreateChapter("第一章", chapter_id=chapter_id),))
    assert prepared.status == CommitStatus.PREPARED
    service.cancel(prepared)
    assert prepared.status == CommitStatus.CANCELLED
    with pytest.raises(InvalidChange):
        service.apply(prepared)
    assert build_project_service().open_project(project.root).metadata.chapter_order == ()

    prepared = service.prepare(project, (CreateChapter("第一章", chapter_id=chapter_id),))
    assert service.apply(prepared).status == "COMPLETED"
    assert prepared.status == CommitStatus.COMPLETED


def test_new_chapter_multi_artifact_commit_and_status_recovery(tmp_path):
    project = _project(tmp_path)
    chapter_id = uuid4()
    artifacts = build_artifact_service()
    session_id = project.metadata.initial_session_id
    summary = artifacts.create(
        project,
        session_id,
        ArtifactType.SUMMARY,
        "SECTION",
        "已确认概述",
        chapter_id=chapter_id,
        section="summary",
    )
    manuscript = artifacts.create(
        project,
        session_id,
        ArtifactType.MANUSCRIPT,
        "SECTION",
        "正文一字不改。",
        chapter_id=chapter_id,
        section="manuscript",
    )
    artifacts.approve(project, summary.ref)
    assert build_project_service().open_project(project.root).metadata.chapter_order == ()
    changes = (
        CreateChapter("第一章", chapter_id=chapter_id),
        SetSection(chapter_id, "summary", artifact=summary.ref),
        SetSection(chapter_id, "manuscript", artifact=manuscript.ref),
    )

    def interrupt_after_status(index, path):
        if path.endswith("statuses/0001.json"):
            raise RuntimeError("interrupt after status write")

    with pytest.raises(RuntimeError):
        CommitService(
            FileProjectRepository(), FileArtifactRepository(), interrupt_after_status
        ).commit(project, changes)
    reopened = build_project_service().open_project(project.root)
    chapter = build_project_service().read_chapter(reopened, chapter_id)
    assert chapter.summary == "已确认概述"
    assert chapter.manuscript == "正文一字不改。"
    assert artifacts.status(reopened, summary.ref).status == ArtifactStatus.COMMITTED
    assert artifacts.status(reopened, manuscript.ref).status == ArtifactStatus.COMMITTED
    assert "正文一字不改。" in (project.root / "MANUSCRIPT.md").read_text(encoding="utf-8")


def test_artifact_reference_and_status_are_project_local(tmp_path):
    service = build_project_service()
    first = service.create_project(tmp_path, "第一部", "会话")
    second = service.create_project(tmp_path, "第二部", "会话")
    chapter_id = uuid4()
    artifact = build_artifact_service().create(
        first,
        first.metadata.initial_session_id,
        ArtifactType.MANUSCRIPT,
        "SECTION",
        "候选",
        chapter_id=chapter_id,
        section="manuscript",
    )
    from novel_agent.domain.errors import ArtifactNotFound

    with pytest.raises(ArtifactNotFound):
        build_artifact_service().get(second, artifact.ref)
    build_artifact_service().discard(first, artifact.ref)
    from novel_agent.domain.errors import InvalidArtifact

    with pytest.raises(InvalidArtifact):
        build_artifact_service().revise(first, artifact.ref, "不能修订")


def test_unrelated_chapter_edit_does_not_stale_outline_artifact(tmp_path):
    project = _project(tmp_path)
    first = _create_chapter(project, "第一章", "第一章正文")
    second = _create_chapter(project, "第二章", "第二章正文")
    artifact = build_artifact_service().create(
        project,
        project.metadata.initial_session_id,
        ArtifactType.OUTLINE,
        "SECTION",
        "第一章大纲",
        chapter_id=first,
        section="outline",
    )
    build_commit_service().commit(project, (SetSection(second, "summary", text="第二章概述"),))
    build_commit_service().commit(project, (SetSection(first, "outline", artifact=artifact.ref),))
    assert build_project_service().read_section(project, first, "outline") == "第一章大纲"


def test_project_text_change_is_controlled_and_auxiliary_artifact_rejected(tmp_path):
    from novel_agent.domain.change import SetProjectText
    from novel_agent.domain.errors import InvalidArtifact

    project = _project(tmp_path)
    artifacts = build_artifact_service()
    report = artifacts.create(
        project,
        project.metadata.initial_session_id,
        ArtifactType.ANALYSIS,
        "PROJECT",
        "分析，不是正式项目内容",
    )
    with pytest.raises(InvalidArtifact):
        build_commit_service().commit(project, (SetProjectText(artifact=report.ref),))
    assert build_project_service().read_project_text(project) == "# 雨夜\n"
    result = build_commit_service().commit(project, (SetProjectText(text="# 雨夜\n\n世界观设定"),))
    assert result.changed_files == ("PROJECT.md",)
    assert build_project_service().read_project_text(project) == "# 雨夜\n\n世界观设定"
