# Workflow Skill Resolver 验证

本次变更引入 `activation: auto` 元数据、Workflow Skill Resolver、冻结候选和按章节/阶段注入。未标记的内置与 Project Skill 保持手动激活；Project Skill 仍需在 Session 启用。`/skill use` 优先于自动候选。

验证覆盖：

- 元数据解析及非法配置拒绝；Project Skill 启用策略、Workflow 类型与阶段过滤。
- 自动 Skill 只用于匹配章节；显式 Skill 排除未请求的自动 Skill；模型选择结果不能越过候选白名单或章节范围。
- Workflow 暂停期间修改源 Skill，恢复后继续使用内容寻址快照；已批准大纲可激活冻结候选用于后续正文。
- Skill 注入上下文限制为 3,000 字符，仅读取列明的 `references/` 文件；Artifact 记录实际使用的 SkillRef。
- 真实模型 `deepseek-flash`：基于描述选中相关自动 Skill 并排除不相关 Skill；真实概述/大纲生成抵达 Approval Gate，Artifact 具备正确 SkillRef，正式 Project 未写入。

结果：确定性/集成测试 **117 passed**；Ruff 通过；新增真实模型测试 **2 passed**。既有 Skill 真实模型回归中，手动选用与恢复、多章导入、续写三项通过；“当前指令不要使用 Skill”一项首次暴露 Freeform 仍注入已手选 Skill，加入显式覆盖规则后该项单独重跑通过。

运行命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q
$env:RUN_REAL_LLM_TESTS='1'
.venv/Scripts/python.exe -m pytest tests/real_llm/test_workflow_skill_auto.py -q -s
.venv/Scripts/ruff.exe check src/novel_agent tests
```
