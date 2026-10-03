# Phase 1（M1.2–M1.4）验证记录

日期：2026-09-26。Project 层实现及阶段 gate 已通过；尚未开始 Main Agent、交互 CLI 或创作 Workflow。

## 交付

- ArtifactStore：Project 内持久化内容、精确 revision、依赖摘要、状态与旧版查阅。创建/修订的多文件元数据可在中断后恢复。
- CommitService：受控直接 Change 与 ArtifactRef 输入、目标/类型验证、精确版本内容提交、SHA-256 freshness、结构化 `DecisionRequired`、批量变更、项目写锁、事务日志、幂等 Commit ID 及向前恢复。
- 确定性操作：章节新增/插入/删除/移动/改标题/相邻合并/锚点拆分；Section 设置和锚点替换/插入/删除；Project 文本设置；搜索、正文字符统计、MANUSCRIPT 派生视图重建。
- SessionService：同 Project 多 Session 的元数据创建、读取及 Mode/Auto/模型选择字段持久化；新 Session 不继承旧 Session 临时设置。
- ModelGateway：从 `.env` 加载 DeepSeek/Grok 聊天模型定义、提前报告缺失配置、按别名调用，未引入 Agent 业务逻辑。
- 决策依据与事务边界见 [ADR 0002](../decisions/0002-commit-and-recovery.md)。

## Deterministic 与 integration

```text
uv lock --check                                           PASS；73 packages resolved
uv run --frozen pytest tests/unit tests/integration -q    PASS；41 passed
uv run --frozen ruff check src tests                      PASS
uv run --frozen ruff format --check src tests             PASS
```

测试均使用临时 Project；现有 `docs/输入文件案例` 与用户工作目录未被改动。关键断言包括：

- Artifact 创建/批准/修订不修改正式章节；r1 展示后即使生成 r2，明确提交 r1 仍写入 r1 的逐字内容。
- 同一 Commit 可提交多个 Artifact 与多个文件；Artifact status、Project metadata、章节和 MANUSCRIPT 同属恢复事务。
- 手动编辑正式章节且还原原 mtime 后，旧 Artifact 与已准备 Commit 均检测到内容变化，不覆盖。
- 不相关章节的修改不会阻止目标章节的 Outline Artifact 提交；辅助分析 Artifact 不能作为正式 Project 文本提交。
- 重复 Section 修改与多匹配文本锚点在写入前拒绝；取消未应用的 Commit 不影响 Project。
- 模拟写入途中异常、新子进程硬退出后重开、Artifact 修订索引中断均可恢复；外部改动造成无法判定的文件状态会阻止恢复。
- 相同 Commit ID 重试不重复插章；不同 Change 指纹不能复用 ID；章节结构变化后章节顺序与 MANUSCRIPT 一致。
- 搜索结果按正文、大纲、概述、Project/参考文件顺序，字符统计口径可复现。

## Real-model Phase 1 gate

```text
uv run --frozen pytest tests/real_llm/test_phase1_connectivity.py -m real_llm -s -q
PASS：2 passed
provider=deepseek model=deepseek-flash
provider=grok model=grok-4.7
```

每个用例从 `.env` 读取配置，创建临时 Project，经 CommitService 写入小型合成章节，再通过 ProjectService 读取**已提交正文**并交给 ModelGateway。两次真实调用返回非空内容与模型标识；调用前后正式文件摘要完全相同。测试不断言生成文句，也不打印 key。Embedding 没有调用，因为 Phase 1 只要求文本模型连通性。

## 阶段边界

本阶段提供 Python API 与测试入口。Deep Agents 主体、用户命令、Markdown 流式 CLI、LangGraph Workflow、Approval/Decision Gate 的用户交互及长篇上下文优化属于 Phase 2–4。当前 `DecisionRequired` 是结构化异常，未来由 Agent/Workflow 映射为待用户选择的 Gate。完整 Project 打开会校验所有登记章节；长篇 Project 的读取性能需在 Phase 2 的上下文构建场景中测量并按证据优化。
