from uuid import uuid4

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

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
from novel_agent.domain.errors import InvalidChange, UnsafeProjectPath
from novel_agent.workflow.import_source import extract_novel_source
from novel_agent.workflow.models import ChapterTarget, GateResponse, WorkflowRequest


class Generator:
    def __init__(self):
        self.calls = []

    def generate(self, stage, instruction, context, *, feedback="", model=None):
        self.calls.append((stage, instruction, context, feedback))
        return f"{stage} 内容 {feedback}".strip()


def project_at(tmp_path):
    return build_project_service().create_project(tmp_path, "M3.2", "主会话")


def formal_chapter(project, title, text):
    chapter_id = uuid4()
    build_commit_service().commit(
        project,
        (
            CreateChapter(title, chapter_id=chapter_id),
            SetSection(chapter_id, "manuscript", text=text),
        ),
    )
    return chapter_id


def request(project, kind, **fields):
    return WorkflowRequest(
        project_id=project.project_id,
        session_id=project.metadata.initial_session_id,
        kind=kind,
        title=fields.pop("title", "目标章节"),
        instruction="创作悬疑章节",
        **fields,
    )


def approve(workflow, project, snapshot, action="APPROVE", feedback=None):
    return workflow.resume(
        project,
        snapshot.workflow_id,
        GateResponse(
            gate_id=snapshot.gate.gate_id,
            action=action,
            feedback=feedback,
        ),
    )


def finish_single(workflow, project, request):
    plan = workflow.start(project, request)
    assert plan.gate.stage == "SUMMARY"
    plan = approve(workflow, project, plan)
    assert plan.gate.stage == "OUTLINE"
    manuscript = approve(workflow, project, plan)
    assert manuscript.gate.stage == "MANUSCRIPT"
    return approve(workflow, project, manuscript)


def test_continue_reads_previous_chapter_and_characters(tmp_path):
    project = project_at(tmp_path)
    formal_chapter(project, "第一章", "前章独有线索：铜钥匙")
    (project.root / "references" / "characters.md").write_text(
        "林舟是失忆的守塔人",
        encoding="utf-8",
    )
    generator = Generator()
    workflow = build_continue_chapter_workflow(generator)
    req = request(project, "CONTINUE_CHAPTER", title="第二章")
    result = finish_single(workflow, project, req)
    assert result.status == "COMPLETED"
    assert "铜钥匙" in generator.calls[0][2]
    assert "失忆的守塔人" in generator.calls[0][2]
    assert (
        build_project_service().open_project(project.root).metadata.chapter_order[-1]
        == req.chapter_id
    )


def test_backfill_reads_later_constraint_and_inserts_at_target(tmp_path):
    project = project_at(tmp_path)
    first = formal_chapter(project, "第一章", "主角找到地图")
    later = formal_chapter(project, "第三章", "后章约束：船长已经失踪")
    generator = Generator()
    workflow = build_backfill_chapter_workflow(generator)
    req = request(project, "BACKFILL_CHAPTER", title="第二章", position=1)
    result = finish_single(workflow, project, req)
    assert result.status == "COMPLETED"
    assert "船长已经失踪" in generator.calls[0][2]
    assert build_project_service().open_project(project.root).metadata.chapter_order == (
        first,
        req.chapter_id,
        later,
    )


def test_rewrite_preserves_identity_and_checks_character_target(tmp_path):
    project = project_at(tmp_path)
    target = formal_chapter(project, "旧标题", "待重写的正文")
    generator = Generator()
    workflow = build_rewrite_chapter_workflow(generator)
    req = request(project, "REWRITE_CHAPTER", chapter_id=target, target_characters=100)
    plan = workflow.start(project, req)
    plan = approve(workflow, project, plan)
    manuscript = approve(workflow, project, plan)
    report = next(
        ref for ref in manuscript.gate.artifact_refs if ref != manuscript.gate.artifact_refs[0]
    )
    from novel_agent.application import build_artifact_service

    assert "字数检查" in build_artifact_service().get(project, report).content
    result = approve(workflow, project, manuscript)
    assert result.status == "COMPLETED"
    current = build_project_service().open_project(project.root)
    assert current.metadata.chapter_order == (target,)
    assert build_project_service().read_chapter(current, target).manuscript == "manuscript 内容"


def test_multi_stage_order_and_no_early_formal_write(tmp_path):
    project = project_at(tmp_path)
    generator = Generator()
    workflow = build_multi_chapter_workflow(generator)
    targets = (ChapterTarget(title="第一章"), ChapterTarget(title="第二章"))
    req = request(project, "MULTI_CHAPTER", chapters=targets)
    snapshot = workflow.start(project, req)
    assert snapshot.gate.stage == "SUMMARIES" and len(snapshot.gate.artifact_refs) == 2
    assert [call[0] for call in generator.calls] == ["summary", "summary"]
    assert not build_project_service().list_chapters(project)
    snapshot = approve(workflow, project, snapshot)
    assert snapshot.gate.stage == "OUTLINES" and len(snapshot.gate.artifact_refs) == 1
    snapshot = approve(workflow, project, snapshot)
    assert snapshot.gate.stage == "OUTLINES"
    snapshot = approve(workflow, project, snapshot)
    assert snapshot.gate.stage == "MANUSCRIPT"
    snapshot = approve(workflow, project, snapshot)
    assert snapshot.gate.stage == "MANUSCRIPT"
    assert not build_project_service().list_chapters(project)
    snapshot = approve(workflow, project, snapshot)
    assert snapshot.status == "COMPLETED"
    assert snapshot.result.chapter_ids == tuple(target.chapter_id for target in targets)
    assert (
        build_project_service().open_project(project.root).metadata.chapter_order
        == snapshot.result.chapter_ids
    )
    assert [call[0] for call in generator.calls] == [
        "summary",
        "summary",
        "outline",
        "outline",
        "manuscript",
        "review",
        "manuscript",
        "review",
    ]


