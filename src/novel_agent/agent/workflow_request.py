"""Identify creative requests, resolve targets, then construct reference-only requests."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from novel_agent.agent.workflow_intent import CreativePlan, PlannedChapter, resolved_creation
from novel_agent.domain.artifact import ArtifactType
from novel_agent.domain.chapter_selector import ordinal
from novel_agent.domain.errors import InvalidChange
from novel_agent.domain.models import Project, SessionMetadata
from novel_agent.services.artifact import ArtifactService
from novel_agent.services.project import ProjectService
from novel_agent.workflow.models import ChapterTarget, WorkflowRequest

_CHAPTER = re.compile(r"第([一二三四五六七八九十百零〇两\d]+)章")
_SOURCE = re.compile(r"(?:sources[/\\][^\s'\"，。]+\.(?:txt|md|pdf))", re.I)
_CREATIVE = re.compile(
    r"续写|补写|重写|改写|创作|导入|"
    r"写.{0,12}(?:章|小说|故事|正文|开头)|生成.{0,12}(?:章|小说|故事|正文)"
)


def accepts_workflow(prompt: str, session: SessionMetadata) -> bool:
    """Apply existing routing exclusions before any Project read or model invocation."""
    if re.search(r"自由协作|自由模式|不要.{0,3}工作流|不进入工作流", prompt):
        return False
    if re.search(r"只.{0,8}(?:大纲|概述)|先写大纲|不写正文", prompt):
        return False
    if re.search(r"[?？]\s*$|(?:请问|为什么|如何|是否|什么|吗|么)\s*$", prompt):
        return False
    explicit = bool(re.search(r"工作流|标准流程|按流程", prompt))
    return bool((explicit or session.execution_mode == "WORKFLOW") and _CREATIVE.search(prompt))


def chapter_title(number: int) -> str:
    return f"第{number}章"


@dataclass(frozen=True)
class ResolvedWorkflowTarget:
    kind: str
    title: str
    instruction: str
    chapter_id: UUID | None = None
    position: int | None = None
    source_path: str | None = None
    chapters: tuple[PlannedChapter, ...] = ()
    planned_total: int | None = None


class WorkflowTargetResolver:
    def __init__(self, projects: ProjectService) -> None:
        self.projects = projects

    def resolve(
        self,
        project: Project,
        prompt: str,
        attachments: Sequence[str | Path],
        plan: CreativePlan | None,
    ) -> ResolvedWorkflowTarget:
        """Resolve chapter arithmetic and precedence without creating Artifacts."""
        count = len(project.metadata.chapter_order)
        files = tuple(str(path) for path in attachments)
        directive = re.split(r"第[一二三四五六七八九十百零〇两\d]+章\s*[:：]", prompt, maxsplit=1)[
            0
        ]
        if "导入" in directive or (plan and plan.operation == "IMPORT_NOVEL"):
            source = next((name for name in files if name.startswith("sources/")), None)
            if source is None:
                match = _SOURCE.search(prompt)
                source = match.group(0).replace("\\", "/") if match else None
            if source is None:
                raise InvalidChange("Import requires a source file under Project sources/")
            return ResolvedWorkflowTarget("IMPORT_NOVEL", "导入小说", prompt, source_path=source)
        match = _CHAPTER.search(prompt)
        number = ordinal(match.group(1)) if match else None
        if "补写" in directive or (plan and plan.operation == "BACKFILL_CHAPTER"):
            if number is None:
                raise InvalidChange("Specify the missing Chapter number for backfill")
            return ResolvedWorkflowTarget(
                "BACKFILL_CHAPTER", chapter_title(number), prompt, position=number - 1
            )
        if (
            "重写" in directive
            or "改写" in directive
            or (plan and plan.operation == "REWRITE_CHAPTER")
        ):
            if number is None or number < 1 or number > count:
                raise InvalidChange("Specify an existing Chapter number for rewrite")
            chapter_id = project.metadata.chapter_order[number - 1]
            title = self.projects.read_chapter(project, chapter_id).title
            return ResolvedWorkflowTarget("REWRITE_CHAPTER", title, prompt, chapter_id=chapter_id)
        if (
            "续写" in directive
            or "下一章" in directive
            or (plan and plan.operation == "CONTINUE_CHAPTER")
        ):
            return ResolvedWorkflowTarget("CONTINUE_CHAPTER", chapter_title(count + 1), prompt)
        resolved, total, preamble = resolved_creation(prompt, plan, existing_count=count)
        if len(resolved) > 1:
            return ResolvedWorkflowTarget(
                "MULTI_CHAPTER",
                f"{len(resolved)}章",
                preamble,
                chapters=resolved,
                planned_total=total,
            )
        title = chapter_title(resolved[0].number if resolved else (number or count + 1))
        return ResolvedWorkflowTarget("CREATE_CHAPTER", title, prompt)


class WorkflowRequestBuilder:
    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts

    def build(
        self,
        project: Project,
        session: SessionMetadata,
        target: ResolvedWorkflowTarget,
        attachments: Sequence[str | Path],
    ) -> WorkflowRequest:
        chapters = tuple(self._chapter(project, session, chapter) for chapter in target.chapters)
        return WorkflowRequest(
            kind=target.kind,
            title=target.title,
            project_id=project.project_id,
            session_id=session.session_id,
            instruction=target.instruction,
            attachments=tuple(str(path) for path in attachments),
            **({"chapter_id": target.chapter_id} if target.chapter_id is not None else {}),
            position=target.position,
            source_path=target.source_path,
            chapters=chapters,
            planned_total=target.planned_total,
        )

    def _chapter(
        self, project: Project, session: SessionMetadata, chapter: PlannedChapter
    ) -> ChapterTarget:
        chapter_id = uuid4()
        brief, brief_ref = chapter.brief, None
        if len(brief) > 2000:
            brief_ref = self.artifacts.create(
                project,
                session.session_id,
                ArtifactType.TASK_BRIEF,
                "CHAPTER",
                brief,
                chapter_id=chapter_id,
            ).ref
            brief = ""
        return ChapterTarget(
            chapter_id=chapter_id,
            title=chapter_title(chapter.number),
            brief=brief,
            brief_ref=brief_ref,
        )
