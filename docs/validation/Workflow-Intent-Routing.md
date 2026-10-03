# Workflow 意图规划与分批章节路由验证

本次变更将广义“写一篇小说”等请求交给当前 Session 模型生成结构化创作计划；明确的单章、续写、补写等请求仍可走确定性快捷路径。章数、章节边界、全书总章数与本次范围由代码核对。明确总数和逐章列举不一致时，Session 保存待澄清状态，不启动 Workflow；普通聊天不会作出选择。

验证结果（2026-09-27）：

- `tests/unit` + `tests/integration`：107 项通过。
- Ruff：通过。
- 真实模型：`deepseek-flash`，`tests/real_llm/test_workflow_intent.py` 5 项通过。
- 真实多章入口：三条逐章要求进入 `MULTI_CHAPTER`，生成三个非空概述 Artifact，停在 `SUMMARIES` Approval Gate；正式章节未写入 Project。
- 全书五章、本次前三章：实际只创建三个 Workflow 目标；第四、第五章未提前生成正式文件。

范围边界：全书计划章数在本次 Workflow Request 中作为约束，并不自动成为 Project 正式大纲。若希望后续 Session 以五章总规划为正式事实，需要另外生成、确认并 Commit 规划 Artifact。
