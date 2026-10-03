"""Multi Chapter and Import Novel business nodes, independent of graph composition."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from langgraph.types import interrupt

from novel_agent.domain.artifact import ArtifactRef, ArtifactType
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.domain.errors import DecisionRequired, GenerationBlocked, InvalidChange
from novel_agent.domain.models import Project
from novel_agent.prompts.prompt import ContextBlock, GenerationContext
from novel_agent.services.draft import SECTIONS
from novel_agent.workflow.batch_state import _ref
from novel_agent.workflow.import_source import extract_novel_source
from novel_agent.workflow.models import (
    ApprovalRecord,
    GateResponse,
    WorkflowGate,
    WorkflowRequest,
    WorkflowResult,
)

if TYPE_CHECKING:
    from novel_agent.workflow.batch_chapters import BatchChapterWorkflow


class BatchChapterNodes:
    """Business nodes; graph names and interrupt order belong to the facade."""

    def __init__(
        self, workflow: BatchChapterWorkflow, project: Project, *, split: bool = False
    ) -> None:
        self.workflow = workflow
        self.project = project
        self.split = split

    def request_of(self, state: dict) -> WorkflowRequest:
        return WorkflowRequest.model_validate(state["request"])

    def auto_enabled(self, request: WorkflowRequest) -> bool:
        if request.auto_approval is not None:
            return request.auto_approval
        return self.workflow.sessions.load(self.project, request.session_id).auto_approval

    def prepare(self, state: dict) -> dict:
        request = self.request_of(state)
        current = self.workflow.projects.open_project(self.project.root)
        if current.project_id != request.project_id:
            raise InvalidChange("Workflow Project identity changed")
        self.workflow.sessions.load(current, request.session_id)
        if request.kind == "IMPORT_NOVEL":
            if not request.source_path:
                raise InvalidChange("Import requires a Project sources/ file")
            imported = extract_novel_source(current.root, request.source_path)
            targets = [
                (uuid5(request.workflow_id, f"import:{i}"), chapter.title, chapter.manuscript)
                for i, chapter in enumerate(imported)
            ]
        else:
            if len(request.chapters) < 2:
                raise InvalidChange("Multi Chapter requires at least two Chapter targets")
            targets = [(target.chapter_id, target.title, None) for target in request.chapters]
        ids = [chapter_id for chapter_id, _, _ in targets]
        if len(ids) != len(set(ids)) or any(
            str(chapter_id) in current.metadata.chapters for chapter_id in ids
        ):
            raise InvalidChange("Workflow Chapter IDs are duplicate or already formal")
        paths = ["PROJECT.md"]
        if current.metadata.chapter_order:
            entry = current.metadata.chapters[str(current.metadata.chapter_order[-1])]
            paths.append(f"chapters/{entry.filename}")
        for relative in request.attachments:
            path = self.workflow.projects.resolve_attachment(current, relative)
            paths.append(path.relative_to(current.root).as_posix())
        if request.source_path:
            paths.append(request.source_path)
        rows = []
        for index, (chapter_id, title, imported_text) in enumerate(targets):
            row = {"chapter_id": str(chapter_id), "title": title}
            if request.kind != "IMPORT_NOVEL":
                target = request.chapters[index]
                row["brief"] = target.brief
                if target.brief_ref:
                    row["brief_ref"] = target.brief_ref.model_dump(mode="json")
            if imported_text is not None:
                revision = self.workflow.artifacts.create(
                    current,
                    request.session_id,
                    ArtifactType.MANUSCRIPT,
                    "SECTION",
                    imported_text,
                    chapter_id=chapter_id,
                    section="manuscript",
                    dependency_paths=tuple(paths),
                )
                row["manuscript"] = revision.ref.model_dump(mode="json")
            rows.append(row)
        return {"rows": rows, "paths": paths, "index": 0, "round": state.get("round", 0)}

    def context(self, state: dict, index: int, *, include_prior: bool = True) -> str:
        request = self.request_of(state)
        snapshot = self.workflow._formal_snapshot(self.project, request, include_attachments=True)
        blocks = self.workflow._formal_blocks(self.project, request, snapshot=snapshot)
        row = state["rows"][index]
        brief = row.get("brief", "")
        if row.get("brief_ref"):
            brief = self.workflow.artifacts.get(
                self.project, ArtifactRef.model_validate(row["brief_ref"])
            ).content
        target = f"目标章节：{row['title']}（第 {index + 1} / {len(state['rows'])} 章）\n{brief}"
        if request.planned_total:
            target += (
                f"\n全书计划：{request.planned_total} 章；本次仅创作 {len(state['rows'])} 章。"
            )
        blocks.append(
            ContextBlock("chapter_brief", target, source="WorkflowRequest", status="USER")
        )
        if include_prior and index:
            previous = state["rows"][index - 1]
            for name in ("manuscript", "outline", "summary"):
                if previous.get(name):
                    blocks.append(
                        self.workflow._artifact_block(
                            self.project, _ref(previous, name), "prior_" + name
                        )
                    )
                    break
        for name in SECTIONS:
            if row.get(name):
                kind = (
                    "imported_manuscript"
                    if name == "manuscript" and request.kind == "IMPORT_NOVEL"
                    else "current_" + name
                )
                blocks.append(self.workflow._artifact_block(self.project, _ref(row, name), kind))
        for reference in request.working_artifact_refs:
            artifact = self.workflow.artifacts.get(self.project, reference)
            if artifact.session_id != request.session_id:
                raise InvalidChange("Working Artifact belongs to another Session")
            blocks.append(
                self.workflow._artifact_block(
                    self.project, reference, "working_" + (artifact.section or "context")
                )
            )
        for relative in request.attachments:
            blocks.append(
                ContextBlock("attachment", snapshot.text_files[relative], source=relative)
            )
        return GenerationContext(blocks)

    def generate(self, state: dict, index: int, stage: str) -> dict:
        request = self.request_of(state)
        rows = [dict(row) for row in state["rows"]]
        row = rows[index]
        model = self.workflow.sessions.load(self.project, request.session_id).current_model
        instruction = request.instruction + f"\n本次目标：《{row['title']}》"
        if request.constraints:
            instruction += "\n约束：" + "；".join(request.constraints)
        evidence = ""
        for key in ("outline", "summary"):
            if row.get(key):
                evidence = self.workflow.artifacts.get(self.project, _ref(row, key)).content
                break
        used_skills, guidance = self.workflow._skill_context(
            self.project, request, stage, chapter_index=index, evidence=evidence
        )
        try:
            content = self.workflow.generator.generate(
                stage,
                instruction,
                GenerationContext(
                    (
                        *self.context(state, index).blocks,
                        ContextBlock("skill", guidance, source="Skill snapshots", status="METHOD"),
                    )
                ),
                feedback=state.get("feedback", ""),
                model=model,
            )
        except GenerationBlocked as exc:
            return self.workflow._generation_failure(self.project, state, exc, stage)
        if not content.strip():
            raise InvalidChange(f"{stage} generator returned empty content")
        name = "manuscript" if stage == "manuscript" else stage
        artifact_type = {
            "summary": ArtifactType.SUMMARY,
            "outline": ArtifactType.OUTLINE,
            "manuscript": ArtifactType.MANUSCRIPT,
            "review": ArtifactType.REVIEW_REPORT,
        }[stage]
        dependencies = tuple(
            _ref(row, key)
            for key in ("summary", "outline", "manuscript")
            if key != name and row.get(key)
        )
        if index > 0 and stage in {"outline", "manuscript", "review"}:
            previous = rows[index - 1]
            for key in ("manuscript", "outline", "summary"):
                if previous.get(key):
                    dependencies += (_ref(previous, key),)
                    break
        if row.get(name):
            revision = self.workflow.artifacts.revise(
                self.project,
                _ref(row, name),
                content,
                skill_refs=used_skills,
                dependencies=dependencies,
            )
        else:
            revision = self.workflow.artifacts.create(
                self.project,
                request.session_id,
                artifact_type,
                "PROJECT" if stage == "review" else "SECTION",
                content,
                chapter_id=None if stage == "review" else UUID(row["chapter_id"]),
                section=None if stage == "review" else name,
                dependency_paths=tuple(state["paths"]),
                dependencies=dependencies,
                skill_refs=used_skills,
            )
        row[name] = revision.ref.model_dump(mode="json")
        if stage == "review" and request.target_characters:
            body = self.workflow.artifacts.get(self.project, _ref(row, "manuscript")).content
            count = sum(char not in "\r\n" for char in body)
            lower = int(request.target_characters * 0.9)
            upper = int(request.target_characters * 1.1)
            verdict = "符合" if lower <= count <= upper else "不符合"
            measured = self.workflow.artifacts.revise(
                self.project,
                revision.ref,
                f"字数检查：{count} 字，目标 {request.target_characters} 字，"
                f"允许 {lower}–{upper} 字；{verdict}。\n\n{content}",
                skill_refs=used_skills,
            )
            row[name] = measured.ref.model_dump(mode="json")
        return {"rows": rows, "generation_problem": None, "pending_gate": None}

    def summaries(self, state: dict) -> dict:
        current = state
        for index in range(state.get("generation_resume_index") or 0, len(state["rows"])):
            current = {**current, **self.generate(current, index, "summary")}
            if current.get("generation_problem"):
                return {
                    "generation_resume_index": index,
                    **{
                        key: current[key]
                        for key in (
                            "rows",
                            "pending_gate",
                            "generation_problem",
                            "generation_retry_node",
                            "generation_answer",
                            "generation_round",
                        )
                    },
                }
        return {"rows": current["rows"], "feedback": "", "generation_resume_index": None}

    def group(self, state: dict) -> range:
        index = state["index"]
        size = 10 if self.request_of(state).kind == "IMPORT_NOVEL" else 1
        return range(index, min(index + size, len(state["rows"])))

    def outlines(self, state: dict) -> dict:
        current = state
        for index in self.group(state):
            if index < (state.get("generation_resume_index") or 0):
                continue
            current = {**current, **self.generate(current, index, "outline")}
            if current.get("generation_problem"):
                return {
                    "generation_resume_index": index,
                    **{
                        key: current[key]
                        for key in (
                            "rows",
                            "pending_gate",
                            "generation_problem",
                            "generation_retry_node",
                            "generation_answer",
                            "generation_round",
                        )
                    },
                }
        return {"rows": current["rows"], "feedback": "", "generation_resume_index": None}

    def manuscript(self, state: dict) -> dict:
        return {**self.generate(state, state["index"], "manuscript"), "feedback": ""}

    def review(self, state: dict) -> dict:
        return self.generate(state, state["index"], "review")

    def make_gate(self, state: dict, stage: str) -> dict:
        request = self.request_of(state)
        if stage == "SUMMARIES":
            refs = tuple(_ref(row, "summary") for row in state["rows"])
        elif stage == "OUTLINES":
            refs = tuple(_ref(state["rows"][index], "outline") for index in self.group(state))
        else:
            row = state["rows"][state["index"]]
            refs = (_ref(row, "manuscript"), _ref(row, "review"))
        gate = WorkflowGate(
            gate_id=uuid5(request.workflow_id, f"{stage}:{state['index']}:{state.get('round', 0)}"),
            kind="APPROVAL",
            stage=stage,
            artifact_refs=refs,
        )
        gate = self.workflow._export_at_gate(self.project, state, gate)
        return {"pending_gate": gate.model_dump(mode="json")}

    def summary_gate(self, state: dict) -> dict:
        return self.make_gate(state, "SUMMARIES")

    def outline_gate(self, state: dict) -> dict:
        return self.make_gate(state, "OUTLINES")

    def manuscript_gate(self, state: dict) -> dict:
        return self.make_gate(state, "MANUSCRIPT")

    def approval(self, state: dict) -> dict:
        gate = WorkflowGate.model_validate(state["pending_gate"])
        auto = self.auto_enabled(self.request_of(state))
        answer = (
            GateResponse(gate_id=gate.gate_id, action="APPROVE")
            if auto and gate.kind == "APPROVAL"
            else GateResponse.model_validate(interrupt(gate.model_dump(mode="json")))
        )
        if answer.gate_id != gate.gate_id:
            raise InvalidChange("Approval response targets a different Gate")
        return {
            "answer": answer.model_dump(mode="json"),
            "approval_source": "AUTO" if auto and gate.kind == "APPROVAL" else "HUMAN",
            "pending_gate": None,
        }

    def approval_route(self, state: dict) -> str:
        action = GateResponse.model_validate(state["answer"]).action
        if action in {"APPROVE", "KEEP"}:
            return "approve"
        if action in {"REVISE", "REJECT", "REGENERATE"}:
            return "revise"
        if action == "EXIT":
            return "exit"
        raise InvalidChange("Invalid Approval response")

    def revise(self, state: dict) -> dict:
        answer = GateResponse.model_validate(state["answer"])
        if answer.action == "REVISE" and not (answer.feedback or "").strip():
            raise InvalidChange("Revision feedback is required")
        return {
            "feedback": answer.feedback or "请重新生成当前候选稿，保留已确认约束。",
            "round": state.get("round", 0) + 1,
            "answer": None,
        }

    def revise_manuscript(self, state: dict) -> dict:
        update = self.revise(state)
        rows = [dict(row) for row in state["rows"]]
        rows[state["index"]].pop("review", None)
        return {**update, "rows": rows}

    def log_approval(self, state: dict, stage: str, refs: tuple[ArtifactRef, ...]) -> list[dict]:
        answer = GateResponse.model_validate(state["answer"])
        record = ApprovalRecord(
            gate_id=answer.gate_id,
            stage=stage,
            source=state["approval_source"],
            artifact_refs=refs,
        )
        return [*state.get("approvals", []), record.model_dump(mode="json")]

    def approve_summaries(self, state: dict) -> dict:
        refs = tuple(_ref(row, "summary") for row in state["rows"])
        for row in state["rows"]:
            self.workflow.artifacts.approve(self.project, _ref(row, "summary"))
        return {
            "index": 0,
            "answer": None,
            "approvals": self.log_approval(state, "SUMMARIES", refs),
        }

    def approve_outlines(self, state: dict) -> dict:
        refs = tuple(_ref(state["rows"][index], "outline") for index in self.group(state))
        for index in self.group(state):
            self.workflow.artifacts.approve(self.project, _ref(state["rows"][index], "outline"))
        return {
            "index": self.group(state).stop,
            "answer": None,
            "approvals": self.log_approval(state, "OUTLINES", refs),
        }

    def after_outlines(self, state: dict) -> str:
        if state["index"] < len(state["rows"]):
            return "more"
        return "commit" if self.request_of(state).kind == "IMPORT_NOVEL" else "manuscript"

    def begin_manuscripts(self, state: dict) -> dict:
        return {"index": 0}

    def approve_manuscript(self, state: dict) -> dict:
        refreshed = (
            self.generate(state, state["index"], "review")
            if not (state["rows"][state["index"]].get("review"))
            else {}
        )
        if refreshed.get("generation_problem"):
            return {**refreshed, "generation_retry_node": "approve_manuscript"}
        state = {**state, **refreshed}
        ref = _ref(state["rows"][state["index"]], "manuscript")
        self.workflow.artifacts.approve(self.project, ref)
        request = self.request_of(state)
        self.workflow.drafts.clean(
            self.project,
            request.session_id,
            request.task_id,
            UUID(state["rows"][state["index"]]["chapter_id"]),
        )
        return {
            **refreshed,
            "index": state["index"] + 1,
            "answer": None,
            "approvals": self.log_approval(state, "MANUSCRIPT", (ref,)),
        }

    def after_manuscript(self, state: dict) -> str:
        if state.get("generation_problem"):
            return "blocked"
        return "more" if state["index"] < len(state["rows"]) else "commit"

    def commit(self, state: dict) -> dict:
        request = self.request_of(state)
        changes = []
        for row in state["rows"]:
            chapter_id = UUID(row["chapter_id"])
            changes.extend(
                (
                    CreateChapter(row["title"], chapter_id=chapter_id),
                    SetSection(chapter_id, "summary", artifact=_ref(row, "summary")),
                    SetSection(chapter_id, "outline", artifact=_ref(row, "outline")),
                    SetSection(chapter_id, "manuscript", artifact=_ref(row, "manuscript")),
                )
            )
        try:
            result = self.workflow.commits.commit(
                self.project, tuple(changes), commit_id=uuid5(request.workflow_id, "commit")
            )
        except DecisionRequired as exc:
            gate = WorkflowGate(
                gate_id=uuid5(request.workflow_id, f"DECISION:{state.get('round', 0)}"),
                kind="DECISION",
                stage="COMMIT",
                reason=exc.reason,
                paths=exc.paths,
                options=("REGENERATE", "EXIT"),
            )
            return {"pending_gate": gate.model_dump(mode="json"), "commit": None}
        for row in state["rows"]:
            self.workflow.drafts.clean(
                self.project, request.session_id, request.task_id, UUID(row["chapter_id"])
            )
        return {"commit": result.model_dump(mode="json"), "pending_gate": None}

    def commit_route(self, state: dict) -> str:
        return "decision" if state.get("pending_gate") else "finish"

    def decision(self, state: dict) -> dict:
        gate = WorkflowGate.model_validate(state["pending_gate"])
        answer = GateResponse.model_validate(interrupt(gate.model_dump(mode="json")))
        if answer.gate_id != gate.gate_id or answer.action not in {"REGENERATE", "EXIT"}:
            raise InvalidChange("Decision requires an explicit choice")
        return {"answer": answer.model_dump(mode="json"), "pending_gate": None}

    def decision_route(self, state: dict) -> str:
        return (
            "regenerate"
            if GateResponse.model_validate(state["answer"]).action == "REGENERATE"
            else "exit"
        )

    def regenerate(self, state: dict) -> dict:
        return {
            "rows": [],
            "round": state.get("round", 0) + 1,
            "feedback": "",
            "answer": None,
            "index": 0,
        }

    def result_node(self, state: dict, status: str) -> dict:
        request = self.request_of(state)
        refs = tuple(
            _ref(row, name)
            for row in state["rows"]
            for name in ("summary", "outline", "manuscript", "review")
            if row.get(name)
        )
        chapter_ids = tuple(UUID(row["chapter_id"]) for row in state["rows"])
        result = WorkflowResult(
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            status=status,
            chapter_id=chapter_ids[0] if chapter_ids else request.chapter_id,
            chapter_ids=chapter_ids,
            artifact_refs=refs,
            commit=state.get("commit") if status == "COMPLETED" else None,
            completed_tasks=(request.kind,) if status == "COMPLETED" else (),
            project_changes=tuple(state["commit"]["changed_files"])
            if status == "COMPLETED"
            else (),
            remaining_tasks=(request.kind,) if status == "CANCELLED" else (),
            approvals=tuple(
                ApprovalRecord.model_validate(item) for item in state.get("approvals", [])
            ),
            warnings=("确认后文件又被修改，相关 draft 已保留。",)
            if status == "COMPLETED"
            and any(
                (
                    self.workflow.drafts.record(
                        self.project, request.session_id, request.task_id, chapter_id
                    )
                    or {}
                ).get("active")
                for chapter_id in chapter_ids
            )
            else (),
        )
        return {"result": result.model_dump(mode="json"), "pending_gate": None}

    def finish(self, state: dict) -> dict:
        return self.result_node(state, "COMPLETED")

    def exit_workflow(self, state: dict) -> dict:
        return self.result_node(state, "CANCELLED")
