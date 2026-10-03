"""Handle pending draft confirmations before invoking the model."""

from __future__ import annotations

import re
from uuid import UUID

from novel_agent.agent.freeform_policy import (
    _has_commit_intent,
)
from novel_agent.agent.freeform_types import FreeformRunScope, RunResult
from novel_agent.prompts.prompt import (
    REVISION_REQUEST,
)
from novel_agent.runtime.events import RuntimeEvent


class FreeformDraftController:
    @staticmethod
    def handle(scope: FreeformRunScope, prompt: str) -> str | RunResult:
        if scope.state.pending_draft and not scope.state.active_workflow_id:
            value = prompt.strip().casefold()
            if scope.state.pending_draft.get("decision"):
                if value not in {"1", "2", "3"}:
                    return RunResult(
                        response="工作稿等待选择：1 重新生成、2 保留手动稿、3 退出确认。",
                        model=scope.definition.model_id,
                    )
                if value == "3":
                    scope.agent._sync_freeform_draft(scope.project, scope.session, scope.state)
                    scope.state.pending_draft = None
                    scope.store.save(scope.session.session_id, scope.state)
                    return RunResult(
                        response="已退出工作稿确认，Artifact 和 draft 已保留。",
                        model=scope.definition.model_id,
                    )
                scope.state.pending_draft.pop("decision")
                value = "y" if value == "2" else "n"
                scope.store.save(scope.session.session_id, scope.state)
            if value in {"e", "退出确认"}:
                scope.agent._sync_freeform_draft(scope.project, scope.session, scope.state)
                scope.state.pending_draft = None
                scope.store.save(scope.session.session_id, scope.state)
                return RunResult(
                    response="已退出工作稿确认，Artifact 和 draft 已保留。",
                    model=scope.definition.model_id,
                    file_review=True,
                )
            if value in {"y", "yes", "确认", "批准", "同意", "通过"}:
                sync = scope.agent._sync_freeform_draft(scope.project, scope.session, scope.state)
                if scope.state.pending_draft["section"] == "manuscript" and any(
                    key in sync.changed for key in ("summary", "outline")
                ):
                    scope.state.pending_draft["decision"] = True
                    scope.store.save(scope.session.session_id, scope.state)
                    return RunResult(
                        response="已保存手动修改；正文依据发生变化。"
                        "输入 1 重新生成正文、2 保留手动正文、3 退出确认。",
                        model=scope.definition.model_id,
                    )
                for ref in sync.refs.values():
                    scope.agent.artifacts.approve(scope.project, ref)
                pending = scope.state.pending_draft
                cleaned = False
                if pending["section"] == "manuscript":
                    cleaned = scope.agent.drafts.clean(
                        scope.project,
                        scope.session.session_id,
                        UUID(pending["task_id"]),
                        UUID(pending["chapter_id"]),
                    )
                    if cleaned:
                        scope.state.pending_draft = None
                scope.state.remember("user", prompt)
                scope.result.events = [
                    RuntimeEvent("Reviewing", "同步手动工作稿"),
                    RuntimeEvent("Completed", "工作稿批准"),
                ]
                if scope.on_event:
                    for event in scope.result.events:
                        scope.on_event(event)
                scope.result.response = "已保存并批准手动工作稿。" + (
                    "正文 draft 已删除；可用 /commit current 正式提交。"
                    if cleaned
                    else "draft 已保留，可继续编辑或创作。"
                )
                scope.result.display_refs = list(sync.refs.values())
                scope.result.file_review = True
                scope.state.remember(
                    "assistant",
                    scope.result.response
                    + "\n"
                    + ", ".join(
                        f"{key}: {ref.artifact_id}@{ref.revision}" for key, ref in sync.refs.items()
                    ),
                )
                scope.store.save(scope.session.session_id, scope.state)
                return scope.result
            if (
                value in {"n", "no", "拒绝", "不通过"}
                or (
                    re.search(r"修改|修订|调整|改写|重写|改成|增加|减少|补充|重做|重新写", prompt)
                    and not re.search(r"[?？]|如何|怎么|是否", prompt)
                )
                or (_has_commit_intent(prompt) and not re.search(r"@\d+", prompt))
            ):
                sync = scope.agent._sync_freeform_draft(scope.project, scope.session, scope.state)
                scope.store.save(scope.session.session_id, scope.state)
                if value in {"n", "no", "拒绝", "不通过"}:
                    ref = sync.refs[scope.state.pending_draft["section"]]
                    prompt = REVISION_REQUEST.format(
                        artifact_id=ref.artifact_id, revision=ref.revision
                    )

        return prompt
