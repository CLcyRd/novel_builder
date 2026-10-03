# Phase 4 gate：Main Agent × Workflow × CLI

Main Agent 在 Agent 层判断 Freeform/Workflow、创作类型和明确用户覆盖；CLI 只解析命令、附件及展示运行状态。Workflow Request 从 Main Agent 交给对应子图；Workflow Result、Commit Result 和改动文件回到 Main Agent，并以小型控制信息保存在 Session。等待 Approval/Decision 时普通问题仍由 Freeform Main Agent 回答，不触碰原 Gate。CLI 支持 `y/n/e`、自然语言修订、Decision 选择、`/workflow status|exit`、`/auto`、Session reopen 和暂停状态切换模型。

确定性联调验证 Gate 绑定、普通聊天与修订区分、拒绝后新修订、Auto 只通过 Approval、Decision 必须明确选择、附件作为数据、退出后 Session 可用、任务依赖和模型切换后的 Artifact hash；完整确定性回归 78 项通过。真实模型端到端使用 `.env` 的 `deepseek-flash`，暂停后模型切换场景还实际调用已配置的 `grok`。六组组合场景覆盖计划 E01–E11：

| 场景 | 验证证据 |
|---|---|
| E01 | Freeform 大纲、revision 2、正文 Artifact、精确版本 Commit |
| E02/E05/E07/E10 | 续写人工双 Gate；等待时 Project 问答；重开 Session 恢复相同 gate_id；暂停时切换模型，既有 Artifact hash 不变 |
| E03/E04/E11 | Auto Approval 下 Project freshness 冲突仍停 Decision；明确选择后重新生成并 Commit，审批来源为 AUTO |
| E06 | Exit 保留未提交 Artifact，正式 Project 不变，Session 可继续聊天 |
| E08 | Project 内附件进入 Agent/Workflow，候选稿经 Approval 后 Commit，附件中的 `/command` 不执行 |
| E09 | 补写读取后章约束，提交后章节顺序正确 |

```powershell
uv run --frozen --extra agent --extra cli pytest -q
uv run --frozen --extra agent --extra cli ruff check src tests
$env:RUN_REAL_LLM_TESTS = "1"
uv run --frozen --extra agent --extra cli pytest tests/real_llm/test_phase4_e2e.py -q -s
```

真实模型结果：6 个组合用例通过，涵盖全部 11 项行为；断言结构、事件、工具/Artifact/Commit 和文件变化，不断言具体生成措辞。尚无 Web/桌面客户端或 OCR。
