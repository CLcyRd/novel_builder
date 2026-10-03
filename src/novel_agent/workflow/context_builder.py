"""Build Workflow context from fresh Project snapshots and exact Artifacts."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from novel_agent.domain.artifact import ArtifactRef
from novel_agent.domain.errors import InvalidChange
from novel_agent.domain.models import Project, ProjectReadSnapshot
from novel_agent.prompts.prompt import ContextBlock, GenerationContext
from novel_agent.services.artifact import ArtifactService
from novel_agent.services.draft import SECTIONS
from novel_agent.services.project import ProjectService
from novel_agent.workflow.models import (
    WorkflowRequest,
)
from novel_agent.workflow.single_state import _ref as _ref
from novel_agent.workflow.single_state import _State as _State

_CHARACTER_KEYWORDS = ("人物", "角色", "character")


class WorkflowContextBuilder:
    """Build one operation's formal/working evidence outside model execution."""

    def __init__(self, projects: ProjectService, artifacts: ArtifactService) -> None:
        self.projects = projects
        self.artifacts = artifacts

    def _artifact_block(self, project: Project, ref: ArtifactRef, kind: str) -> ContextBlock:
        artifact = self.artifacts.get(project, ref)
        status = self.artifacts.status(project, ref).status.value
        return ContextBlock(
            kind,
            artifact.content,
            source=f"Artifact {ref.artifact_id}@{ref.revision}",
            status=status,
            artifact_id=str(ref.artifact_id),
            revision=ref.revision,
            chapter_id=str(artifact.chapter_id) if artifact.chapter_id else None,
        )

    def _formal_snapshot(
        self, project: Project, request: WorkflowRequest, *, include_attachments: bool = False
    ) -> ProjectReadSnapshot:
        return self.projects.read_snapshot(
            project,
            reference_directories=("references",),
            reference_keywords=_CHARACTER_KEYWORDS,
            attachments=request.attachments if include_attachments else (),
        )

    @staticmethod
    def _context_chapters(snapshot: ProjectReadSnapshot, request: WorkflowRequest) -> list[UUID]:
        order = list(snapshot.project.metadata.chapter_order)
        if request.kind == "BACKFILL_CHAPTER":
            position = request.position
            return order[max(0, position - 1) : min(len(order), position + 1)]
        if request.kind == "REWRITE_CHAPTER":
            index = order.index(request.chapter_id)
            return order[max(0, index - 1) : min(len(order), index + 2)]
        return order[-1:]

    @staticmethod
    def _character_files(snapshot: ProjectReadSnapshot) -> list[tuple[str, str]]:
        return [
            (relative, text)
            for relative, text in snapshot.text_files.items()
            if relative.startswith("references/")
            and any(word in Path(relative).stem.casefold() for word in _CHARACTER_KEYWORDS)
        ]

    def _formal_blocks(
        self,
        project: Project,
        request: WorkflowRequest,
        *,
        snapshot: ProjectReadSnapshot | None = None,
    ) -> list[ContextBlock]:
        snapshot = snapshot or self._formal_snapshot(project, request)
        current = snapshot.project
        blocks = []
        for chapter_id in self._context_chapters(snapshot, request):
            entry = current.metadata.chapters[str(chapter_id)]
            chapter = snapshot.chapter(chapter_id)
            for section in SECTIONS:
                blocks.append(
                    ContextBlock(
                        (
                            "formal_target_"
                            if request.kind == "REWRITE_CHAPTER"
                            and chapter_id == request.chapter_id
                            else "formal_"
                        )
                        + section,
                        getattr(chapter, section),
                        source=f"chapters/{entry.filename}",
                        status="FORMAL",
                        chapter_id=str(chapter_id),
                    )
                )
        blocks.append(
            ContextBlock(
                "project_settings", snapshot.project_text, source="PROJECT.md", status="FORMAL"
            )
        )
        blocks.extend(
            ContextBlock("reference", text, source=relative)
            for relative, text in self._character_files(snapshot)
        )
        return blocks

    def _context(self, project: Project, request: WorkflowRequest, state: _State) -> str:
        snapshot = self._formal_snapshot(project, request, include_attachments=True)
        blocks = self._formal_blocks(project, request, snapshot=snapshot)
        blocks.append(
            ContextBlock(
                "chapter_brief",
                f"目标章节：{request.title}",
                source="WorkflowRequest",
                status="USER",
            )
        )
        for relative in request.attachments:
            blocks.append(
                ContextBlock("attachment", snapshot.text_files[relative], source=relative)
            )
        for reference in request.working_artifact_refs:
            revision = self.artifacts.get(project, reference)
            if revision.session_id != request.session_id:
                raise InvalidChange("Working Artifact belongs to another Session")
            blocks.append(
                self._artifact_block(
                    project, reference, "working_" + (revision.section or "context")
                )
            )
        for name in SECTIONS:
            if state.get(name + "_ref"):
                blocks.append(
                    self._artifact_block(project, _ref(state, name + "_ref"), "current_" + name)
                )
        return GenerationContext(blocks)

    def _formal_context(self, project: Project, request: WorkflowRequest) -> list[tuple[str, str]]:
        snapshot = self._formal_snapshot(project, request)
        current = snapshot.project
        files = []
        for chapter_id in self._context_chapters(snapshot, request):
            entry = current.metadata.chapters[str(chapter_id)]
            chapter = snapshot.chapter(chapter_id)
            files.append(
                (
                    f"chapters/{entry.filename}",
                    f"正式章节《{chapter.title}》正文（最高优先级）：\n{chapter.manuscript[-9000:]}\n"
                    f"大纲：{chapter.outline[:2000]}\n概述：{chapter.summary[:1000]}",
                )
            )
        files.append(("PROJECT.md", f"Project 设定：\n{snapshot.project_text}"))
        files.extend(
            (relative, f"人物参考资料 {relative}：\n{text[:5000]}")
            for relative, text in self._character_files(snapshot)
        )
        return files
