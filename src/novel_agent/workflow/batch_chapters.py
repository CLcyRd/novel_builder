"""M3.2 persisted Multi Chapter and Import Novel LangGraph subgraphs."""

from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, START, StateGraph

from novel_agent.domain.artifact import ArtifactRef
from novel_agent.domain.errors import InvalidChange
from novel_agent.domain.models import Project
from novel_agent.workflow.batch_nodes import BatchChapterNodes
from novel_agent.workflow.batch_state import _BatchState as _BatchState
from novel_agent.workflow.batch_state import _ref as _ref
from novel_agent.workflow.create_chapter import CreateChapterWorkflow
from novel_agent.workflow.models import (
    ApprovalRecord,
    WorkflowGate,
    WorkflowResult,
    WorkflowSnapshot,
)


class BatchChapterWorkflow(CreateChapterWorkflow):
    """Shared durable stages; each concrete class compiles its own named subgraph."""

    workflow_kind = "MULTI_CHAPTER"

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
            ArtifactRef.model_validate(row[name])
            for row in values.get("rows", ())
            for name in ("summary", "outline", "manuscript", "review")
            if row.get(name)
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

    def _build(self, project: Project, saver, *, workflow_version: int = 2):
        split = workflow_version >= 2
        graph = StateGraph(_BatchState)
        nodes_impl = BatchChapterNodes(self, project, split=split)
        nodes = {
            "prepare": nodes_impl.prepare,
            "summaries": nodes_impl.summaries,
            "summary_gate": nodes_impl.summary_gate,
            "approval_summaries": nodes_impl.approval,
            "revise_summaries": nodes_impl.revise,
            "approve_summaries": nodes_impl.approve_summaries,
            "outlines": nodes_impl.outlines,
            "outline_gate": nodes_impl.outline_gate,
            "approval_outlines": nodes_impl.approval,
            "revise_outlines": nodes_impl.revise,
            "approve_outlines": nodes_impl.approve_outlines,
            "begin_manuscripts": nodes_impl.begin_manuscripts,
            "manuscript": nodes_impl.manuscript,
            "review": nodes_impl.review,
            "manuscript_gate": nodes_impl.manuscript_gate,
            "approval_manuscript": nodes_impl.approval,
            "revise_manuscript": nodes_impl.revise_manuscript,
            "approve_manuscript": nodes_impl.approve_manuscript,
            "commit": nodes_impl.commit,
            "decision": nodes_impl.decision,
            "regenerate": nodes_impl.regenerate,
            "finish": nodes_impl.finish,
            "exit": nodes_impl.exit_workflow,
            "generation_decision": self._generation_decision,
        }
        for name, function in nodes.items():
            graph.add_node(name, function)
        for source, target in (
            (START, "prepare"),
            ("prepare", "summaries"),
            ("summary_gate", "approval_summaries"),
            ("revise_summaries", "summaries"),
            ("approve_summaries", "outlines"),
            ("outline_gate", "approval_outlines"),
            ("revise_outlines", "outlines"),
            ("approve_outlines", "begin_manuscripts"),
            ("begin_manuscripts", "manuscript"),
            ("manuscript_gate", "approval_manuscript"),
            ("revise_manuscript", "manuscript"),
            ("regenerate", "prepare"),
            ("finish", END),
            ("exit", END),
        ):
            if source == "approve_outlines" or source == "regenerate":
                continue
            graph.add_edge(source, target)
        graph.add_edge("regenerate", "prepare")
        for source, target in {
            "summaries": "summary_gate",
            "outlines": "outline_gate",
            "manuscript": "review",
            "review": "manuscript_gate",
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
                    "summaries",
                    "outlines",
                    "manuscript",
                    "review",
                    "approve_manuscript",
                    "exit",
                )
            },
        )
        graph.add_conditional_edges(
            "approval_summaries",
            nodes_impl.approval_route,
            {
                "approve": "approve_summaries",
                "revise": "revise_summaries",
                "exit": "exit",
            },
        )
        graph.add_conditional_edges(
            "approval_outlines",
            nodes_impl.approval_route,
            {
                "approve": "approve_outlines",
                "revise": "revise_outlines",
                "exit": "exit",
            },
        )
        graph.add_conditional_edges(
            "approve_outlines",
            nodes_impl.after_outlines,
            {
                "more": "outlines",
                "manuscript": "begin_manuscripts",
                "commit": "commit",
            },
        )
        graph.add_conditional_edges(
            "approval_manuscript",
            nodes_impl.approval_route,
            {
                "approve": "approve_manuscript",
                "revise": "revise_manuscript",
                "exit": "exit",
            },
        )
        graph.add_conditional_edges(
            "approve_manuscript",
            nodes_impl.after_manuscript,
            {
                "blocked": "generation_decision",
                "more": "manuscript",
                "commit": "commit",
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


class MultiChapterWorkflow(BatchChapterWorkflow):
    workflow_kind = "MULTI_CHAPTER"


class ImportNovelWorkflow(BatchChapterWorkflow):
    workflow_kind = "IMPORT_NOVEL"
