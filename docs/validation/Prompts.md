# 提示词集中与阶段生成验证

验证日期：2026-10-01。

## 分层与范围

新增 `src/novel_agent/prompts/prompt.py`，作为 Agent 与 Workflow 共用的提示组织层，不负责网络调用、Project 读取、Artifact 保存或 Commit。Main Agent、只读分析、意图规划、Skill 选择、阶段要求和公共上下文模板在此维护。Tool 的描述仍与工具接口放在一起，Skill 的写作方法仍以独立 `SKILL.md` 资源提供；框架自带提示不复制进应用代码。

`workflow/generator.py` 负责调用模型并识别明确的 `NEEDS_DECISION` 协议信号；单章及多章 Workflow 负责持久化 Gate 和恢复。`ContextBlock` / `GenerationContext` 只用于本次调用的上下文组装，checkpoint 保持 Artifact 引用，不保存组装后的整篇小说上下文。旧的字符串生成器接口与 `domain/outline_structure.py` 导入路径保留兼容。

## 行为

- 用户指令决定创作和修改范围，正式事实仍按正文、大纲、概述、普通参考资料排序；授权的新创作可提出候选，不捏造既有事实。
- 概述、大纲、正文、审阅是 Workflow 阶段，不是四个长期用户 Agent。正文依据最新确认大纲，包括手动编辑；审阅只给报告与局部建议，不批准、重写或提交。
- 大纲保留原有栏目，增加本章目标与冲突、连续性检查及场景阻碍；有相关前章正式正文才写前情提要。不新增大纲格式校验器。
- 上下文带来源、真实状态、Artifact ID、revision 和截断标记。关键当前材料、重写目标全文、逐章要求和已选 Skill 不静默截断；次要材料按阶段与事实层级分配余量。预算在 `config/setting.py` 的 `generation_context_max_chars`，默认 18,000 个内容字符，不是 token 上限。
- 关键材料超预算，或模型明确返回必要条件缺失/冲突的协议信号，进入持久化 Decision。Auto 不代选，`y` 与普通问答不恢复它。用户可重试、补充明确条件或退出，不新增 CLI 命令。
- Decision 中的明确条件持续影响后续阶段。单章及批量重开恢复、已生成章节保留、审阅重试不重生成正文、等待期间手动编辑同步均有测试。
- 此次未新增文学质量、栏目完整性或任意自然语言冲突报告的语义检测器；协议之外的文学输出仍由用户和现有审阅流程确认。

## 确定性测试

```powershell
.venv\Scripts\python -m pytest tests/unit tests/integration -q
.venv\Scripts\ruff.exe check src tests
```

最终全量 **175 项通过**（61.15 秒），Ruff 通过。

新增测试覆盖保护手动正文与重写基稿、参考资料不挤掉关键上下文、来源与状态、数据标签转义、旧字符串兼容、完整 Skill 保留、超预算前禁止模型调用、模型决策输出不进入 Artifact、Auto 下 Decision、Exit、持久化重开、审阅重试、手动编辑同步及后续阶段持续采用明确条件。

## 真实模型测试

配置来自 `.env`，本次实际使用 `deepseek` 别名对应的 `deepseek-flash`。测试均创建临时 Project，验证 Gate、Artifact 引用、工具行为、精确 Commit 与文件状态，不断言文学措辞。

```powershell
$env:RUN_REAL_LLM_TESTS = '1'
.venv\Scripts\python -m pytest tests/real_llm/test_generation_decision.py tests/real_llm/test_editable_drafts.py tests/real_llm/test_builtin_writing_skills.py tests/real_llm/test_m3_2_workflows.py tests/real_llm/test_workflow_intent.py tests/real_llm/test_workflow_skill_auto.py -m real_llm -s -q
```

这次运行的既有 **16 项通过**；新增 Decision 用例发现用户补充条件只影响一次生成、后续大纲又读取旧约束的问题。修复为持久保留当前任务的最新明确决策，随后单独复验：

```powershell
.venv\Scripts\python -m pytest tests/real_llm/test_generation_decision.py -m real_llm -s -q
```

新增用例 **1 项通过**（55.30 秒），证明真实模型在 Auto 下请求 Decision，用户明确补充条件后经重开恢复、继续生成并完成 Commit。上述 17 个用例均已成功验证；并非将首次失败记为通过。

既有用例覆盖 Freeform 生成/工具/手动确认/显式 Commit，Workflow 手动大纲用于正文与精确提交，Skill 使用，续写、补写、重写、多章、导入，以及语义任务规划和自动 Skill 选择。重写目标全文保护调整后再单独复验重写场景，结果见下方最终记录。

```powershell
.venv\Scripts\python -m pytest tests/real_llm/test_m3_2_workflows.py::test_live_rewrite_preserves_chapter_identity -m real_llm -s -q
```

重写复验 **1 项通过**（65.92 秒）。本次无凭证或真实模型验证阻塞。
