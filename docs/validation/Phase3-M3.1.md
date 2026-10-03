# M3.1 单章 Workflow 验证记录

M3.1 实现 `CREATE_CHAPTER` LangGraph 子图：Prepare Context → 概述/大纲 Artifact → Approval → 正文 Artifact → 只读 Review Artifact → Approval → Commit → Workflow Result。`WorkflowRequest` 携带 Project/Session/Task/目标、约束、附件、工作稿引用与 Auto Approval 设置。状态只保存这些小型控制信息和 Artifact 引用；章节内容存于 Artifact Store。正式章节和 `MANUSCRIPT.md` 仅由现有 CommitService 写入。

两个 Approval Gate 均支持批准、带反馈修订、拒绝及退出。Auto Approval 可推进 Approval Gate，但 freshness 冲突产生的 Decision Gate 始终等待明确选择（重新生成或退出）。Gate 使用稳定 ID，错误 ID 不会恢复图。每个节点使用同步 SQLite checkpoint；重建服务后可从 Session 的活动 Workflow ID 找回 Gate 和 Artifact 引用。Commit 使用稳定 ID，重复执行沿用 CommitService 的幂等结果。

验证命令与结果：

```powershell
uv run pytest -q
# 58 passed
$env:RUN_REAL_LLM_TESTS = "1"
uv run pytest tests/real_llm/test_m3_1_create_chapter.py -q -s
# 3 passed；model=deepseek-flash
```

确定性测试还在独立 Python 子进程中恢复等待中的 Gate 并继续到第二次审批。真实模型场景覆盖人工两次审批、计划反馈修订、重建 Workflow 服务后的等待状态恢复与 Commit；Auto Approval 下的正式 Project 并发修改触发 Decision Gate，选择重新生成后提交；以及 Exit 后正式 Project 无新增章节而工作 Artifact 保留。断言结构、Gate、Commit 和文件变化，不断言具体文学措辞。

M3.1 只提供应用服务 API。其余五类 Workflow 是 M3.2；Main Agent handoff、CLI Gate/状态命令、等待期间普通聊天属于 M4。当前 `/mode workflow` 尚不能从 CLI 调用此子图。
