"""Build Project tools with authorization enforced at execution time."""

from __future__ import annotations

import re
from uuid import UUID, uuid4, uuid5

from langchain_core.tools import tool

from novel_agent.agent.freeform_policy import (
    _has_commit_intent,
)
from novel_agent.agent.freeform_types import FreeformRunScope
from novel_agent.domain.artifact import ArtifactRef, ArtifactType
from novel_agent.domain.errors import (
    ArtifactNotFound,
    DecisionRequired,
    InvalidArtifact,
    InvalidChange,
)
from novel_agent.services.draft import SECTIONS

_SECTION_TYPE = {
    "summary": ArtifactType.SUMMARY,
    "outline": ArtifactType.OUTLINE,
    "manuscript": ArtifactType.MANUSCRIPT,
}


class FreeformToolFactory:
    def __init__(self, scope: FreeformRunScope) -> None:
        self.scope = scope

    def project_overview(self) -> str:
        """读取 Project 说明，以及按顺序排列的章节 ID 和标题。"""
        self.scope.trace("project_overview")
        snapshot = self.scope.agent.projects.read_snapshot(self.scope.project)
        fresh = snapshot.project
        lines = [f"Project：{fresh.metadata.name}", snapshot.project_text]
        lines.extend(
            f"{i}. {snapshot.chapter(chapter_id).title} | chapter_id={chapter_id}"
            for i, chapter_id in enumerate(fresh.metadata.chapter_order, 1)
        )
        return "\n".join(lines)

    def read_chapter(self, chapter_id: str, section: str = "manuscript") -> str:
        """按 UUID 读取正式章节的指定部分：manuscript、outline 或 summary。"""
        self.scope.trace("read_chapter", section)
        try:
            if section not in SECTIONS:
                raise ValueError("Unknown chapter section")
            snapshot = self.scope.agent.projects.read_snapshot(self.scope.project)
            fresh = snapshot.project
            text = getattr(snapshot.chapter(UUID(chapter_id)), section)
            entry = fresh.metadata.chapters[str(UUID(chapter_id))]
            return f"来源：chapters/{entry.filename}#{section}\n{text}"
        except (LookupError, ValueError) as exc:
            return f"读取失败：{exc}。请用 project_overview 查询有效的章节 ID。"

    def search_project(self, needle: str) -> str:
        """搜索正式章节、Project 说明、参考资料和来源文件。"""
        self.scope.trace("search_project", needle[:40])
        hits = self.scope.agent.projects.search(self.scope.project, needle)
        return (
            "\n".join(f"{hit.path}#{hit.section}:{hit.line}: {hit.text}" for hit in hits[:25])
            or "没有匹配结果"
        )

    def read_project_file(self, path: str) -> str:
        """读取 Project 内已有的 .md/.txt 文件；不能读取目录。"""
        self.scope.trace("read_project_file", path)
        try:
            file = self.scope.agent.projects.resolve_attachment(self.scope.project, path)
        except (OSError, ValueError) as exc:
            return f"读取失败：{exc}。请提供 Project 内已有的 .md/.txt 文件路径，而非目录。"
        text = file.read_text(encoding="utf-8")
        if len(text) > 32_000:
            raise InvalidChange("文件超过 32,000 字符；请搜索较短的片段")
        return f"来源：{file.relative_to(self.scope.project.root).as_posix()}\n{text}"

    def show_working_artifact(self, artifact_id: str, revision: int) -> str:
        """读取指定的工作 Artifact 修订版本，以便修改或 Commit。"""
        self.scope.trace("show_working_artifact", artifact_id)
        try:
            ref = self.scope.agent.resolve_ref(
                self.scope.project, self.scope.session, f"{artifact_id}@{revision}"
            )
        except (ArtifactNotFound, InvalidArtifact, ValueError) as exc:
            return f"Artifact 读取失败：{exc}。请核对已有修订版本号。"
        artifact = self.scope.agent.artifacts.get(self.scope.project, ref)
        self.scope.offer_display(ref)
        return (
            f"Artifact {artifact.artifact_id}@{artifact.revision}；"
            f"type={artifact.artifact_type}；target={artifact.chapter_id}/{artifact.section}\n"
            f"{artifact.content}"
        )

    def create_working_artifact(
        self, content: str, section: str, chapter: str, title: str = ""
    ) -> str:
        """保存未 Commit 的草稿。section 可选 summary/outline/manuscript/project/analysis；
        chapter 可填章节 UUID、new 或 project。新章节必须提供 title；普通聊天不要调用。
        """
        self.scope.trace("create_working_artifact", section)
        if not content.strip():
            raise InvalidArtifact("Artifact content cannot be empty")
        if section == "analysis" and chapter == "project":
            kind, scope, chapter_id, target = ArtifactType.ANALYSIS, "PROJECT", None, None
        elif section == "project" and chapter == "project":
            kind, scope, chapter_id, target = ArtifactType.SUMMARY, "PROJECT", None, None
        elif section in _SECTION_TYPE:
            kind, scope, target = _SECTION_TYPE[section], "SECTION", section
            if chapter == "new":
                if not title.strip():
                    raise InvalidArtifact("New Chapter requires a title")
                chapter_id = uuid4()
                self.scope.state.draft_titles[str(chapter_id)] = title.strip()
                self.scope.store.save(self.scope.session.session_id, self.scope.state)
            else:
                chapter_id = UUID(chapter)
                if (
                    chapter_id
                    not in self.scope.agent.projects.open_project(
                        self.scope.project.root
                    ).metadata.chapter_order
                    and str(chapter_id) not in self.scope.state.draft_titles
                ):
                    raise InvalidArtifact("Chapter does not exist")
        else:
            raise InvalidArtifact("Unknown Artifact section or Chapter")
        dependencies = ()
        if chapter_id and section in SECTIONS:
            task_id = uuid5(self.scope.session.session_id, f"freeform:{chapter_id}")
            record = self.scope.agent.drafts.record(
                self.scope.project, self.scope.session.session_id, task_id, chapter_id
            )
            if record:
                dependencies = tuple(
                    ArtifactRef.model_validate(record["refs"][key])
                    for key in SECTIONS[: SECTIONS.index(section)]
                    if key in record["refs"]
                )
        artifact = self.scope.agent.artifacts.create(
            self.scope.project,
            self.scope.session.session_id,
            kind,
            scope,
            content,
            chapter_id=chapter_id,
            section=target,
            dependency_paths=self.scope.task.attachment_paths,
            dependencies=dependencies,
            skill_refs=self.scope.used_refs(),
        )
        self.scope.result.artifacts.append(artifact.ref)
        self.scope.offer_display(artifact.ref)
        path = self.scope.agent._export_freeform_draft(
            self.scope.project, self.scope.session, self.scope.state, artifact.ref
        )
        if path:
            self.scope.store.save(self.scope.session.session_id, self.scope.state)
            self.scope.result.draft_paths.append(path)
            return (
                f"已保存 Artifact {artifact.artifact_id}@1。工作稿文件：{path}；无需重复输出全文。"
            )
        return (
            f"已创建 Artifact {artifact.artifact_id}@1，type={kind.value}，"
            f"chapter_id={chapter_id}，section={section}。请向用户展示此修订版本的完整原文。"
            f"正式 Project 文件尚未改变。\n{content}"
        )

    def revise_working_artifact(self, artifact_id: str, revision: int, content: str) -> str:
        """修订已有 Artifact。revision 是现有的基础版本号，不是期望的新版本号；
        调用后会创建一个不可变的新修订版本。
        """
        self.scope.trace("revise_working_artifact", artifact_id)
        try:
            ref = self.scope.agent.resolve_ref(
                self.scope.project, self.scope.session, f"{artifact_id}@{revision}"
            )
            old = self.scope.agent.artifacts.get(self.scope.project, ref)
            if old.session_id != self.scope.session.session_id:
                raise InvalidArtifact("Artifact belongs to another Session")
            updated = self.scope.agent.artifacts.revise(
                self.scope.project, ref, content, skill_refs=self.scope.used_refs()
            )
        except (ArtifactNotFound, InvalidArtifact, ValueError) as exc:
            return f"修订失败：{exc}。请传入现有的基础版本号（首次修订通常为 1），然后重试。"
        self.scope.result.artifacts.append(updated.ref)
        self.scope.offer_display(updated.ref)
        path = self.scope.agent._export_freeform_draft(
            self.scope.project, self.scope.session, self.scope.state, updated.ref
        )
        if path:
            self.scope.store.save(self.scope.session.session_id, self.scope.state)
            self.scope.result.draft_paths.append(path)
            return (
                f"已保存 Artifact {updated.artifact_id}@{updated.revision}。"
                f"工作稿文件：{path}；无需重复输出全文。"
            )
        return (
            f"已创建 Artifact {updated.artifact_id}@{updated.revision}；"
            f"旧版本仍可读取。请向用户展示此版本的完整原文。\n{content}"
        )

    def commit_working_artifact(self, artifact_id: str, revision: int) -> str:
        """仅在用户明确要求 Commit 时，提交已经展示过的指定 Artifact 修订版本。"""
        self.scope.trace("commit_working_artifact", artifact_id)
        if not _has_commit_intent(self.scope.task.prompt):
            raise InvalidChange("Current user message has no explicit Commit intent")
        self.scope.emit("Committing", f"{artifact_id}@{revision}")
        selectors = (f"{artifact_id}@{revision}",)
        if (
            self.scope.state.current_ref
            and str(self.scope.state.current_ref.artifact_id) == artifact_id
            and not (re.search(r"@\d+", self.scope.task.prompt))
        ):
            selectors = ()
        commit = self.scope.agent.commit_visible(self.scope.project, self.scope.session, selectors)
        self.scope.state.pending_draft = self.scope.store.load(
            self.scope.session.session_id
        ).pending_draft
        self.scope.result.commits.append(commit)
        return f"Commit {commit.commit_id} 已完成；changed_files={list(commit.changed_files)}"

    def delete_formal_chapter(self, chapter_id: str) -> str:
        """仅当用户明确要求删除整章时，才按 UUID 通过 Commit 删除一个正式章节。
        不得通过虚拟文件系统删除 Project 文件。
        """
        self.scope.trace("delete_formal_chapter", chapter_id)
        if self.scope.delete_target is None:
            return "拒绝删除：用户当前消息没有要求删除整个章节。"
        try:
            selected = UUID(chapter_id)
        except ValueError:
            return f"章节 ID 无效。用户要求的章节 ID 是 {self.scope.delete_target}。"
        if selected != self.scope.delete_target:
            return f"目标不匹配。用户要求的章节 ID 是 {self.scope.delete_target}。"
        if self.scope.delete_report is not None:
            return self.scope.delete_report
        self.scope.emit("Committing", f"删除章节 {selected}")
        try:
            commit, title, filename = self.scope.agent.commit_chapter_deletion(
                self.scope.project, selected, self.scope.delete_baselines
            )
        except DecisionRequired as exc:
            self.scope.delete_blocker = str(exc)
            return f"删除已停止：{exc}。没有删除任何章节。"
        self.scope.result.commits.append(commit)
        self.scope.delete_report = (
            f"已删除正式章节《{title}》（chapter_id={selected}，原文件 "
            f"chapters/{filename}）。Commit {commit.commit_id} 已完成；"
            f"变更文件：{', '.join(commit.changed_files)}。其他章节未自动改写。"
        )
        return self.scope.delete_report

    def build(self):
        names = (
            "project_overview",
            "read_chapter",
            "search_project",
            "read_project_file",
            "show_working_artifact",
            "create_working_artifact",
            "revise_working_artifact",
            "commit_working_artifact",
            "delete_formal_chapter",
        )
        return [tool(getattr(self, name)) for name in names]
