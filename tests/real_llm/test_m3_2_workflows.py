"""Representative real-model M3.2 gates; opt in with RUN_REAL_LLM_TESTS=1."""

import os
from pathlib import Path
from uuid import uuid4

import pytest

from novel_agent.application import (
    build_backfill_chapter_workflow,
    build_commit_service,
    build_continue_chapter_workflow,
    build_import_novel_workflow,
    build_multi_chapter_workflow,
    build_project_service,
    build_rewrite_chapter_workflow,
)
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.workflow.generator import LLMChapterGenerator
from novel_agent.workflow.models import ChapterTarget, WorkflowRequest

pytestmark = pytest.mark.real_llm


class RecordingGenerator(LLMChapterGenerator):
    def __init__(self, gateway):
        super().__init__(gateway)
        self.calls = []

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, context))
        return super().generate(stage, instruction, context, feedback=feedback, model=model)


@pytest.fixture
def live(tmp_path):
    if os.environ.get("RUN_REAL_LLM_TESTS") != "1":
        pytest.skip("Explicit real-model opt-in required")
    gateway = ModelGateway.from_env(Path(__file__).resolve().parents[2] / ".env")
    project = build_project_service().create_project(tmp_path, "M3.2 Live", "Main")
    print(f"model={gateway.definition().model_id}")
    return project, RecordingGenerator(gateway)


def seed(project, title, manuscript):
    chapter_id = uuid4()
    build_commit_service().commit(
        project,
        (
            CreateChapter(title, chapter_id=chapter_id),
            SetSection(chapter_id, "manuscript", text=manuscript),
        ),
    )
    return chapter_id


def task(project, kind, **fields):
    return WorkflowRequest(
        project_id=project.project_id,
        session_id=project.metadata.initial_session_id,
        kind=kind,
        title=fields.pop("title", "新章节"),
        instruction="写简短悬疑故事，每章正文约80字，概述和大纲各用一两句。",
        auto_approval=True,
        **fields,
    )


def test_live_continue_uses_previous_chapter_and_character_notes(live):
    project, generator = live
    seed(project, "第一章", "林舟在灯塔找到了铜钥匙。")
    (project.root / "references" / "characters.md").write_text(
        "林舟是失忆的守塔人。",
        encoding="utf-8",
    )
    request = task(project, "CONTINUE_CHAPTER", title="第二章")
    done = build_continue_chapter_workflow(generator).start(project, request)
    assert done.status == "COMPLETED" and done.result.commit
    assert "铜钥匙" in generator.calls[0][1]
    assert "失忆的守塔人" in generator.calls[0][1]
    assert build_project_service().read_chapter(project, request.chapter_id).manuscript.strip()


def test_live_backfill_uses_later_chapter_constraint(live):
    project, generator = live
    first = seed(project, "第一章", "林舟得知船长正准备离港。")
    later = seed(project, "第三章", "船长已经失踪，林舟在空船上发现血迹。")
    request = task(project, "BACKFILL_CHAPTER", position=1, title="第二章")
    done = build_backfill_chapter_workflow(generator).start(project, request)
    assert done.status == "COMPLETED" and done.result.commit
    assert "船长已经失踪" in generator.calls[0][1]
    assert build_project_service().open_project(project.root).metadata.chapter_order == (
        first,
        request.chapter_id,
        later,
    )


def test_live_rewrite_preserves_chapter_identity(live):
    project, generator = live
    chapter_id = seed(project, "第一章", "旧正文：林舟没有走进钟楼。")
    request = task(project, "REWRITE_CHAPTER", chapter_id=chapter_id, title="第一章")
    done = build_rewrite_chapter_workflow(generator).start(project, request)
    assert done.status == "COMPLETED" and done.result.commit
    current = build_project_service().open_project(project.root)
    assert current.metadata.chapter_order == (chapter_id,)
    assert build_project_service().read_chapter(current, chapter_id).manuscript.strip()
    assert "旧正文" in generator.calls[0][1]


def test_live_multi_all_summaries_before_outlines_and_commit(live):
    project, generator = live
    targets = (ChapterTarget(title="第一章"), ChapterTarget(title="第二章"))
    request = task(project, "MULTI_CHAPTER", chapters=targets)
    done = build_multi_chapter_workflow(generator).start(project, request)
    assert done.status == "COMPLETED" and done.result.commit
    stages = [stage for stage, _ in generator.calls]
    assert stages[:2] == ["summary", "summary"]
    assert stages[2:4] == ["outline", "outline"]
    assert len(done.result.chapter_ids) == 2
    assert all(
        build_project_service().read_chapter(project, chapter_id).manuscript.strip()
        for chapter_id in done.result.chapter_ids
    )


def test_live_import_preserves_source_manuscript(live):
    project, generator = live
    source = project.root / "sources" / "existing.txt"
    source.write_text("林舟在雨夜推开钟楼的门，里面传来熟悉的脚步声。", encoding="utf-8")
    request = task(project, "IMPORT_NOVEL", source_path="sources/existing.txt")
    done = build_import_novel_workflow(generator).start(project, request)
    assert done.status == "COMPLETED" and done.result.commit
    assert len(done.result.chapter_ids) == 1
    chapter = build_project_service().read_chapter(project, done.result.chapter_ids[0])
    assert chapter.manuscript == source.read_text(encoding="utf-8")
    assert chapter.summary.strip() and chapter.outline.strip()
    assert [stage for stage, _ in generator.calls] == ["summary", "outline"]
