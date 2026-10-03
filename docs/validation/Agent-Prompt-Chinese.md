# Agent 提示词中文化验证

Freeform Main Agent 的系统提示词、只读 `project_analyst` 的说明、Project/Artifact/Commit 工具描述及返回给模型的操作提示已改为中文。工具名、参数名、Artifact 类型、章节 section 和 JSON 字段保持原有接口标识；Workflow 生成器、意图规划器及 Skill 选择器原本已使用中文指令。

验证结果：

- 定向确定性测试：`tests/unit/test_outline_guidance.py` 与 `tests/integration/test_skill_runtime.py`，6 passed。
- Ruff：`ruff check src/novel_agent tests`，通过。
- 真实模型 `deepseek-flash`：普通聊天、Project 查询、Artifact 生成/修订/Commit、只读子 Agent 分析，3 passed。

完整确定性测试目前为 102 passed、15 failed。失败均发生在 Workflow 路由调用 `WorkflowSkillResolver` 时：仓库内置 Skill 的 `activation: auto` 使测试中的假模型被用于在线 Skill 选择，随后连接 `example.invalid` 失败。该问题与本次 Freeform 提示词翻译无关，需单独修复测试隔离或 Skill 选择策略。
