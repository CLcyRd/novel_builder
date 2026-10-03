"""Present results and persist successful Freeform exchanges."""

from __future__ import annotations

from novel_agent.agent.freeform_types import FreeformRunScope, RunResult


class FreeformRunRecorder:
    @staticmethod
    def finish(scope: FreeformRunScope, prompt: str, final_message: str) -> RunResult:
        scope.result.response = (
            f"删除未执行：{scope.delete_blocker}。请重新查看 Project 后再提出删除请求。"
            if scope.delete_blocker
            else scope.delete_report or final_message
        )
        if not scope.result.response.strip():
            raise RuntimeError("Model returned no final assistant response")
        if scope.result.draft_paths:
            scope.result.file_review = True
            scope.result.draft_paths = list(dict.fromkeys(scope.result.draft_paths))
            scope.result.response = (
                "工作稿已保存，请打开文件阅读或编辑，保存后输入 y 确认、n 重做，"
                "e 退出确认，或给出修改意见。\n" + "\n".join(scope.result.draft_paths)
            )
            if scope.state.pending_draft and scope.state.pending_draft.get("decision"):
                scope.result.response = (
                    "生成期间文件有手动修改，draft 已保留。"
                    "输入 1 重新生成、2 保留手动稿、3 退出确认。\n"
                    + "\n".join(scope.result.draft_paths)
                )
        scope.state.remember("user", prompt)
        if scope.result.display_refs:
            refs = ", ".join(
                f"{ref.artifact_id}@{ref.revision}" for ref in scope.result.display_refs
            )
            scope.state.remember("assistant", f"Displayed working Artifact revisions: {refs}")
        else:
            scope.state.remember("assistant", scope.result.response)
        for ref in scope.result.display_refs:
            if not (scope.state.pending_draft and scope.state.pending_draft.get("decision")):
                scope.state.display(ref)
        scope.store.save(scope.session.session_id, scope.state)
        scope.result.skill_refs.extend(scope.used_refs())
        if scope.selected_skill or (scope.ignore_skills and scope.session.next_skill):
            scope.agent.sessions.update_settings(
                scope.project, scope.session.session_id, next_skill=None
            )
        scope.emit("Completed", f"{len(scope.result.tool_calls)} tools")
        return scope.result
