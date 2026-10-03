"""Composition root for the currently available Project API."""

from novel_agent.infrastructure.filesystem.artifact_repository import FileArtifactRepository
from novel_agent.infrastructure.filesystem.project_repository import FileProjectRepository
from novel_agent.services.artifact import ArtifactService
from novel_agent.services.commit import CommitService
from novel_agent.services.project import ProjectService
from novel_agent.services.session import SessionService
from novel_agent.workflow.batch_chapters import ImportNovelWorkflow, MultiChapterWorkflow
from novel_agent.workflow.create_chapter import (
    BackfillChapterWorkflow,
    ContinueChapterWorkflow,
    CreateChapterWorkflow,
    RewriteChapterWorkflow,
)
from novel_agent.workflow.generator import ChapterGenerator


def build_project_service() -> ProjectService:
    return ProjectService(FileProjectRepository())


def build_artifact_service() -> ArtifactService:
    return ArtifactService(FileArtifactRepository())


def build_commit_service() -> CommitService:
    return CommitService(FileProjectRepository(), FileArtifactRepository())


def build_session_service() -> SessionService:
    return SessionService(FileProjectRepository())


def build_create_chapter_workflow(generator: ChapterGenerator) -> CreateChapterWorkflow:
    """Compose M3.1 Workflow without coupling its business logic to CLI."""
    return CreateChapterWorkflow(
        build_project_service(),
        build_session_service(),
        build_artifact_service(),
        build_commit_service(),
        generator,
    )


def build_continue_chapter_workflow(generator: ChapterGenerator) -> ContinueChapterWorkflow:
    return ContinueChapterWorkflow(
        build_project_service(),
        build_session_service(),
        build_artifact_service(),
        build_commit_service(),
        generator,
    )


def build_backfill_chapter_workflow(generator: ChapterGenerator) -> BackfillChapterWorkflow:
    return BackfillChapterWorkflow(
        build_project_service(),
        build_session_service(),
        build_artifact_service(),
        build_commit_service(),
        generator,
    )


def build_rewrite_chapter_workflow(generator: ChapterGenerator) -> RewriteChapterWorkflow:
    return RewriteChapterWorkflow(
        build_project_service(),
        build_session_service(),
        build_artifact_service(),
        build_commit_service(),
        generator,
    )


def build_multi_chapter_workflow(generator: ChapterGenerator) -> MultiChapterWorkflow:
    return MultiChapterWorkflow(
        build_project_service(),
        build_session_service(),
        build_artifact_service(),
        build_commit_service(),
        generator,
    )


def build_import_novel_workflow(generator: ChapterGenerator) -> ImportNovelWorkflow:
    return ImportNovelWorkflow(
        build_project_service(),
        build_session_service(),
        build_artifact_service(),
        build_commit_service(),
        generator,
    )


__all__ = [
    "build_project_service",
    "build_artifact_service",
    "build_commit_service",
    "build_session_service",
    "build_create_chapter_workflow",
    "build_continue_chapter_workflow",
    "build_backfill_chapter_workflow",
    "build_rewrite_chapter_workflow",
    "build_multi_chapter_workflow",
    "build_import_novel_workflow",
]
