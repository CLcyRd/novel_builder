"""Sole user-facing Agent: Freeform collaboration and explicit Workflow handoff."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path

from novel_agent.agent.freeform import FreeformAgent, RunResult, _requested_chapter_deletion
from novel_agent.agent.workflow_intent import (
    IntentPlanner,
    LLMIntentPlanner,
    needs_semantic_plan,
)
from novel_agent.agent.workflow_request import (
    WorkflowRequestBuilder,
    WorkflowTargetResolver,
    accepts_workflow,
)
from novel_agent.application import (
    build_backfill_chapter_workflow,
    build_continue_chapter_workflow,
    build_create_chapter_workflow,
    build_import_novel_workflow,
    build_multi_chapter_workflow,
    build_rewrite_chapter_workflow,
)
from novel_agent.domain.artifact import ArtifactRef, ArtifactType
from novel_agent.domain.errors import DecisionRequired, InvalidChange
from novel_agent.domain.models import Project, SessionMetadata
from novel_agent.runtime.events import RuntimeEvent
from novel_agent.runtime.state import SessionStateStore
from novel_agent.services.workflow_skill import (
    LLMSkillSelector,
    SkillSelector,
    WorkflowSkillResolver,
)
from novel_agent.workflow.generator import ChapterGenerator, LLMChapterGenerator
from novel_agent.workflow.models import (
    GateResponse,
    WorkflowRequest,
    WorkflowSnapshot,
)

_REVISION = re.compile(r"删掉|删除|改成|修改|调整|增加|减少|补充|重做|重新写|不要|改写|重写")


class MainAgent(FreeformAgent):
    """Routes creative requests in the Agent layer; CLI only parses control syntax."""

    def __init__(
        self,
        *args,
        generator: ChapterGenerator | None = None,
        intent_planner: IntentPlanner | None = None,
        skill_selector: SkillSelector | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.generator = generator or LLMChapterGenerator(self.models)
        self.intent_planner = intent_planner or LLMIntentPlanner(self.models)
        self.skill_resolver = WorkflowSkillResolver(
            self.skills, self.artifacts, skill_selector or LLMSkillSelector(self.models)
        )

    def _service(self, kind: str):
        factories = {
            "CREATE_CHAPTER": build_create_chapter_workflow,
            "CONTINUE_CHAPTER": build_continue_chapter_workflow,
            "BACKFILL_CHAPTER": build_backfill_chapter_workflow,
            "REWRITE_CHAPTER": build_rewrite_chapter_workflow,
            "MULTI_CHAPTER": build_multi_chapter_workflow,
            "IMPORT_NOVEL": build_import_novel_workflow,
        }
        try:
            return factories[kind](self.generator)
        except KeyError as exc:
            raise InvalidChange(f"Unknown Workflow type: {kind}") from exc

    def load_active(self, project: Project, session: SessionMetadata) -> WorkflowSnapshot | None:
        state = SessionStateStore(project.root).load(session.session_id)
        if state.active_workflow_id is None:
            return None
        # Older M3.1 Session checkpoints may not have persisted the kind.
        kind = state.active_workflow_kind or "CREATE_CHAPTER"
        snapshot = self._service(kind).load(project, state.active_workflow_id)
        if snapshot.kind != kind:
            return self._service(snapshot.kind).load(project, state.active_workflow_id)
        return snapshot

    def load_routing_decision(self, project: Project, session: SessionMetadata) -> dict | None:
        return SessionStateStore(project.root).load(session.session_id).pending_routing_decision

    def _set_routing_decision(
        self,
        project: Project,
        session: SessionMetadata,
        prompt: str,
        attachments: Sequence[str | Path],
        reason: str,
        *,
        prompt_ref=None,
    ) -> RunResult:
        if prompt_ref is None:
            prompt_ref = self.artifacts.create(
                project, session.session_id, ArtifactType.TASK_BRIEF, "PROJECT", prompt
            ).ref
        store = SessionStateStore(project.root)
        state = store.load(session.session_id)
        state.pending_routing_decision = {
            "prompt_ref": prompt_ref.model_dump(mode="json"),
            "attachments": [str(path) for path in attachments],
            "reason": reason,
        }
        store.save(session.session_id, state)
        return RunResult(
            response=f"创作范围需要您明确选择：{reason}\n可补充缺失章节要求、"
            "说明由我规划，或说明本次只写哪些章节；输入“取消”放弃本次请求。",
            model=self.models.definition(session.current_model).model_id,
        )

    @staticmethod
    def _routing_reply(prompt: str) -> bool:
        if "?" in prompt or "？" in prompt:
            return False
        return bool(
            re.search(
                r"第.{1,8}章\s*[:：]|由你规划|由系统规划|你来规划|"
                r"本次|这次|只写|先写|取消",
                prompt,
            )
        )

    @staticmethod
    def gate_response(snapshot: WorkflowSnapshot, prompt: str) -> GateResponse | None:
        gate = snapshot.gate
        if gate is None:
            return None
        value = prompt.strip()
        lower = value.casefold()
        if gate.generation_blocked:
            if lower in {"2", "e", "exit", "退出工作流"}:
                return GateResponse(gate_id=gate.gate_id, action="EXIT")
            if lower in {"1", "重新生成"}:
                return GateResponse(gate_id=gate.gate_id, action="REGENERATE")
            if (
                "?" not in value
                and "？" not in value
                and (_REVISION.search(value) or re.search(r"重新生成|补充条件|采用|确定为", value))
            ):
                return GateResponse(gate_id=gate.gate_id, action="REGENERATE", feedback=value)
            return None
        if gate.kind == "APPROVAL":
            if lower in {"y", "yes", "确认", "批准", "同意", "通过"}:
                action = "APPROVE"
            elif lower in {"n", "no", "拒绝", "不通过"}:
                action = "REJECT"
            elif lower in {"e", "exit", "退出工作流"}:
                action = "EXIT"
            elif "?" not in value and "？" not in value and _REVISION.search(value):
                return GateResponse(gate_id=gate.gate_id, action="REVISE", feedback=value)
            else:
                return None
            return GateResponse(gate_id=gate.gate_id, action=action)
        if "KEEP" in gate.options:
            choices = {
                "1": "REGENERATE",
                "重新生成": "REGENERATE",
                "2": "KEEP",
                "保留手动稿": "KEEP",
                "保留正文": "KEEP",
                "3": "EXIT",
                "e": "EXIT",
            }
            if lower in choices:
                return GateResponse(gate_id=gate.gate_id, action=choices[lower])
            return None
        if lower in {"1", "重新生成", "按新设定重新生成", "regenerate"} or (
            "?" not in value
            and "？" not in value
            and re.search(r"(?:选择|请|按新设定).{0,8}重新生成", value)
        ):
            return GateResponse(gate_id=gate.gate_id, action="REGENERATE")
        if lower in {"2", "e", "exit", "退出工作流"}:
            return GateResponse(gate_id=gate.gate_id, action="EXIT")
        return None

    def _request(
        self,
        project: Project,
        session: SessionMetadata,
        prompt: str,
        attachments: Sequence[str | Path],
    ) -> WorkflowRequest | None:
        if not accepts_workflow(prompt, session):
            return None
        current = self.projects.open_project(project.root)
        plan = (
            self.intent_planner.plan(prompt, model=session.current_model)
            if needs_semantic_plan(prompt)
            else None
        )
        target = WorkflowTargetResolver(self.projects).resolve(current, prompt, attachments, plan)
        request = WorkflowRequestBuilder(self.artifacts).build(
            current, session, target, attachments
        )
        return self.skill_resolver.resolve(current, session, request)

    def _workflow_run_result(
        self,
        project: Project,
        session: SessionMetadata,
        snapshot: WorkflowSnapshot,
        on_event: Callable[[RuntimeEvent], None] | None = None,
    ) -> RunResult:
        model_alias = self.sessions.load(project, session.session_id).current_model
        model = self.models.definition(model_alias).model_id
        result = RunResult(response="", model=model, workflow_snapshot=snapshot)
        result.artifacts.extend(snapshot.artifact_refs)
        for ref in snapshot.artifact_refs:
            for skill in self.artifacts.get(project, ref).skill_refs:
                if skill not in result.skill_refs:
                    result.skill_refs.append(skill)
        if snapshot.gate:
            result.display_refs.extend(snapshot.gate.artifact_refs)
            gate = snapshot.gate
            result.file_review = True
            for ref in gate.artifact_refs:
                record = self.drafts.find(project, session.session_id, ref)
                if record and record["active"] and record["path"] not in result.draft_paths:
                    result.draft_paths.append(record["path"])
            if gate.kind == "APPROVAL":
                next_step = {
                    "SUMMARY": "当前确认章节概述；确认后生成大纲。\n",
                    "OUTLINE": "当前确认章节大纲；确认后生成正文。\n",
                    "MANUSCRIPT": "当前确认章节正文；确认后进入 Commit。\n",
                }.get(gate.stage, "")
                result.response = (
                    f"Workflow {snapshot.kind} | {gate.stage} | WAITING_APPROVAL\n"
                    f"Gate: {gate.gate_id}\n"
                    f"{next_step}"
                    "打开工作稿文件阅读或编辑，保存后输入 y 批准、n 重做、e 退出，或给出修改意见。"
                )
            else:
                result.response = (
                    f"Workflow {snapshot.kind} | WAITING_DECISION\n"
                    f"Gate: {gate.gate_id}\n原因：{gate.reason}\n"
                    f"涉及：{', '.join(gate.paths)}\n"
                    + (
                        "输入 1 重新生成、2 保留手动稿、3 退出 Workflow。"
                        if "KEEP" in gate.options
                        else "输入 1 重试或明确补充修改条件后重新生成，或 2 退出 Workflow。"
                        if gate.generation_blocked
                        else "输入 1 基于新 Project 状态重新生成，或 2 退出 Workflow。"
                    )
                )
            if result.draft_paths:
                result.response += "\n工作稿文件：\n" + "\n".join(result.draft_paths)
            event = RuntimeEvent("Waiting", gate.stage)
        elif snapshot.result:
            result.response = (
                f"Workflow {snapshot.kind} {snapshot.result.status}; "
                f"changed_files={list(snapshot.result.project_changes)}"
            )
            if snapshot.result.commit:
                result.commits.append(snapshot.result.commit)
            if snapshot.result.warnings:
                result.response += "\n" + "\n".join(snapshot.result.warnings)
            event = RuntimeEvent(
                "Completed" if snapshot.result.status == "COMPLETED" else "Cancelled",
                snapshot.kind or "",
            )
        else:
            result.response = f"Workflow {snapshot.kind} is running"
            event = RuntimeEvent("Thinking", snapshot.kind or "")
        result.events.append(event)
        if on_event:
            on_event(event)
        return result

    def resume_workflow(
        self,
        project: Project,
        session: SessionMetadata,
        response: GateResponse,
        on_event: Callable[[RuntimeEvent], None] | None = None,
    ) -> RunResult:
        snapshot = self.load_active(project, session)
        if snapshot is None:
            raise InvalidChange("No active Workflow")
        if on_event:
            on_event(RuntimeEvent("Generating", f"Resume {snapshot.kind}"))
        service = self._service(snapshot.kind)
        if on_event:
            service.on_skill = lambda ref: on_event(RuntimeEvent("Reading", f"Skill {ref.name}"))
        updated = service.resume(project, snapshot.workflow_id, response)
        return self._workflow_run_result(project, session, updated, on_event)

    def exit_workflow(self, project: Project, session: SessionMetadata) -> RunResult:
        snapshot = self.load_active(project, session)
        if snapshot is None:
            raise InvalidChange("No active Workflow")
        updated = self._service(snapshot.kind).exit(project, snapshot.workflow_id)
        return self._workflow_run_result(project, session, updated)

    def apply_auto(self, project: Project, session: SessionMetadata) -> RunResult | None:
        snapshot = self.load_active(project, session)
        if snapshot is None or snapshot.gate is None or snapshot.gate.kind != "APPROVAL":
            return None
        updated = self._service(snapshot.kind).apply_auto(project, snapshot.workflow_id)
        return self._workflow_run_result(project, session, updated)

    async def run(
        self,
        project: Project,
        session: SessionMetadata,
        prompt: str,
        attachments: Sequence[str | Path] = (),
        on_event: Callable[[RuntimeEvent], None] | None = None,
        on_content: Callable[[str], None] | None = None,
    ) -> RunResult:
        current = self.projects.open_project(project.root)
        session = self.sessions.load(current, session.session_id)
        waiting = self.load_active(current, session)
        routing = self.load_routing_decision(current, session)
        routing_ref = None
        if routing and not waiting:
            if prompt.strip() == "取消":
                store = SessionStateStore(current.root)
                state = store.load(session.session_id)
                state.pending_routing_decision = None
                store.save(session.session_id, state)
                return RunResult(
                    response="已取消待澄清的创作请求。",
                    model=self.models.definition(session.current_model).model_id,
                )
            if self._routing_reply(prompt):
                routing_ref = ArtifactRef.model_validate(routing["prompt_ref"])
                original = self.artifacts.get(current, routing_ref).content
                prompt = original + "\n" + prompt
                attachments = tuple(routing["attachments"])
            else:
                return await super().run(
                    current,
                    session,
                    prompt,
                    attachments,
                    on_event=on_event,
                    on_content=on_content,
                    allow_workflow_chat=True,
                )
        if waiting:
            # A formal Chapter deletion is a new Project edit, not revision feedback
            # for a paused creative Workflow.
            answer = (
                None
                if _requested_chapter_deletion(prompt, current, self.projects) is not None
                else self.gate_response(waiting, prompt)
            )
            if answer:
                return self.resume_workflow(current, session, answer, on_event)
        try:
            request = self._request(current, session, prompt, attachments)
        except DecisionRequired as exc:
            if on_event:
                on_event(RuntimeEvent("Waiting", "Routing decision"))
            return self._set_routing_decision(
                current, session, prompt, attachments, exc.reason, prompt_ref=routing_ref
            )
        if request:
            if waiting:
                raise InvalidChange("An active Workflow is waiting; resume or exit it first")
            if on_event:
                on_event(RuntimeEvent("Thinking", f"Route {request.kind}"))
                on_event(RuntimeEvent("Generating", request.kind))
            service = self._service(request.kind)
            if on_event:
                service.on_skill = lambda ref: on_event(
                    RuntimeEvent("Reading", f"Skill {ref.name}")
                )
            snapshot = service.start(current, request)
            if routing_ref:
                store = SessionStateStore(current.root)
                state = store.load(session.session_id)
                state.pending_routing_decision = None
                store.save(session.session_id, state)
            if session.next_skill:
                self.sessions.update_settings(current, session.session_id, next_skill=None)
            result = self._workflow_run_result(current, session, snapshot, on_event)
            result.attachment_paths = tuple(str(path) for path in attachments)
            return result
        return await super().run(
            current,
            session,
            prompt,
            attachments,
            on_event=on_event,
            on_content=on_content,
            allow_workflow_chat=True,
        )
