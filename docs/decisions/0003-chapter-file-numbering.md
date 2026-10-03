# ADR 0003：章节文件名与当前章节顺序对齐

状态：已实施。此决策替代 [ADR 0002](0002-commit-and-recovery.md) 中“新文件名单调分配、插入和移动不重命名”的规则。

用户用“第二章”指向 Project 的第二个正式章节。章节文件名现在按当前 `chapter_order` 排列：第一个章节为 `chapters/0001.md`，第二个为 `chapters/0002.md`，依此类推。`chapter_id` 始终保持稳定，文件名仅是当前顺序的投影。

创建、插入、删除、移动、合并或拆分章节时，CommitService 在同一持久事务中写入新的章节文件、删除不再使用的旧路径、更新 `.novel-agent/project.json`，并重建 `MANUSCRIPT.md`。已有旧格式 Project 可继续打开；其下一次章节结构 Commit 会统一文件名。仅修改正文、概述或大纲时不会触发重编号。

重编号会使引用旧章节路径的未提交 Artifact、附件或外部资料可能过期；Commit 的 freshness 检查继续阻止旧 Artifact 静默覆盖新状态。自动更新这些引用不属于本次变更。
