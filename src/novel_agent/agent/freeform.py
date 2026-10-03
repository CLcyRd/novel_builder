"""Project-aware Freeform agent with controlled Artifact and Commit tools."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import UUID, uuid5

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from deepagents import (
    create_deep_agent as create_deep_agent,
)

from novel_agent.agent.freeform_draft import FreeformDraftController
from novel_agent.agent.freeform_inputs import FreeformInputBuilder
from novel_agent.agent.freeform_policy import _chapter_delete_baselines as _chapter_delete_baselines
from novel_agent.agent.freeform_policy import _has_commit_intent as _has_commit_intent
from novel_agent.agent.freeform_policy import (
    _requested_chapter_deletion as _requested_chapter_deletion,
)
from novel_agent.agent.freeform_recorder import FreeformRunRecorder
from novel_agent.agent.freeform_runner import FreeformModelRunner
from novel_agent.agent.freeform_tools import FreeformToolFactory
from novel_agent.agent.freeform_types import FreeformRunScope
from novel_agent.agent.freeform_types import RunResult as RunResult
from novel_agent.agent.freeform_types import TaskContext as TaskContext
from novel_agent.domain.artifact import ArtifactRef, ArtifactRevision
from novel_agent.domain.change import CreateChapter, DeleteChapter, SetProjectText, SetSection
from novel_agent.domain.errors import (
    DecisionRequired,
    InvalidArtifact,
    InvalidChange,
)
from novel_agent.domain.models import Project, SessionMetadata
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.infrastructure.models.langchain import build_chat_model as build_chat_model
from novel_agent.runtime.events import RuntimeEvent
from novel_agent.runtime.state import SessionState, SessionStateStore
from novel_agent.services.artifact import ArtifactService
from novel_agent.services.commit import CommitResult, CommitService
from novel_agent.services.draft import SECTIONS, DraftService
from novel_agent.services.project import ProjectService
from novel_agent.services.session import SessionService
from novel_agent.services.skill import SkillService

# The SDK's automatic general-purpose subagent otherwise inherits write tools.
register_harness_profile(
    "openai", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False))
)


class FreeformAgent:
    def __init__(
        self,
        projects: ProjectService,
        sessions: SessionService,
        artifacts: ArtifactService,
        commits: CommitService,
        models: ModelGateway,
    ) -> None:
        self.projects = projects
        self.sessions = sessions
        self.artifacts = artifacts
        self.commits = commits
        self.models = models
        self.skills = SkillService()
        self.drafts = DraftService(artifacts)

    def _sync_freeform_draft(self, project: Project, session: SessionMetadata, state: SessionState):
        pending = state.pending_draft
        if not pending:
            return None
        sync = self.drafts.sync(
            project, session.session_id, UUID(pending["task_id"]), UUID(pending["chapter_id"])
        )
        if sync:
            for ref in sync.refs.values():
                state.display(ref)
            state.current_ref = sync.refs.get(pending["section"], state.current_ref)
        return sync

    def _export_freeform_draft(
        self,
        project: Project,
        session: SessionMetadata,
        state: SessionState,
        ref: ArtifactRef,
    ) -> str | None:
        artifact = self.artifacts.get(project, ref)
        if not artifact.chapter_id or artifact.section not in SECTIONS:
            return None
        chapter_id = artifact.chapter_id
        title = state.draft_titles.get(str(chapter_id))
        if not title:
            title = self.projects.read_chapter(project, chapter_id).title
        task_id = uuid5(session.session_id, f"freeform:{chapter_id}")
        conflict = False
        try:
            path = self.drafts.export(
                project, session.session_id, task_id, chapter_id, title, {artifact.section: ref}
            )
        except DecisionRequired as exc:
            path = exc.paths[0]
            conflict = True
        state.pending_draft = {
            "task_id": str(task_id),
            "chapter_id": str(chapter_id),
            "section": artifact.section,
        }
        if conflict:
            state.pending_draft["decision"] = True
        return path

    def _state(
        self, project: Project, session: SessionMetadata
    ) -> tuple[SessionStateStore, SessionState]:
        self.sessions.load(project, session.session_id)
        store = SessionStateStore(project.root)
        return store, store.load(session.session_id)

    def resolve_ref(self, project: Project, session: SessionMetadata, selector: str) -> ArtifactRef:
        """Resolve a UUID or unique prefix, optionally with an exact @revision."""
        token, _, revision_text = selector.partition("@")
        matches = [
            index
            for index in self.artifacts.list(project, session.session_id)
            if str(index.artifact_id).startswith(token.lower())
        ]
        if len(matches) != 1:
            raise InvalidArtifact("Artifact ID is missing or ambiguous")
        index = matches[0]
        revision = int(revision_text) if revision_text else index.latest_revision
        return self.artifacts.get_by_id(project, index.artifact_id, revision).ref

    def show_artifact(
        self, project: Project, session: SessionMetadata, selector: str
    ) -> ArtifactRevision:
        ref = self.resolve_ref(project, session, selector)
        revision = self.artifacts.get(project, ref)
        store, state = self._state(project, session)
        state.display(ref)
        store.save(session.session_id, state)
        return revision

    def commit_visible(
        self, project: Project, session: SessionMetadata, selectors: Sequence[str]
    ) -> CommitResult:
        store, state = self._state(project, session)
        if not selectors:
            if state.current_ref is None:
                raise InvalidChange("No current Artifact")
            refs = [state.current_ref]
        else:
            refs = []
            for selector in selectors:
                ref = self.resolve_ref(project, session, selector)
                if "@" not in selector:
                    visible = [
                        item for item in state.visible_refs if item.artifact_id == ref.artifact_id
                    ]
                    if len(visible) != 1:
                        raise InvalidChange("Specify a displayed exact Artifact revision with @N")
                    ref = visible[0]
                refs.append(ref)
        records = []
        for ref in tuple(refs):
            record = self.drafts.find(project, session.session_id, ref)
            if not record:
                continue
            if selectors and record["active"] and self.drafts.dirty(project, record):
                raise DecisionRequired(
                    "指定 revision 与手动工作稿可能不同。请先确认手动稿，"
                    "或使用 /commit current 提交已保存编辑。",
                    (record["path"],),
                )
            sync = self.drafts.sync(
                project, session.session_id, UUID(record["task_id"]), UUID(record["chapter_id"])
            )
            if not selectors and sync:
                refs = list(sync.refs.values())
                for updated in refs:
                    state.display(updated)
            records.append(record)
        if any(ref not in state.visible_refs for ref in refs):
            raise InvalidChange("Show the exact Artifact revision before Commit")
        changes = []
        created_chapters: set[UUID] = set()
        current = self.projects.open_project(project.root)
        for ref in refs:
            artifact = self.artifacts.get(current, ref)
            if artifact.session_id != session.session_id:
                raise InvalidArtifact("Artifact belongs to another Session")
            if artifact.scope == "PROJECT":
                changes.append(SetProjectText(artifact=ref))
            elif artifact.chapter_id and artifact.section:
                if (
                    artifact.chapter_id not in current.metadata.chapter_order
                    and artifact.chapter_id not in created_chapters
                ):
                    title = state.draft_titles.get(str(artifact.chapter_id))
                    if not title:
                        raise InvalidChange("New Chapter title is missing")
                    changes.append(CreateChapter(title=title, chapter_id=artifact.chapter_id))
                    created_chapters.add(artifact.chapter_id)
                changes.append(SetSection(artifact.chapter_id, artifact.section, artifact=ref))
            else:
                raise InvalidArtifact("This Artifact type is not committable")
        result = self.commits.commit(current, tuple(changes))
        for record in records:
            self.drafts.clean(
                project, session.session_id, UUID(record["task_id"]), UUID(record["chapter_id"])
            )
        if state.pending_draft and any(
            record["task_id"] == state.pending_draft["task_id"] for record in records
        ):
            state.pending_draft = None
        store.save(session.session_id, state)
        return result

    def commit_chapter_deletion(
        self, project: Project, chapter_id: UUID, expected_baselines: dict[str, str | None]
    ) -> tuple[CommitResult, str, str]:
        """Apply a structural deletion only against the Project version the user addressed."""
        entry = project.metadata.chapters.get(str(chapter_id))
        if entry is None:
            raise InvalidChange(f"Chapter does not exist: {chapter_id}")
        title = self.projects.read_chapter(project, chapter_id).title
        prepared = self.commits.prepare(project, (DeleteChapter(chapter_id),))
        if prepared.baselines != expected_baselines:
            raise DecisionRequired("Project changed since the Chapter was identified")
        commit = self.commits.apply(prepared)
        return commit, title, entry.filename

    async def run(
        self,
        project: Project,
        session: SessionMetadata,
        prompt: str,
        attachments: Sequence[str | Path] = (),
        on_event: Callable[[RuntimeEvent], None] | None = None,
        on_content: Callable[[str], None] | None = None,
        allow_workflow_chat: bool = False,
    ) -> RunResult:
        current = self.projects.open_project(project.root)
        session = self.sessions.load(current, session.session_id)
        if session.execution_mode == "WORKFLOW" and not allow_workflow_chat:
            raise InvalidChange(
                "Workflow mode is saved, but fixed Workflows arrive in Phase 3; switch to FREEFORM"
            )
        model_alias = session.current_model or self.models.default_model
        definition = self.models.definition(model_alias)
        store, state = self._state(current, session)
        result = RunResult(response="", model=definition.model_id)
        scope = FreeformRunScope(
            self, current, session, store, state, definition, result, on_event, on_content
        )
        prepared = FreeformDraftController.handle(scope, prompt)
        if isinstance(prepared, RunResult):
            return prepared
        prompt = prepared
        attachments_text = FreeformInputBuilder.prepare(scope, prompt, attachments)
        tools = FreeformToolFactory(scope).build()
        runner = FreeformModelRunner(build_chat_model, create_deep_agent)
        graph = runner.prepare(scope, tools)
        messages = FreeformInputBuilder.messages(scope, prompt, attachments_text)
        try:
            response = await runner.execute(scope, graph, messages, tools)
            return FreeformRunRecorder.finish(scope, prompt, response)
        except BaseException as exc:
            scope.emit(
                "Cancelled"
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt))
                else "Failed",
                type(exc).__name__,
            )
            raise
