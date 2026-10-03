# Phase 3 gate：六类 Workflow

Phase 3 合并检查覆盖 Create、Continue、Multi、Backfill、Rewrite、Import 六类 LangGraph 子图。Workflow Request 带稳定 workflow/task/target ID、附件与工作稿引用、可选前置 Task ID；未完成的前置 Task 阻止启动，已完成 Task ID 保存在 Session 控制状态。Approval 支持人工批准、拒绝后重新生成、自然语言反馈修订、退出；每次批准记录 `HUMAN` 或 `AUTO` 来源及精确 Artifact revision。Auto Approval 不选择 Decision；freshness 冲突保留候选稿并等待用户重新生成或退出。Commit 使用稳定 ID，正式文件只由 CommitService 写入。

确定性与集成检查包括六类图迁移、Gate ID 校验、单章和批量审批、11 章导入的 10+1 大纲批次、字数工具检查、Project-local 路径、Commit 冲突及幂等、独立 Python 进程恢复。完整确定性回归 78 项通过。真实模型使用 `.env` 的 `deepseek-flash`；M3.1/M3.2 共 9 个用例通过，覆盖单章人工审批与反馈、Auto、Decision、Exit、续写前章与人物资料、补写后章约束、多章顺序、重写和导入。新增跨进程真实用例还覆盖 `REJECT` 后新 revision 与恢复提交。

```powershell
uv run --frozen --extra agent --extra cli pytest -q
$env:RUN_REAL_LLM_TESTS = "1"
uv run --frozen --extra agent --extra cli pytest tests/real_llm/test_m3_1_create_chapter.py tests/real_llm/test_m3_2_workflows.py -q -s
```

验收以 Gate 状态、Artifact 类型/目标/版本、正式文件变化与恢复状态为准，不比较文学措辞。Workflow 不作为 Subagent；CLI handoff 与等待时聊天在 Phase 4 完成。
