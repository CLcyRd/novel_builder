"""Command and attachment shell; creative decisions remain in the Agent."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from pathlib import Path

from novel_agent.agent.freeform import RunResult
from novel_agent.agent.main import MainAgent
from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
    build_session_service,
)
from novel_agent.cli.commands import CLICommandHandlers
from novel_agent.cli.types import CLIResult as CLIResult
from novel_agent.config.setting import DEFAULT_SETTINGS, Settings
from novel_agent.domain.models import Project, SessionMetadata
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.runtime.events import RuntimeEvent
from novel_agent.runtime.history import SessionHistoryStore

_ATTACHMENT = re.compile(r'(?<!\S)@(?:"([^"]+)"|\'([^\']+)\'|(\S+))')


def parse_attachments(line: str) -> tuple[str, tuple[str, ...]]:
    """Only user input passes here; model output and file contents never do."""
    names: list[str] = []

    def replace(match: re.Match[str]) -> str:
        names.append(next(value for value in match.groups() if value is not None))
        return " "

    prompt = _ATTACHMENT.sub(replace, line).strip()
    if not prompt:
        raise ValueError("A chat prompt is required with attachments")
    return prompt, tuple(names)


class NovelCLI:
    def __init__(
        self,
        workspace: Path,
        env_path: Path | None = None,
        *,
        models: ModelGateway | None = None,
        settings: Settings = DEFAULT_SETTINGS,
    ) -> None:
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.projects = build_project_service()
        self.sessions = build_session_service()
        self.artifacts = build_artifact_service()
        self.commits = build_commit_service()
        if models is None and env_path is None:
            raise ValueError("Model configuration path is required")
        self.models = models or ModelGateway.from_env(env_path)
        self.settings = settings
        self.agent = MainAgent(
            self.projects, self.sessions, self.artifacts, self.commits, self.models
        )
        self.project: Project | None = None
        self.session: SessionMetadata | None = None

    def _selected(self) -> tuple[Project, SessionMetadata]:
        if self.project is None or self.session is None:
            raise ValueError("Open a Project and Session first")
        self.project = self.projects.open_project(self.project.root)
        self.session = self.sessions.load(self.project, self.session.session_id)
        return self.project, self.session

    async def execute(
        self,
        line: str,
        *,
        on_event: Callable[[RuntimeEvent], None] | None = None,
        on_content: Callable[[str], None] | None = None,
    ) -> CLIResult:
        line = line.strip()
        if not line:
            return CLIResult("")
        if line.startswith("/"):
            return self._command(line)
        project, session = self._selected()
        prompt, attachments = parse_attachments(line)
        # Validate every attachment before Agent invocation; no partial call.
        for name in attachments:
            self.projects.resolve_attachment(project, name)
        run = await self.agent.run(
            project, session, prompt, attachments, on_event=on_event, on_content=on_content
        )
        SessionHistoryStore(project.root, self.settings).record_exchange(
            session.session_id, line, run.response, tuple(run.display_refs)
        )
        return self._present_run(project, run)

    def _present_run(self, project: Project, run: RunResult) -> CLIResult:
        previews = (
            [self._format_artifact(self.artifacts.get(project, ref)) for ref in run.display_refs]
            if not run.file_review
            else []
        )
        return CLIResult(run.response, run=run, artifact_text="\n\n".join(previews))

    def prompt_label(self) -> str:
        if self.project is None or self.session is None:
            return "novel> "
        snapshot = self.agent.load_active(*self._selected())
        if snapshot and snapshot.gate:
            if "KEEP" in snapshot.gate.options:
                return "decision[1/2/3]> "
            return "approve[y/n/e]> " if snapshot.gate.kind == "APPROVAL" else "decision[1/2]> "
        if self.agent.load_routing_decision(*self._selected()):
            return "decision[reply]> "
        pending = self.agent._state(*self._selected())[1].pending_draft
        if pending and pending.get("decision"):
            return "decision[1/2/3]> "
        return "novel> "

    def _workflow_status(self, project: Project, session: SessionMetadata) -> str:
        snapshot = self.agent.load_active(project, session)
        if snapshot is None:
            pending = self.agent.load_routing_decision(project, session)
            if pending:
                return f"No active Workflow\nRouting Decision: {pending['reason']}"
            return "No active Workflow"
        gate = snapshot.gate
        lines = [
            f"Workflow: {snapshot.kind}",
            f"ID: {snapshot.workflow_id}",
            f"Status: {snapshot.status}",
        ]
        if gate:
            lines.extend((f"Stage: {gate.stage}", f"Gate: {gate.gate_id}"))
            if gate.reason:
                lines.append(f"Reason: {gate.reason}")
            lines.extend(
                f"Artifact: {ref.artifact_id}@{ref.revision} "
                f"[{self.artifacts.status(project, ref).status.value}]"
                for ref in gate.artifact_refs
            )
            paths = set()
            for ref in gate.artifact_refs:
                record = self.agent.drafts.find(project, session.session_id, ref)
                if record and record["active"] and record["path"] not in paths:
                    paths.add(record["path"])
                    lines.append(f"Draft: {record['path']}")
        return "\n".join(lines)

    def _command(self, line: str) -> CLIResult:
        try:
            args = shlex.split(line)
        except ValueError as exc:
            raise ValueError(f"Invalid command quoting: {exc}") from exc
        return CLICommandHandlers(self).dispatch(args[0].lower(), args[1:])

    @staticmethod
    def _format_artifact(artifact) -> str:
        return (
            f"Artifact {artifact.artifact_id}@{artifact.revision} "
            f"[{artifact.artifact_type.value}] "
            f"target={artifact.chapter_id or 'project'}/{artifact.section or 'project'}\n\n"
            f"{artifact.content}"
        )

    def _artifact_list_line(self, project, index) -> str:
        artifact = self.artifacts.get_by_id(project, index.artifact_id, index.latest_revision)
        status = self.artifacts.status(project, artifact.ref)
        return (
            f"{index.artifact_id}@{index.latest_revision} "
            f"[{artifact.artifact_type.value}/{status.status.value}] "
            f"{artifact.chapter_id or 'project'}/{artifact.section or 'project'}"
        )
