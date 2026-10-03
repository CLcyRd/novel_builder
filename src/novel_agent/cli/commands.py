"""Dispatch control commands to handlers grouped by command family."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from novel_agent.cli.types import CLIResult
from novel_agent.runtime.history import SessionHistoryStore

if TYPE_CHECKING:
    from novel_agent.cli.shell import NovelCLI


class CLICommandHandlers:
    """Command handlers own shell selection and presentation, never business policy."""

    def __init__(self, cli: NovelCLI) -> None:
        self.cli = cli

    def dispatch(self, command: str, arguments: list[str]) -> CLIResult:
        handlers = {
            "/help": self.help,
            "/exit": self.exit,
            "/clear": self.clear,
            "/history": self.history,
            "/project": self.project,
            "/session": self.session,
            "/status": self.status,
            "/mode": self.mode,
            "/auto": self.auto,
            "/model": self.model,
            "/skill": self.skill,
            "/artifact": self.artifact,
            "/commit": self.commit,
            "/workflow": self.workflow,
        }
        handler = handlers.get(command)
        if handler is None:
            raise ValueError("Unknown or incomplete command; use /help")
        return handler(arguments)

    def help(self, rest: list[str]) -> CLIResult:
        return CLIResult(
            "/project create|list|open|maintenance · /session create|list|open · /status · "
            "/mode freeform|workflow · /auto on|off · /model list|<alias> · "
            "/artifact list|show|current|discard · /commit [current|id@revision...] · "
            "/skill list|show|enable|disable|use|clear · "
            "/workflow status|exit · /history · /clear · /exit\n"
            "章节 draft 可直接编辑，保存后 y 确认、n 重做、e 退出。\n"
            "Decision: freshness 1/2；工作稿修改冲突 1/2/3。\n"
            'Attach Project .txt/.md files with @path or @"path with spaces".'
        )

    def exit(self, rest: list[str]) -> CLIResult:
        return CLIResult("再见。", exit_requested=True)

    def clear(self, rest: list[str]) -> CLIResult:
        if not rest:
            return CLIResult("", clear_screen=True)

        raise ValueError("Unknown or incomplete command; use /help")

    def history(self, rest: list[str]) -> CLIResult:
        if not rest:
            project, session = self.cli._selected()
            messages = SessionHistoryStore(project.root, self.cli.settings).recent(
                session.session_id
            )
            if not messages:
                return CLIResult("当前 Session 暂无对话历史。")
            lines = []
            for message in messages:
                label = "用户" if message.role == "user" else "助手"
                content = message.content + ("…（已截断）" if message.truncated else "")
                lines.append(f"{label}：{content}")
                if message.artifact_refs:
                    refs = ", ".join(
                        f"{ref.artifact_id}@{ref.revision}" for ref in message.artifact_refs
                    )
                    lines.append(f"工作稿：{refs}")
            return CLIResult("\n\n".join(lines))

        raise ValueError("Unknown or incomplete command; use /help")

    def project(self, rest: list[str]) -> CLIResult:
        if rest == ["maintenance"]:
            if self.cli.project is None:
                raise ValueError("Open a Project first")
            report = self.cli.projects.maintain_transactions(self.cli.project)
            lines = [
                "事务维护完成",
                f"待恢复或收尾事务：{report.pending_transactions}",
                f"正式 Commit 凭据：{report.receipt_count}",
                f"待清理事务：{report.garbage_transactions}",
                f"遗留暂存项：{report.staging_entries}",
                f"事务存储占用：{report.disk_bytes} bytes",
                f"本次回收：{report.reclaimed_bytes} bytes",
            ]
            lines.extend(f"维护警告：{warning}" for warning in report.warnings)
            return CLIResult("\n".join(lines))
        if not rest or rest[0] == "list":
            projects = self.cli.projects.list_projects(self.cli.workspace)
            return CLIResult(
                "\n".join(f"{p.metadata.name} | {p.root}" for p in projects) or "No Projects"
            )
        if rest[0] == "create" and len(rest) >= 2:
            self.cli.project = self.cli.projects.create_project(
                self.cli.workspace, " ".join(rest[1:]), "Main"
            )
            self.cli.session = next(
                item
                for item in self.cli.project.sessions
                if item.session_id == self.cli.project.metadata.initial_session_id
            )
            return CLIResult(
                f"Project created: {self.cli.project.root}\nSession: {self.cli.session.name}"
            )
        if rest[0] == "open" and len(rest) >= 2:
            candidate = Path(" ".join(rest[1:]))
            root = candidate if candidate.is_absolute() else self.cli.workspace / candidate
            self.cli.project = self.cli.projects.open_project(root)
            self.cli.session = next(
                item
                for item in self.cli.project.sessions
                if item.session_id == self.cli.project.metadata.initial_session_id
            )
            return CLIResult(
                f"Project opened: {self.cli.project.metadata.name}\n"
                f"Session: {self.cli.session.name}"
            )

        raise ValueError("Unknown or incomplete command; use /help")

    def session(self, rest: list[str]) -> CLIResult:
        if self.cli.project is None:
            raise ValueError("Open a Project first")
        if not rest or rest[0] == "list":
            return CLIResult(
                "\n".join(
                    f"{s.name} | {s.session_id}" for s in self.cli.sessions.list(self.cli.project)
                )
            )
        if rest[0] == "create" and len(rest) >= 2:
            self.cli.session = self.cli.sessions.create(self.cli.project, " ".join(rest[1:]))
            return CLIResult(
                f"Session created: {self.cli.session.name} | {self.cli.session.session_id}"
            )
        if rest[0] == "open" and len(rest) >= 2:
            matches = [
                s
                for s in self.cli.sessions.list(self.cli.project)
                if s.name == " ".join(rest[1:]) or str(s.session_id).startswith(rest[1].lower())
            ]
            if len(matches) != 1:
                raise ValueError("Session is missing or ambiguous")
            self.cli.session = matches[0]
            return CLIResult(f"Session opened: {self.cli.session.name}")

        raise ValueError("Unknown or incomplete command; use /help")

    def status(self, rest: list[str]) -> CLIResult:
        project, session = self.cli._selected()
        state = self.cli.agent._state(project, session)[1]
        ref = state.current_ref
        current_artifact = f"{ref.artifact_id}@{ref.revision}" if ref else "none"
        enabled_skills = (
            ", ".join(skill.ref.name for skill in self.cli.agent.skills.enabled(project, session))
            or "none"
        )
        return CLIResult(
            f"Project: {project.metadata.name}\nSession: {session.name}\n"
            f"Mode: {session.execution_mode}\nAuto Approval: {session.auto_approval}\n"
            f"Model: {session.current_model or self.cli.models.default_model}\n"
            f"Skills: {enabled_skills}\nNext Skill: {session.next_skill or 'none'}\n"
            f"Current Artifact: {current_artifact}\n"
            f"{self.cli._workflow_status(project, session)}"
        )

    def mode(self, rest: list[str]) -> CLIResult:
        if not rest:
            return CLIResult(f"Mode: {self.cli._selected()[1].execution_mode}")

        if len(rest) == 1:
            project, session = self.cli._selected()
            mode = rest[0].upper()
            if mode not in {"FREEFORM", "WORKFLOW"}:
                raise ValueError("Mode must be freeform or workflow")
            self.cli.session = self.cli.sessions.update_settings(
                project, session.session_id, execution_mode=mode
            )
            return CLIResult(f"Mode: {mode}")

        raise ValueError("Unknown or incomplete command; use /help")

    def auto(self, rest: list[str]) -> CLIResult:
        if len(rest) == 1:
            project, session = self.cli._selected()
            if rest[0].lower() not in {"on", "off"}:
                raise ValueError("Use /auto on or /auto off")
            self.cli.session = self.cli.sessions.update_settings(
                project, session.session_id, auto_approval=rest[0].lower() == "on"
            )
            if self.cli.session.auto_approval:
                advanced = self.cli.agent.apply_auto(project, self.cli.session)
                if advanced:
                    presented = self.cli._present_run(project, advanced)
                    presented.text = "Auto Approval: True\n" + presented.text
                    return presented
            return CLIResult(f"Auto Approval: {self.cli.session.auto_approval}")

        if not rest:
            return CLIResult(f"Auto Approval: {self.cli._selected()[1].auto_approval}")

        raise ValueError("Unknown or incomplete command; use /help")

    def model(self, rest: list[str]) -> CLIResult:
        if not rest or rest[0] == "list":
            selected = (
                self.cli.session.current_model
                if self.cli.session
                else self.cli.models.default_model
            )
            return CLIResult(
                "\n".join(
                    f"{'*' if alias == (selected or self.cli.models.default_model) else ' '} "
                    f"{alias}: {self.cli.models.definition(alias).model_id}"
                    for alias in self.cli.models.available_models()
                )
            )
        if len(rest) == 1:
            project, session = self.cli._selected()
            self.cli.models.definition(rest[0])
            self.cli.session = self.cli.sessions.update_settings(
                project, session.session_id, current_model=rest[0]
            )
            return CLIResult(f"Model: {rest[0]}")

        raise ValueError("Unknown or incomplete command; use /help")

    def skill(self, rest: list[str]) -> CLIResult:
        project, session = self.cli._selected()
        catalog = self.cli.agent.skills
        if not rest or rest[0] == "list":
            enabled = {skill.ref.name for skill in catalog.enabled(project, session)}
            return CLIResult(
                "\n".join(
                    f"{skill.ref.name} [{skill.ref.source}; "
                    f"{'enabled' if skill.ref.name in enabled else 'disabled'}; "
                    f"{skill.activation}] "
                    f"{skill.description}"
                    for skill in catalog.list(project)
                )
                or "No Skills"
            )
        if rest[0] == "clear" and len(rest) == 1:
            self.cli.session = self.cli.sessions.update_settings(
                project, session.session_id, next_skill=None
            )
            return CLIResult("Next Skill cleared")
        if len(rest) == 2 and rest[0] in {"show", "enable", "disable", "use"}:
            skill = catalog.get(project, rest[1])
            if rest[0] == "show":
                return CLIResult(skill.files["SKILL.md"].decode("utf-8"))
            enabled = set(session.enabled_skills)
            disabled = set(session.disabled_skills)
            if rest[0] == "enable":
                enabled.add(skill.ref.name)
                disabled.discard(skill.ref.name)
            elif rest[0] == "disable":
                enabled.discard(skill.ref.name)
                disabled.add(skill.ref.name)
            elif skill not in catalog.enabled(project, session):
                raise ValueError("Enable the Skill before selecting it")
            next_skill = session.next_skill
            if rest[0] == "use":
                next_skill = skill.ref.name
            elif rest[0] == "disable" and next_skill == skill.ref.name:
                next_skill = None
            self.cli.session = self.cli.sessions.update_settings(
                project,
                session.session_id,
                enabled_skills=tuple(sorted(enabled)),
                disabled_skills=tuple(sorted(disabled)),
                next_skill=next_skill,
            )
            return CLIResult(f"Skill {rest[0]}: {skill.ref.name}")

        raise ValueError("Unknown or incomplete command; use /help")

    def artifact(self, rest: list[str]) -> CLIResult:
        project, session = self.cli._selected()
        if not rest or rest[0] == "list":
            items = self.cli.artifacts.list(project, session.session_id)
            return CLIResult(
                "\n".join(self.cli._artifact_list_line(project, item) for item in items)
                or "No Artifacts"
            )
        if rest[0] == "current":
            state = self.cli.agent._state(project, session)[1]
            if state.current_ref is None:
                return CLIResult("No current Artifact")
            artifact = self.cli.agent.show_artifact(
                project,
                session,
                f"{state.current_ref.artifact_id}@{state.current_ref.revision}",
            )
            return CLIResult(self.cli._format_artifact(artifact))
        if rest[0] == "show" and len(rest) == 2:
            return CLIResult(
                self.cli._format_artifact(self.cli.agent.show_artifact(project, session, rest[1]))
            )
        if rest[0] == "discard" and len(rest) == 2:
            ref = self.cli.agent.resolve_ref(project, session, rest[1])
            self.cli.artifacts.discard(project, ref)
            return CLIResult(f"Discarded {ref.artifact_id}@{ref.revision}")

        raise ValueError("Unknown or incomplete command; use /help")

    def commit(self, rest: list[str]) -> CLIResult:
        project, session = self.cli._selected()
        if "current" in rest and rest != ["current"]:
            raise ValueError("Use /commit current alone or specify Artifact revisions")
        selectors = () if rest == ["current"] else rest
        result = self.cli.agent.commit_visible(project, session, selectors)
        return CLIResult(f"Commit {result.commit_id} completed\n" + "\n".join(result.changed_files))

    def workflow(self, rest: list[str]) -> CLIResult:
        if rest and rest[0] == "status":
            return CLIResult(self.cli._workflow_status(*self.cli._selected()))

        if rest and rest[0] == "exit":
            project, session = self.cli._selected()
            return self.cli._present_run(project, self.cli.agent.exit_workflow(project, session))

        raise ValueError("Unknown or incomplete command; use /help")
