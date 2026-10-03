# ADR 0002：Phase 1 Artifact、Commit 与故障恢复

状态：M1.2–M1.4 已实施，2026-09-26。依据：需求 V2 §6、§18–22，Agent 设计 §9–12、§26–28，以及 [ADR 0001](0001-project-format.md)。

## Artifact

`.novel-agent/drafts/<artifact_id>/revisions/<N>.json` 保存用户可见的正文、类型、scope、target、依赖 Artifact 引用及正式文件内容摘要。revision 内容创建后不覆盖。对应 `statuses/<N>.json` 单独保存 CANDIDATE、APPROVED、SUPERSEDED、DISCARDED、COMMITTED 等状态。`index.json` 指向最新 revision；旧 revision 在新 revision 产生后仍可通过精确 `ArtifactRef(id, revision, content_hash)` 查阅或显式提交。Artifact 创建和修订的多文件元数据也走同一恢复事务，防止中断后 index 与状态不一致。Approval 只改变状态，不写正式内容。

Artifact 创建时为目标章节及额外读取的 Project 文件记录 SHA-256；新章节目标记录 Project 章节索引摘要。Commit 前复核这些摘要。只改动无关章节不会使旧章节 Outline Artifact 过期。辅助分析、上下文摘要和 Review Artifact 不作为正式章节/Project 文本提交。

## Change 与正式写入

所有正式写入统一经 CommitService。Change API 支持创建、删除、移动、改标题、合并相邻章、以唯一文本锚点拆章，Section 整体设置、替换/插入/删除文本，以及 PROJECT.md 文本更新。直接编辑 Change 由未来 Main Agent 在识别用户明确授权后调用；CLI 不处理小说业务判断。Section 的 Artifact 来源会验证类型、章节、精确 revision 与状态。

章节保持稳定 UUID。以下关于文件名的历史决策已由 [ADR 0003](0003-chapter-file-numbering.md) 替代：插入和移动只改 `chapter_order`、既有文件不重命名、新文件名按 `next_chapter_number` 单调分配。位置参数采用从 0 开始的最终顺序索引，未来 CLI 可转换为用户可读的章序。删除、合并和移动不自动改写其他章节剧情。合并当前仅支持相邻章节；若需求扩展跨章合并，应有单独业务决策。

正文、标题或章节顺序改变时，Commit 在同一次事务中重建 `MANUSCRIPT.md`。聚合只取正式章节标题与正文，不包含概述、大纲、Working Artifact 或运行事件。`count_characters` 统计正文所有非换行字符，包括空格、中文标点、英文字母和数字。

## 多文件事务

以下记录原始布局。当前 pending、receipt、迁移和恢复副本回收规则见 [ADR 0004](0004-transaction-retention.md)；恢复校验及正式 Commit 幂等语义继续有效。

Commit 有稳定 UUID 与 Change 指纹。`prepare` 捕获目标/依赖摘要；`apply` 在 Project 写锁内重新检查并渲染所有结果。冲突返回 `DecisionRequired`，不覆盖。操作状态为 PREPARED、VALIDATING、APPLYING、COMPLETED；未落盘的准备结果可标记 FAILED 或 CANCELLED。已经开始应用的中断由持久事务日志表示，恢复完成后为 COMPLETED。

事务先把全部目标字节放入 `.novel-agent/commits/.staging-*`，再原子改名为 `<commit_id>/`，形成含 before/after 摘要及结果的 manifest。每个目标用同目录临时文件加 `os.replace` 更新。写入 Project metadata、章节、MANUSCRIPT 和 Artifact status 都属于同一事务。重开 Project 前持有相同写锁，检查当前文件仅处于预期的 before/after 状态，再向前完成剩余写入。相同 Commit ID 与同一 Change 指纹重复提交只返回原结果；不同指纹报错。未形成 manifest 的 `.staging-*` 不视作已准备 Commit。

锁保护本应用的读写及恢复，使本应用不会读取到多文件事务中的混合状态。外部编辑器不受应用锁约束；在应用每个文件前再次检查摘要。若发现非预期外部修改，恢复停止并报告 `RecoveryConflict`，保留文件供用户处理。普通本地文件系统不能保证外部进程对多个文件看到强原子快照；这不是多用户协同事务。临时 staged 文件仅用于崩溃恢复，不提供历史版本管理或自动撤销已完成 Commit。

## 测试门槛

确定性测试覆盖旧 revision 精确提交、手工修改且还原 mtime 后的 freshness、无关章节变化、重复 Change、章节结构、正文派生、Artifact 状态、幂等 ID、模拟不同位置中断、外部冲突，以及子进程硬退出后新进程恢复。真实模型测试经 ProjectService 读取已 Commit 章节，再由 ModelGateway 调用配置模型，并核对正式文件摘要未变。