def test_multi_collective_summary_feedback_reaches_every_chapter(tmp_path):
    project = project_at(tmp_path)
    generator = Generator()
    workflow = build_multi_chapter_workflow(generator)
    req = request(
        project,
        "MULTI_CHAPTER",
        chapters=(
            ChapterTarget(title="第一章"),
            ChapterTarget(title="第二章"),
        ),
    )
    first = workflow.start(project, req)
    revised = approve(workflow, project, first, "REVISE", "两章都要加入红色线索")
    assert revised.gate.stage == "SUMMARIES"
    assert [call[3] for call in generator.calls[-2:]] == [
        "两章都要加入红色线索",
        "两章都要加入红色线索",
    ]
    assert workflow.exit(project, req.workflow_id).status == "CANCELLED"


def test_import_txt_and_no_heading_one_chapter(tmp_path):
    project = project_at(tmp_path)
    source = project.root / "sources" / "book.txt"
    source.write_text("第一章 雨夜\n林舟收到信。\n第二章 钟楼\n林舟找到钟楼。", encoding="utf-8")
    generator = Generator()
    workflow = build_import_novel_workflow(generator)
    req = request(project, "IMPORT_NOVEL", source_path="sources/book.txt")
    snapshot = workflow.start(project, req)
    assert snapshot.gate.stage == "SUMMARIES" and len(snapshot.gate.artifact_refs) == 2
    snapshot = approve(workflow, project, snapshot)
    assert snapshot.gate.stage == "OUTLINES" and len(snapshot.gate.artifact_refs) == 2
    assert not build_project_service().list_chapters(project)
    done = approve(workflow, project, snapshot)
    assert done.status == "COMPLETED" and len(done.result.chapter_ids) == 2
    first = build_project_service().read_chapter(project, done.result.chapter_ids[0])
    assert first.manuscript == "林舟收到信。"
    source.write_text("没有标题的整篇正文", encoding="utf-8")
    parsed = extract_novel_source(project.root, "sources/book.txt")
    assert len(parsed) == 1 and parsed[0].manuscript == "没有标题的整篇正文"


def test_import_outline_batches_of_ten_and_exit(tmp_path):
    project = project_at(tmp_path)
    (project.root / "sources" / "many.md").write_text(
        "\n".join(f"# 第{i}章\n第{i}章正文" for i in range(1, 12)),
        encoding="utf-8",
    )
    workflow = build_import_novel_workflow(Generator())
    req = request(project, "IMPORT_NOVEL", source_path="sources/many.md")
    summaries = workflow.start(project, req)
    first_batch = approve(workflow, project, summaries)
    assert first_batch.gate.stage == "OUTLINES"
    assert len(first_batch.gate.artifact_refs) == 10
    second_batch = approve(workflow, project, first_batch)
    assert len(second_batch.gate.artifact_refs) == 1
    assert workflow.exit(project, req.workflow_id).status == "CANCELLED"
    assert not build_project_service().list_chapters(project)


def test_import_rejects_empty_pdf_and_unsafe_source(tmp_path):
    project = project_at(tmp_path)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with (project.root / "sources" / "empty.pdf").open("wb") as stream:
        writer.write(stream)
    with pytest.raises(InvalidChange, match="no extractable text"):
        extract_novel_source(project.root, "sources/empty.pdf")
    with pytest.raises(UnsafeProjectPath):
        extract_novel_source(project.root, "../outside.txt")


def test_import_extracts_text_pdf_without_ocr(tmp_path):
    project = project_at(tmp_path)
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 10 150 Td (Chapter 1) Tj 0 -20 Td (Imported body) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    with (project.root / "sources" / "novel.pdf").open("wb") as stream:
        writer.write(stream)
    chapters = extract_novel_source(project.root, "sources/novel.pdf")
    assert len(chapters) == 1
    assert chapters[0].title == "Chapter 1"
    assert chapters[0].manuscript == "Imported body"


def test_import_auto_stops_at_decision_when_source_changes(tmp_path):
    project = project_at(tmp_path)
    source = project.root / "sources" / "draft.txt"
    source.write_text("旧版正文", encoding="utf-8")

    class ChangingGenerator(Generator):
        changed = False

        def generate(self, stage, instruction, context, *, feedback="", model=None):
            result = super().generate(stage, instruction, context, feedback=feedback, model=model)
            if stage == "outline" and not self.changed:
                self.changed = True
                source.write_text("新版正文", encoding="utf-8")
            return result

    generator = ChangingGenerator()
    workflow = build_import_novel_workflow(generator)
    req = request(project, "IMPORT_NOVEL", source_path="sources/draft.txt", auto_approval=True)
    waiting = workflow.start(project, req)
    assert waiting.status == "WAITING_DECISION" and waiting.gate.kind == "DECISION"
    assert "sources/draft.txt" in waiting.gate.paths
    assert not build_project_service().list_chapters(project)
    reopened = build_import_novel_workflow(generator)
    assert reopened.load_active(project, req.session_id) == waiting
    done = approve(reopened, project, waiting, "REGENERATE")
    assert done.status == "COMPLETED"
    chapter = build_project_service().read_chapter(project, done.result.chapter_ids[0])
    assert chapter.manuscript == "新版正文"
