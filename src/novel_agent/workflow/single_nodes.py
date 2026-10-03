"""Single Chapter business nodes, independent of graph composition."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid5

from langgraph.types import interrupt

from novel_agent.domain.artifact import ArtifactRef, ArtifactStatus, ArtifactType
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.domain.errors import DecisionRequired, GenerationBlocked, InvalidChange
from novel_agent.domain.models import Project
from novel_agent.workflow.models import (
    ApprovalRecord,
    GateResponse,
    WorkflowGate,
    WorkflowRequest,
    WorkflowResult,
)
from novel_agent.workflow.single_state import _ref, _State

if TYPE_CHECKING:
    from novel_agent.workflow.create_chapter import CreateChapterWorkflow


class SingleChapterNodes:
    """Business nodes; graph names and interrupt order belong to the facade."""

    def __init__(
        self, workflow: CreateChapterWorkflow, project: Project, *, split: bool = False
    ) -> None:
        self.workflow = workflow
        self.project = project
        self.split = split

    def prepare(self, state: _State) -> dict:
        request = WorkflowRequest.model_validate(state["request"])
        current = self.workflow.projects.open_project(self.project.root)
        if current.project_id != request.project_id:
            raise InvalidChange("Workflow Project identity changed")
        self.workflow.sessions.load(current, request.session_id)
        exists = str(request.chapter_id) in current.metadata.chapters
        count = len(current.metadata.chapter_order)
        if request.kind == "REWRITE_CHAPTER":
            if not exists:
                raise InvalidChange("Rewrite target Chapter does not exist")
        elif exists:
            raise InvalidChange("Reserved Workflow Chapter ID already exists")
        if request.kind == "CONTINUE_CHAPTER" and count == 0:
            raise InvalidChange("Continuation requires a previous Chapter")
        if request.kind == "CONTINUE_CHAPTER" and request.position is not None:
            raise InvalidChange("Continuation appends after the last Chapter")
        if request.kind == "BACKFILL_CHAPTER" and (
            request.position is None or not 0 <= request.position < count
        ):
            raise InvalidChange("Backfill requires an insertion position before a later Chapter")
        paths = [relative for relative, _ in self.workflow._formal_context(current, request)]
        for relative in request.attachments:
            path = self.workflow.projects.resolve_attachment(current, relative)
            paths.append(path.relative_to(current.root).as_posix())
        for reference in request.working_artifact_refs:
            revision = self.workflow.artifacts.get(current, reference)
            if revision.session_id != request.session_id:
                raise InvalidChange("Working Artifact belongs to another Session")
        return {"dependency_paths": list(dict.fromkeys(paths))}

    def artifact(
        self,
        state: _State,
        stage: str,
        name: str,
        artifact_type: ArtifactType,
        section: str | None,
        dependencies: tuple[ArtifactRef, ...] = (),
    ) -> dict:
        request = WorkflowRequest.model_validate(state["request"])
        try:
            content, used_skills = self.workflow._generate(self.project, state, stage)
        except GenerationBlocked as exc:
            return self.workflow._generation_failure(self.project, state, exc, stage)
        previous = state.get(name)
        if previous is not None:
            revision = self.workflow.artifacts.revise(
                self.project,
                _ref(state, name),
                content,
                skill_refs=used_skills,
                dependencies=dependencies + request.working_artifact_refs,
            )
        else:
            revision = self.workflow.artifacts.create(
                self.project,
                request.session_id,
                artifact_type,
                "SECTION" if section else "PROJECT",
                content,
                chapter_id=request.chapter_id if section else None,
                section=section,
                dependency_paths=tuple(state["dependency_paths"]),
                dependencies=dependencies
                + WorkflowRequest.model_validate(state["request"]).working_artifact_refs,
                skill_refs=used_skills,
            )
        return {
            name: revision.ref.model_dump(mode="json"),
            "feedback": "",
            "generation_problem": None,
            "pending_gate": None,
        }

    def summary(self, state: _State) -> dict:
        return self.artifact(state, "summary", "summary_ref", ArtifactType.SUMMARY, "summary")

    def outline(self, state: _State) -> dict:
        if self.split and self.workflow.artifacts.status(
            self.project, _ref(state, "summary_ref")
        ).status != (ArtifactStatus.APPROVED):
            raise InvalidChange("Outline generation requires the approved Summary revision")
        return self.artifact(
            state,
            "outline",
            "outline_ref",
            ArtifactType.OUTLINE,
            "outline",
            (_ref(state, "summary_ref"),),
        )

    def plan_gate(self, state: _State) -> dict:
        request = WorkflowRequest.model_validate(state["request"])
        gate = self.workflow._export_at_gate(
            self.project, state, self.workflow._gate(request, state, "PLAN", "APPROVAL")
        )
        return {"pending_gate": gate.model_dump(mode="json")}

    def section_gate(self, state: _State, stage: str) -> dict:
        request = WorkflowRequest.model_validate(state["request"])
        gate = self.workflow._export_at_gate(
            self.project, state, self.workflow._gate(request, state, stage, "APPROVAL")
        )
        return {"pending_gate": gate.model_dump(mode="json")}

    def summary_gate(self, state: _State) -> dict:
        return self.section_gate(state, "SUMMARY")

    def outline_gate(self, state: _State) -> dict:
        return self.section_gate(state, "OUTLINE")

    def manuscript(self, state: _State) -> dict:
        if self.split and any(
            self.workflow.artifacts.status(self.project, _ref(state, name)).status
            != ArtifactStatus.APPROVED
            for name in ("summary_ref", "outline_ref")
        ):
            raise InvalidChange("Manuscript generation requires approved Summary and Outline")
        return self.artifact(
            state,
            "manuscript",
            "manuscript_ref",
            ArtifactType.MANUSCRIPT,
            "manuscript",
            (_ref(state, "summary_ref"), _ref(state, "outline_ref")),
        )

    def review(self, state: _State) -> dict:
        # Review is read-only; its report is a noncommittable auxiliary Artifact.
        updated = self.artifact(
            state,
            "review",
            "review_ref",
            ArtifactType.REVIEW_REPORT,
            None,
            (_ref(state, "manuscript_ref"),),
        )
        if updated.get("generation_problem"):
            return updated
        request = WorkflowRequest.model_validate(state["request"])
        if request.target_characters:
            manuscript = self.workflow.artifacts.get(
                self.project, _ref(state, "manuscript_ref")
            ).content
            count = sum(char not in "\r\n" for char in manuscript)
            lower = int(request.target_characters * 0.9)
            upper = int(request.target_characters * 1.1)
            verdict = "符合" if lower <= count <= upper else "不符合"
            report = self.workflow.artifacts.get(
                self.project, ArtifactRef.model_validate(updated["review_ref"])
            )
            revised = self.workflow.artifacts.revise(
                self.project,
                report.ref,
                f"字数检查：{count} 字，目标 {request.target_characters} 字，"
                f"允许 {lower}–{upper} 字；{verdict}。\n\n{report.content}",
            )
            updated["review_ref"] = revised.ref.model_dump(mode="json")
        return updated

    def manuscript_gate(self, state: _State) -> dict:
        request = WorkflowRequest.model_validate(state["request"])
        gate = self.workflow._export_at_gate(
            self.project, state, self.workflow._gate(request, state, "MANUSCRIPT", "APPROVAL")
        )
        return {"pending_gate": gate.model_dump(mode="json")}

    def approval(self, state: _State) -> dict:
        gate = WorkflowGate.model_validate(state["pending_gate"])
        request = WorkflowRequest.model_validate(state["request"])
        auto = request.auto_approval
        if auto is None:
            auto = self.workflow.sessions.load(self.project, request.session_id).auto_approval
        if auto and gate.kind == "APPROVAL":
            answer = GateResponse(gate_id=gate.gate_id, action="APPROVE")
        else:
            answer = GateResponse.model_validate(interrupt(gate.model_dump(mode="json")))
        if answer.gate_id != gate.gate_id:
            raise InvalidChange("Approval response targets a different Gate")
        return {
            "answer": answer.model_dump(mode="json"),
            "approval_source": "AUTO" if auto and gate.kind == "APPROVAL" else "HUMAN",
            "pending_gate": None,
        }

    def approval_route(self, state: _State) -> str:
        action = GateResponse.model_validate(state["answer"]).action
        if self.split and state.get("upstream_changed") and action != "EXIT":
            return "upstream"
        if action in {"APPROVE", "KEEP"}:
            return "approve"
        if action in {"REVISE", "REJECT", "REGENERATE"}:
            return "revise"
        if action == "EXIT":
            return "exit"
        raise InvalidChange("Invalid Approval Gate action")

    def approve_plan(self, state: _State) -> dict:
        self.workflow.artifacts.approve(self.project, _ref(state, "summary_ref"))
        self.workflow.artifacts.approve(self.project, _ref(state, "outline_ref"))
        answer = GateResponse.model_validate(state["answer"])
        record = ApprovalRecord(
            gate_id=answer.gate_id,
            stage="PLAN",
            source=state["approval_source"],
            artifact_refs=(_ref(state, "summary_ref"), _ref(state, "outline_ref")),
        )
        return {
            "answer": None,
            "approvals": [
                *state.get("approvals", []),
                record.model_dump(mode="json"),
            ],
        }

    def revise_plan(self, state: _State) -> dict:
        answer = GateResponse.model_validate(state["answer"])
        if answer.action == "REVISE" and not (answer.feedback or "").strip():
            raise InvalidChange("Revision feedback is required")
        return {
            "feedback": answer.feedback or "请重新生成当前候选稿，保留已确认约束。",
            "plan_round": state.get("plan_round", 0) + 1,
            "outline_ref": None,
            "answer": None,
        }

    def approve_section(self, state: _State, name: str, stage: str) -> dict:
        ref = _ref(state, name + "_ref")
        self.workflow.artifacts.approve(self.project, ref)
        answer = GateResponse.model_validate(state["answer"])
        record = ApprovalRecord(
            gate_id=answer.gate_id,
            stage=stage,
            source=state["approval_source"],
            artifact_refs=(ref,),
        )
        return {
            "answer": None,
            "approvals": [*state.get("approvals", []), record.model_dump(mode="json")],
        }

    def rebind_kept(self, state: _State, name: str, prior: tuple[str, ...]) -> dict:
        if not state.get("reuse_downstream") or not state.get(name + "_ref"):
            return {}
        old = self.workflow.artifacts.get(self.project, _ref(state, name + "_ref"))
        request = WorkflowRequest.model_validate(state["request"])
        dependencies = tuple(
            ref
            for ref in old.dependencies
            if self.workflow.artifacts.get(self.project, ref).chapter_id != request.chapter_id
            or self.workflow.artifacts.get(self.project, ref).section not in prior
        ) + tuple(_ref(state, key + "_ref") for key in prior)
        if set(dependencies) == set(old.dependencies):
            return {}
        revision = self.workflow.artifacts.revise(
            self.project,
            old.ref,
            old.content,
            dependencies=dependencies,
            skill_refs=old.skill_refs,
        )
        return {name + "_ref": revision.ref.model_dump(mode="json"), "review_ref": None}

    def approve_summary(self, state: _State) -> dict:
        return {
            **self.approve_section(state, "summary", "SUMMARY"),
            **self.rebind_kept(state, "outline", ("summary",)),
        }

    def approve_outline(self, state: _State) -> dict:
        if self.workflow.artifacts.status(self.project, _ref(state, "summary_ref")).status != (
            ArtifactStatus.APPROVED
        ):
            raise InvalidChange("Outline approval requires the approved Summary revision")
        return {
            **self.approve_section(state, "outline", "OUTLINE"),
            **self.rebind_kept(state, "manuscript", ("summary", "outline")),
        }

    def revise_section(self, state: _State, name: str, stage: str) -> dict:
        answer = GateResponse.model_validate(state["answer"])
        update = {
            "feedback": answer.feedback or "请重新生成当前候选稿，保留已确认约束。",
            name + "_round": state.get(name + "_round", 0) + 1,
            "answer": None,
            "reuse_downstream": False,
            "upstream_changed": [],
        }
        downstream = (
            ("outline", "manuscript", "review") if name == "summary" else ("manuscript", "review")
        )
        proposed = {**state, **update, **{key + "_ref": None for key in downstream}}
        # Rebind before generating, so a failed generation/restart cannot revive
        # old downstream sections from DraftService's merged record.
        gate_update = self.section_gate(proposed, stage)
        if WorkflowGate.model_validate(gate_update["pending_gate"]).kind == "DECISION":
            return {**update, **gate_update}
        return {**update, **{key + "_ref": None for key in downstream}, "pending_gate": None}

    def revise_summary(self, state: _State) -> dict:
        return self.revise_section(state, "summary", "SUMMARY")

    def revise_outline(self, state: _State) -> dict:
        return self.revise_section(state, "outline", "OUTLINE")

    def reconfirm_upstream(self, state: _State) -> dict:
        answer = GateResponse.model_validate(state["answer"])
        name = "summary" if "summary" in state["upstream_changed"] else "outline"
        keep = answer.action == "KEEP"
        update = {
            "answer": None,
            "upstream_changed": [],
            "reuse_downstream": keep,
            "feedback": "",
            name + "_round": state.get(name + "_round", 0) + 1,
        }
        if (answer.feedback or "").strip():
            request = WorkflowRequest.model_validate(state["request"])
            update["request"] = request.model_copy(
                update={
                    "instruction": request.instruction
                    + "\n\n最新用户修订要求：\n"
                    + answer.feedback.strip(),
                }
            ).model_dump(mode="json")
        if not keep:
            downstream = (
                ("outline", "manuscript", "review")
                if name == "summary"
                else ("manuscript", "review")
            )
            update.update({key + "_ref": None for key in downstream})
        update["return_stage"] = name
        return update

    def after_summary_approval(self, state: _State) -> str:
        return (
            "outline_gate"
            if state.get("reuse_downstream") and state.get("outline_ref")
            else ("outline")
        )

    def after_outline_approval(self, state: _State) -> str:
        return (
            "review"
            if state.get("reuse_downstream") and state.get("manuscript_ref")
            else ("manuscript")
        )

    def approve_manuscript(self, state: _State) -> dict:
        refreshed = self.review(state) if state.get("review_ref") is None else {}
        if refreshed.get("generation_problem"):
            return {**refreshed, "generation_retry_node": "approve_manuscript"}
        state = {**state, **refreshed}
        self.workflow.artifacts.approve(self.project, _ref(state, "manuscript_ref"))
        request = WorkflowRequest.model_validate(state["request"])
        self.workflow.drafts.clean(
            self.project, request.session_id, request.task_id, request.chapter_id
        )
        answer = GateResponse.model_validate(state["answer"])
        record = ApprovalRecord(
            gate_id=answer.gate_id,
            stage="MANUSCRIPT",
            source=state["approval_source"],
            artifact_refs=(_ref(state, "manuscript_ref"),),
        )
        return {
            **refreshed,
            "answer": None,
            "approvals": [
                *state.get("approvals", []),
                record.model_dump(mode="json"),
            ],
        }

    def revise_manuscript(self, state: _State) -> dict:
        answer = GateResponse.model_validate(state["answer"])
        if answer.action == "REVISE" and not (answer.feedback or "").strip():
            raise InvalidChange("Revision feedback is required")
        return {
            "feedback": answer.feedback or "请重新生成当前候选稿，保留已确认约束。",
            "manuscript_round": state.get("manuscript_round", 0) + 1,
            "review_ref": None,
            "answer": None,
        }

    def commit(self, state: _State) -> dict:
        request = WorkflowRequest.model_validate(state["request"])
        changes = (
            *(
                (
                    CreateChapter(
                        title=request.title,
                        position=request.position,
                        chapter_id=request.chapter_id,
                    ),
                )
                if request.kind != "REWRITE_CHAPTER"
                else ()
            ),
            SetSection(request.chapter_id, "summary", artifact=_ref(state, "summary_ref")),
            SetSection(request.chapter_id, "outline", artifact=_ref(state, "outline_ref")),
            SetSection(request.chapter_id, "manuscript", artifact=_ref(state, "manuscript_ref")),
        )
        try:
            result = self.workflow.commits.commit(
                self.project, changes, commit_id=uuid5(request.workflow_id, "commit")
            )
        except DecisionRequired as exc:
            gate = WorkflowGate(
                gate_id=uuid5(request.workflow_id, f"DECISION:COMMIT:{state.get('plan_round', 0)}"),
                kind="DECISION",
                stage="COMMIT",
                reason=exc.reason,
                paths=exc.paths,
                options=("REGENERATE", "EXIT"),
            )
            return {"pending_gate": gate.model_dump(mode="json"), "commit": None}
        self.workflow.drafts.clean(
            self.project, request.session_id, request.task_id, request.chapter_id
        )
        return {"commit": result.model_dump(mode="json"), "pending_gate": None}

    def commit_route(self, state: _State) -> str:
        return "decision" if state.get("pending_gate") else "finish"

    def decision(self, state: _State) -> dict:
        gate = WorkflowGate.model_validate(state["pending_gate"])
        answer = GateResponse.model_validate(interrupt(gate.model_dump(mode="json")))
        if answer.gate_id != gate.gate_id or answer.action not in {"REGENERATE", "EXIT"}:
            raise InvalidChange("Decision requires an explicit choice for this Gate")
        return {"answer": answer.model_dump(mode="json"), "pending_gate": None}

    def decision_route(self, state: _State) -> str:
        return (
            "regenerate"
            if GateResponse.model_validate(state["answer"]).action == "REGENERATE"
            else "exit"
        )

    def regenerate(self, state: _State) -> dict:
        # New Artifact IDs capture new baselines; old Candidates remain inspectable.
        return {
            "summary_ref": None,
            "outline_ref": None,
            "manuscript_ref": None,
            "review_ref": None,
            "plan_round": state.get("plan_round", 0) + 1,
            "manuscript_round": 0,
            "summary_round": state.get("summary_round", 0) + 1,
            "outline_round": state.get("outline_round", 0) + 1,
            "reuse_downstream": False,
            "upstream_changed": [],
            "answer": None,
            "feedback": "",
        }

    def finish(self, state: _State) -> dict:
        request = WorkflowRequest.model_validate(state["request"])
        record = self.workflow.drafts.record(
            self.project, request.session_id, request.task_id, request.chapter_id
        )
        result = WorkflowResult(
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            status="COMPLETED",
            chapter_id=request.chapter_id,
            artifact_refs=tuple(
                _ref(state, name)
                for name in ("summary_ref", "outline_ref", "manuscript_ref", "review_ref")
            ),
            commit=state["commit"],
            completed_tasks=(request.kind,),
            project_changes=tuple(state["commit"]["changed_files"]),
            approvals=tuple(
                ApprovalRecord.model_validate(item) for item in state.get("approvals", [])
            ),
            warnings=("确认后文件又被修改，draft 已保留；正式内容使用已确认的 revision。",)
            if record and record["active"]
            else (),
        )
        return {"result": result.model_dump(mode="json")}

    def exit_workflow(self, state: _State) -> dict:
        request = WorkflowRequest.model_validate(state["request"])
        refs = tuple(
            _ref(state, name)
            for name in ("summary_ref", "outline_ref", "manuscript_ref", "review_ref")
            if state.get(name)
        )
        result = WorkflowResult(
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            status="CANCELLED",
            chapter_id=request.chapter_id,
            artifact_refs=refs,
            remaining_tasks=(request.kind,),
            approvals=tuple(
                ApprovalRecord.model_validate(item) for item in state.get("approvals", [])
            ),
        )
        return {"result": result.model_dump(mode="json"), "pending_gate": None}
