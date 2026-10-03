"""M3.1: persisted single-chapter LangGraph subgraph and service facade."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid5

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from novel_agent.domain.artifact import ArtifactRef, ArtifactType
from novel_agent.domain.errors import DecisionRequired, GenerationBlocked, InvalidChange
from novel_agent.domain.models import Project, ProjectReadSnapshot
from novel_agent.prompts.prompt import ContextBlock, GenerationContext
from novel_agent.runtime.state import SessionStateStore
from novel_agent.services.artifact import ArtifactService
from novel_agent.services.commit import CommitService
from novel_agent.services.draft import SECTIONS, DraftService
from novel_agent.services.project import ProjectService
from novel_agent.services.session import SessionService
from novel_agent.services.skill import SkillService
from novel_agent.workflow.context_builder import WorkflowContextBuilder
from novel_agent.workflow.generator import ChapterGenerator
from novel_agent.workflow.models import (
    ApprovalRecord,
    GateResponse,
    WorkflowGate,
    WorkflowRequest,
    WorkflowResult,
    WorkflowSnapshot,
)
from novel_agent.workflow.single_nodes import SingleChapterNodes
from novel_agent.workflow.single_state import _ref as _ref
from novel_agent.workflow.single_state import _State as _State
from novel_agent.workflow.skill_context import skill_context


class CreateChapterWorkflow:
    """One active Workflow per Session; gates can only resume via exact gate IDs."""

    workflow_kind = "CREATE_CHAPTER"

    def _generation_failure(
        self, project: Project, state: dict, error: GenerationBlocked, stage: str
    ) -> dict:
        request = WorkflowRequest.model_validate(state["request"])
        batch = "rows" in state
        generation_round = state.get("generation_round", 0) + 1
        gate_stage = (
            (
                "SUMMARIES"
                if batch
                else "SUMMARY"
                if state.get("workflow_version", 1) >= 2
                else "PLAN"
            )
            if stage == "summary"
            else (
                "OUTLINES"
                if batch
                else "OUTLINE"
                if state.get("workflow_version", 1) >= 2
                else "PLAN"
            )
            if stage == "outline"
            else "MANUSCRIPT"
        )
        refs = tuple(
            ArtifactRef.model_validate(row[key])
            for row in self._draft_rows(state)
            for key in (*SECTIONS, "review")
            if row.get(key)
        )
        gate = WorkflowGate(
            gate_id=uuid5(
                request.workflow_id,
                f"generation:{stage}:{state.get('index', 0)}:{generation_round}",
            ),
            kind="DECISION",
            stage=gate_stage,
            artifact_refs=refs,
            reason=error.reason,
            paths=error.paths,
            options=("REGENERATE", "EXIT"),
            generation_blocked=True,
        )
        exported = self._export_at_gate(project, state, gate)
        # A concurrent manual edit stays on disk and is synchronized on explicit retry.
        # Generation cannot offer KEEP as a shortcut around its missing-condition Decision.
        if exported.paths:
            gate = gate.model_copy(
                update={
                    "paths": exported.paths,
                    "reason": error.reason + "；工作稿手动修改已保留，重试时将读取。",
                }
            )
        retry = (
            {"summary": "summaries", "outline": "outlines"}.get(stage, stage) if batch else stage
        )
        return {
            "pending_gate": gate.model_dump(mode="json"),
            "generation_problem": error.reason,
            "generation_retry_node": retry,
            "generation_answer": state.get("answer"),
            "generation_round": generation_round,
        }

    @staticmethod
    def _generation_decision(state: dict) -> dict:
        gate = WorkflowGate.model_validate(state["pending_gate"])
        answer = GateResponse.model_validate(interrupt(gate.model_dump(mode="json")))
        if answer.gate_id != gate.gate_id or answer.action not in {"REGENERATE", "EXIT"}:
            raise InvalidChange("Generation Decision requires an explicit current-Gate choice")
        request = WorkflowRequest.model_validate(state["request"])
        if answer.action == "REGENERATE" and (answer.feedback or "").strip():
            # A Decision resolves a task prerequisite for every subsequent stage,
            # rather than acting as one-shot prose revision feedback.
            request = request.model_copy(
                update={
                    "instruction": request.instruction
                    + "\n\n最新用户明确决策（覆盖以上旧约束）：\n"
                    + answer.feedback.strip(),
                }
            )
        return {
            "request": request.model_dump(mode="json"),
            "generation_action": answer.action,
            "feedback": "\n".join(filter(None, (state.get("feedback"), answer.feedback))),
            "answer": answer.model_dump(mode="json")
            if state.get("upstream_changed")
            else state.get("generation_answer"),
            "pending_gate": None,
            "generation_problem": None,
        }

    @staticmethod
    def _generation_route(state: dict) -> str:
        if state["generation_action"] == "EXIT":
            return "exit"
        return (
            "reconfirm_upstream"
            if state.get("upstream_changed")
            else state["generation_retry_node"]
        )

    def __init__(
        self,
        projects: ProjectService,
        sessions: SessionService,
        artifacts: ArtifactService,
        commits: CommitService,
        generator: ChapterGenerator,
    ) -> None:
        self.projects = projects
        self.sessions = sessions
        self.artifacts = artifacts
        self.commits = commits
        self.generator = generator
        self.context_builder = WorkflowContextBuilder(projects, artifacts)
        self.drafts = DraftService(artifacts)
        self.skills = SkillService()
        self.on_skill = None

    def _draft_rows(self, values: dict) -> list[dict]:
        if "rows" in values:
            return [dict(row) for row in values["rows"]]
        request = WorkflowRequest.model_validate(values["request"])
        return [
            {
                "chapter_id": str(request.chapter_id),
                "title": request.title,
                **{
                    key: values[key + "_ref"]
                    for key in (*SECTIONS, "review")
                    if values.get(key + "_ref")
                },
            }
        ]

    def _export_at_gate(self, project: Project, values: dict, gate: WorkflowGate) -> WorkflowGate:
        request = WorkflowRequest.model_validate(values["request"])
        ids = {self.artifacts.get(project, ref).chapter_id for ref in gate.artifact_refs}
        for row in self._draft_rows(values):
            chapter_id = UUID(row["chapter_id"])
            if chapter_id not in ids:
                continue
            try:
                self.drafts.export(
                    project,
                    request.session_id,
                    request.task_id,
                    chapter_id,
                    row["title"],
                    {key: ArtifactRef.model_validate(row[key]) for key in SECTIONS if row.get(key)},
                    replace_refs="rows" not in values and values.get("workflow_version", 1) >= 2,
                )
            except DecisionRequired as exc:
                return gate.model_copy(
                    update={
                        "kind": "DECISION",
                        "reason": exc.reason,
                        "paths": exc.paths,
                        "options": ("REGENERATE", "KEEP", "EXIT"),
                    }
                )
        return gate

    def _sync_drafts(self, project: Project, values: dict, gate: WorkflowGate, *, auto=False):
        request = WorkflowRequest.model_validate(values["request"])
        ids = {self.artifacts.get(project, ref).chapter_id for ref in gate.artifact_refs}
        rows = self._draft_rows(values)
        conflict = False
        upstream_changed = []
        paths = []
        for row in rows:
            chapter_id = UUID(row["chapter_id"])
            if chapter_id not in ids:
                continue
            sync = self.drafts.sync(project, request.session_id, request.task_id, chapter_id)
            if not sync:
                continue
            paths.append(sync.path)
            row.update({key: ref.model_dump(mode="json") for key, ref in sync.refs.items()})
            if "manuscript" in sync.changed:
                row["review"] = None
            if sync.changed and auto:
                conflict = True
            if gate.stage == "MANUSCRIPT" and any(
                key in sync.changed for key in ("summary", "outline")
            ):
                conflict = True
            if gate.stage == "OUTLINES" and "summary" in sync.changed:
                conflict = True
            if "rows" not in values and values.get("workflow_version", 1) >= 2:
                prior = {"OUTLINE": ("summary",), "MANUSCRIPT": ("summary", "outline")}
                upstream_changed.extend(
                    key for key in prior.get(gate.stage, ()) if key in sync.changed
                )
                if upstream_changed:
                    conflict = True
        update = (
            {"rows": rows}
            if "rows" in values
            else {key + "_ref": rows[0].get(key) for key in (*SECTIONS, "review")}
        )
        # Preserve gate scope while rebinding each displayed candidate to its saved revision.
        gate_refs = []
        for ref in gate.artifact_refs:
            old = self.artifacts.get(project, ref)
            row = next((row for row in rows if str(old.chapter_id) == row["chapter_id"]), None)
            if row and old.section in SECTIONS:
                gate_refs.append(ArtifactRef.model_validate(row[old.section]))
            elif old.artifact_type != ArtifactType.REVIEW_REPORT or all(
                row.get("review") for row in rows if UUID(row["chapter_id"]) in ids
            ):
                gate_refs.append(ref)
        updated_gate = gate.model_copy(update={"artifact_refs": tuple(gate_refs)})
        if conflict and gate.kind == "APPROVAL":
            updated_gate = updated_gate.model_copy(
                update={
                    "kind": "DECISION",
                    "paths": tuple(paths),
                    "reason": "工作稿修改了已确认的依据或 Auto 下存在手动编辑。"
                    "请选择重新生成、保留手动稿或退出。",
                    "options": ("REGENERATE", "KEEP", "EXIT"),
                }
            )
        update["pending_gate"] = updated_gate.model_dump(mode="json")
        if "rows" not in values and values.get("workflow_version", 1) >= 2:
            # Preserve the reason across a second synchronization at Decision.
            update["upstream_changed"] = list(
                dict.fromkeys((*values.get("upstream_changed", []), *upstream_changed))
            )
        return update, conflict

    def _skill_context(
        self,
        project: Project,
        request: WorkflowRequest,
        stage: str,
        chapter_index: int = 0,
        evidence: str = "",
    ):
        refs, guidance = skill_context(
            self.skills, project, request, stage, chapter_index, evidence
        )
        if self.on_skill:
            for ref in refs:
                self.on_skill(ref)
        return refs, guidance

    def _artifact_block(self, project: Project, ref: ArtifactRef, kind: str) -> ContextBlock:
        return self.context_builder._artifact_block(project, ref, kind)

    def _formal_snapshot(
        self, project: Project, request: WorkflowRequest, *, include_attachments: bool = False
    ) -> ProjectReadSnapshot:
        return self.context_builder._formal_snapshot(
            project, request, include_attachments=include_attachments
        )

    @staticmethod
    def _context_chapters(snapshot: ProjectReadSnapshot, request: WorkflowRequest) -> list[UUID]:
        return WorkflowContextBuilder._context_chapters(snapshot, request)

    @staticmethod
    def _character_files(snapshot: ProjectReadSnapshot) -> list[tuple[str, str]]:
        return WorkflowContextBuilder._character_files(snapshot)

    def _formal_blocks(
        self,
        project: Project,
        request: WorkflowRequest,
        *,
        snapshot: ProjectReadSnapshot | None = None,
    ) -> list[ContextBlock]:
        return self.context_builder._formal_blocks(project, request, snapshot=snapshot)

    def _context(self, project: Project, request: WorkflowRequest, state: _State) -> str:
        return self.context_builder._context(project, request, state)

    def _formal_context(self, project: Project, request: WorkflowRequest) -> list[tuple[str, str]]:
        return self.context_builder._formal_context(project, request)

    def _generate(self, project: Project, state: _State, stage: str):
        request = WorkflowRequest.model_validate(state["request"])
        model = self.sessions.load(project, request.session_id).current_model
        evidence_ref = state.get("outline_ref") or state.get("summary_ref")
        evidence = (
            self.artifacts.get(project, ArtifactRef.model_validate(evidence_ref)).content
            if evidence_ref
            else ""
        )
        used_skills, guidance = self._skill_context(project, request, stage, evidence=evidence)
        content = self.generator.generate(
            stage,
            request.instruction
            + ("\n约束：" + "；".join(request.constraints) if request.constraints else ""),
            GenerationContext(
                (
                    *self._context(project, request, state).blocks,
                    ContextBlock("skill", guidance, source="Skill snapshots", status="METHOD"),
                )
            ),
            feedback=state.get("feedback", ""),
            model=model,
        )
        if not content.strip():
            raise InvalidChange(f"{stage} generator returned empty content")
        return content, used_skills

    @staticmethod
    def _gate(request: WorkflowRequest, state: _State, stage: str, kind: str) -> WorkflowGate:
        round_number = state.get(
            {"PLAN": "plan_round", "SUMMARY": "summary_round", "OUTLINE": "outline_round"}.get(
                stage, "manuscript_round"
            ),
            0,
        )
        names = {
            "PLAN": ("summary_ref", "outline_ref"),
            "SUMMARY": ("summary_ref",),
            "OUTLINE": ("outline_ref",),
            "MANUSCRIPT": ("manuscript_ref", "review_ref"),
        }[stage]
        refs = tuple(_ref(state, name) for name in names if state.get(name))
        return WorkflowGate(
            gate_id=uuid5(request.workflow_id, f"{kind}:{stage}:{round_number}"),
            kind=kind,
            stage=stage,
            artifact_refs=refs,
        )

    def _build(self, project: Project, saver, *, workflow_version: int = 2):
        split = workflow_version >= 2
        graph = StateGraph(_State)
        nodes_impl = SingleChapterNodes(self, project, split=split)
        nodes = {
            "prepare": nodes_impl.prepare,
            "summary": nodes_impl.summary,
            "outline": nodes_impl.outline,
            "plan_gate": nodes_impl.plan_gate,
            "approval_plan": nodes_impl.approval,
            "approve_plan": nodes_impl.approve_plan,
            "revise_plan": nodes_impl.revise_plan,
            "manuscript": nodes_impl.manuscript,
            "review": nodes_impl.review,
            "manuscript_gate": nodes_impl.manuscript_gate,
            "approval_manuscript": nodes_impl.approval,
            "approve_manuscript": nodes_impl.approve_manuscript,
            "revise_manuscript": nodes_impl.revise_manuscript,
            "commit": nodes_impl.commit,
            "decision": nodes_impl.decision,
            "regenerate": nodes_impl.regenerate,
            "finish": nodes_impl.finish,
            "exit": nodes_impl.exit_workflow,
            "generation_decision": self._generation_decision,
        }
        if split:
            nodes.update(
                {
                    "summary_gate": nodes_impl.summary_gate,
                    "approval_summary": nodes_impl.approval,
                    "approve_summary": nodes_impl.approve_summary,
                    "revise_summary": nodes_impl.revise_summary,
                    "outline_gate": nodes_impl.outline_gate,
                    "approval_outline": nodes_impl.approval,
                    "approve_outline": nodes_impl.approve_outline,
                    "revise_outline": nodes_impl.revise_outline,
                    "reconfirm_upstream": nodes_impl.reconfirm_upstream,
                }
            )
        for name, function in nodes.items():
            graph.add_node(name, function)
        edges = [
            (START, "prepare"),
            ("prepare", "summary"),
            ("manuscript_gate", "approval_manuscript"),
            ("revise_manuscript", "manuscript"),
            ("regenerate", "prepare"),
            ("finish", END),
            ("exit", END),
        ]
        edges.extend(
            [
                ("summary_gate", "approval_summary"),
                ("outline_gate", "approval_outline"),
            ]
            if split
            else [
                ("plan_gate", "approval_plan"),
                ("approve_plan", "manuscript"),
                ("revise_plan", "summary"),
            ]
        )
        for source, target in edges:
            graph.add_edge(source, target)
        for source, target in {
            "summary": "summary_gate" if split else "outline",
            "outline": "outline_gate" if split else "plan_gate",
            "manuscript": "review",
            "review": "manuscript_gate",
            "approve_manuscript": "commit",
        }.items():
            graph.add_conditional_edges(
                source,
                lambda state: "blocked" if state.get("generation_problem") else "next",
                {"blocked": "generation_decision", "next": target},
            )
        graph.add_conditional_edges(
            "generation_decision",
            self._generation_route,
            {
                name: name
                for name in (
                    "summary",
                    "outline",
                    "manuscript",
                    "review",
                    "approve_manuscript",
                    "exit",
                    *(("reconfirm_upstream",) if split else ()),
                )
            },
        )
        if split:
            for name in ("summary", "outline"):
                graph.add_conditional_edges(
                    "approval_" + name,
                    nodes_impl.approval_route,
                    {
                        "approve": "approve_" + name,
                        "revise": "revise_" + name,
                        "upstream": "reconfirm_upstream",
                        "exit": "exit",
                    },
                )
                graph.add_conditional_edges(
                    "revise_" + name,
                    lambda state: "wait" if state.get("pending_gate") else "generate",
                    {"wait": "approval_" + name, "generate": name},
                )
            graph.add_conditional_edges(
                "approve_summary",
                nodes_impl.after_summary_approval,
                {"outline": "outline", "outline_gate": "outline_gate"},
            )
            graph.add_conditional_edges(
                "approve_outline",
                nodes_impl.after_outline_approval,
                {"manuscript": "manuscript", "review": "review"},
            )
            graph.add_conditional_edges(
                "reconfirm_upstream",
                lambda state: state["return_stage"],
                {"summary": "summary_gate", "outline": "outline_gate"},
            )
        else:
            graph.add_conditional_edges(
                "approval_plan",
                nodes_impl.approval_route,
                {"approve": "approve_plan", "revise": "revise_plan", "exit": "exit"},
            )
        graph.add_conditional_edges(
            "approval_manuscript",
            nodes_impl.approval_route,
            {
                "approve": "approve_manuscript",
                "revise": "revise_manuscript",
                **({"upstream": "reconfirm_upstream"} if split else {}),
                "exit": "exit",
            },
        )
        graph.add_conditional_edges(
            "commit",
            nodes_impl.commit_route,
            {
                "decision": "decision",
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "decision",
            nodes_impl.decision_route,
            {
                "regenerate": "regenerate",
                "exit": "exit",
            },
        )
        return graph.compile(checkpointer=saver, name=self.workflow_kind.lower())

    @contextmanager
    def _graph(self, project: Project, workflow_id: UUID) -> Iterator:
        path = project.root / ".novel-agent" / "workflows.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(path)) as saver:
            checkpoint = saver.get_tuple(self._config(workflow_id))
            # Existing checkpoints have no version and retain their original nodes
            # and interrupts. New single-chapter runs persist v2 before generation.
            version = (
                checkpoint.checkpoint["channel_values"].get("workflow_version", 1)
                if checkpoint
                else 2
            )
            yield self._build(project, saver, workflow_version=version)

    @staticmethod
    def _config(workflow_id: UUID) -> dict:
        return {"configurable": {"thread_id": str(workflow_id)}}

    @staticmethod
    def _snapshot(workflow_id: UUID, values: dict) -> WorkflowSnapshot:
        if not values:
            raise InvalidChange("Workflow checkpoint does not exist")
        gate = (
            WorkflowGate.model_validate(values["pending_gate"])
            if values.get("pending_gate")
            else None
        )
        result = WorkflowResult.model_validate(values["result"]) if values.get("result") else None
        status = (
            result.status
            if result
            else (
                "WAITING_APPROVAL"
                if gate and gate.kind == "APPROVAL"
                else "WAITING_DECISION"
                if gate
                else "RUNNING"
            )
        )
        refs = tuple(
            _ref(values, name)
            for name in ("summary_ref", "outline_ref", "manuscript_ref", "review_ref")
            if values.get(name)
        )
        return WorkflowSnapshot(
            workflow_id=workflow_id,
            kind=values.get("request", {}).get("kind"),
            status=status,
            gate=gate,
            result=result,
            artifact_refs=refs,
            approvals=tuple(
                ApprovalRecord.model_validate(item) for item in values.get("approvals", [])
            ),
        )

    def _sync_session(self, project: Project, session_id: UUID, snapshot: WorkflowSnapshot) -> None:
        store = SessionStateStore(project.root)
        state = store.load(session_id)
        if snapshot.result:
            state.active_workflow_id = None
            state.active_workflow_kind = None
            state.last_workflow_result = snapshot.result.model_dump(mode="json")
            if (
                snapshot.result.status == "COMPLETED"
                and snapshot.result.task_id not in state.completed_task_ids
            ):
                state.completed_task_ids.append(snapshot.result.task_id)
            changed = ", ".join(snapshot.result.project_changes) or "none"
            state.remember(
                "assistant",
                f"Workflow {snapshot.result.workflow_id} {snapshot.result.status}; "
                f"changed Project files: {changed}",
            )
        else:
            state.active_workflow_id = snapshot.workflow_id
            state.active_workflow_kind = snapshot.kind
        store.save(session_id, state)

    def start(self, project: Project, request: WorkflowRequest) -> WorkflowSnapshot:
        if request.kind != self.workflow_kind:
            raise InvalidChange(f"Expected {self.workflow_kind} Workflow Request")
        if request.project_id != project.project_id:
            raise InvalidChange("Workflow Request belongs to another Project")
        self.sessions.load(project, request.session_id)
        current = SessionStateStore(project.root).load(request.session_id)
        if current.active_workflow_id:
            raise InvalidChange("Session already has an active Workflow")
        missing = tuple(
            task_id for task_id in request.depends_on if task_id not in current.completed_task_ids
        )
        if missing:
            raise InvalidChange(f"Task blocked by incomplete dependencies: {missing}")
        with self._graph(project, request.workflow_id) as graph:
            config = self._config(request.workflow_id)
            if graph.get_state(config).values:
                raise InvalidChange("Workflow ID already exists")
            graph.invoke(
                {
                    "workflow_version": 2,
                    "request": request.model_dump(mode="json"),
                    "plan_round": 0,
                    "manuscript_round": 0,
                    "feedback": "",
                },
                config,
                durability="sync",
            )
            snapshot = self._snapshot(request.workflow_id, graph.get_state(config).values)
        self._sync_session(project, request.session_id, snapshot)
        return snapshot

    def load(self, project: Project, workflow_id: UUID) -> WorkflowSnapshot:
        with self._graph(project, workflow_id) as graph:
            values = graph.get_state(self._config(workflow_id)).values
        snapshot = self._snapshot(workflow_id, values)
        request = WorkflowRequest.model_validate(values["request"])
        if request.project_id != project.project_id:
            raise InvalidChange("Workflow belongs to another Project")
        return snapshot

    def load_active(self, project: Project, session_id: UUID) -> WorkflowSnapshot | None:
        self.sessions.load(project, session_id)
        workflow_id = SessionStateStore(project.root).load(session_id).active_workflow_id
        return self.load(project, workflow_id) if workflow_id else None

    def resume(
        self, project: Project, workflow_id: UUID, response: GateResponse
    ) -> WorkflowSnapshot:
        with self._graph(project, workflow_id) as graph:
            config = self._config(workflow_id)
            values = graph.get_state(config).values
            snapshot = self._snapshot(workflow_id, values)
            request = WorkflowRequest.model_validate(values["request"])
            if request.project_id != project.project_id:
                raise InvalidChange("Workflow belongs to another Project")
            if snapshot.result or snapshot.gate is None:
                raise InvalidChange("Workflow is not waiting at a Gate")
            if response.gate_id != snapshot.gate.gate_id:
                raise InvalidChange("Response targets a different Gate")
            if snapshot.gate.kind == "APPROVAL" and response.action not in {
                "APPROVE",
                "REVISE",
                "REJECT",
                "EXIT",
            }:
                raise InvalidChange("Invalid Approval response")
            if snapshot.gate.kind == "DECISION" and response.action not in set(
                snapshot.gate.options or ("REGENERATE", "EXIT")
            ):
                raise InvalidChange("Decision requires an explicit choice")
            if response.action == "REVISE" and not (response.feedback or "").strip():
                raise InvalidChange("Revision feedback is required")
            if snapshot.gate.stage != "COMMIT":
                auto = request.auto_approval
                if auto is None:
                    auto = self.sessions.load(project, request.session_id).auto_approval
                update, conflict = self._sync_drafts(
                    project, values, snapshot.gate, auto=auto and response.action == "APPROVE"
                )
                node = (
                    values["generation_retry_node"]
                    if snapshot.gate.generation_blocked
                    else {
                        "PLAN": "plan_gate",
                        "SUMMARY": "summary_gate",
                        "OUTLINE": "outline_gate",
                        "SUMMARIES": "summary_gate",
                        "OUTLINES": "outline_gate",
                        "MANUSCRIPT": "manuscript_gate",
                    }[snapshot.gate.stage]
                )
                graph.update_state(config, update, as_node=node)
                if conflict and snapshot.gate.kind == "APPROVAL" and response.action == "APPROVE":
                    updated = self._snapshot(workflow_id, graph.get_state(config).values)
                    self._sync_session(project, request.session_id, updated)
                    return updated
            graph.invoke(
                Command(resume=response.model_dump(mode="json")), config, durability="sync"
            )
            updated = self._snapshot(workflow_id, graph.get_state(config).values)
        self._sync_session(project, request.session_id, updated)
        return updated

    def apply_auto(self, project: Project, workflow_id: UUID) -> WorkflowSnapshot:
        snapshot = self.load(project, workflow_id)
        if snapshot.gate is None or snapshot.gate.kind != "APPROVAL":
            raise InvalidChange("Auto Approval applies only to an Approval Gate")
        with self._graph(project, workflow_id) as graph:
            request = WorkflowRequest.model_validate(
                graph.get_state(self._config(workflow_id)).values["request"]
            )
        auto = request.auto_approval
        if auto is None:
            auto = self.sessions.load(project, request.session_id).auto_approval
        if not auto:
            raise InvalidChange("Auto Approval is disabled for this Session")
        return self.resume(
            project, workflow_id, GateResponse(gate_id=snapshot.gate.gate_id, action="APPROVE")
        )

    def exit(self, project: Project, workflow_id: UUID) -> WorkflowSnapshot:
        snapshot = self.load(project, workflow_id)
        if snapshot.gate is None:
            raise InvalidChange("Workflow is not waiting at a Gate")
        return self.resume(
            project, workflow_id, GateResponse(gate_id=snapshot.gate.gate_id, action="EXIT")
        )


class ContinueChapterWorkflow(CreateChapterWorkflow):
    workflow_kind = "CONTINUE_CHAPTER"


class BackfillChapterWorkflow(CreateChapterWorkflow):
    workflow_kind = "BACKFILL_CHAPTER"


class RewriteChapterWorkflow(CreateChapterWorkflow):
    workflow_kind = "REWRITE_CHAPTER"
