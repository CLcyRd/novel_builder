# 写作资料与内置 Skill

本目录保留七份来源资料，不作为运行时提示词直接加载。经过冲突修正的方法版本位于 `src/novel_agent/builtin_skills/`；每项由短 `SKILL.md`、`references/examples.md` 和 `references/sources.md` 组成。后者记录来源、改编及已有许可声明，不推定其他文件的许可。

| 来源文件 | 内置 Skill | Workflow 阶段 |
| --- | --- | --- |
| living-voice.md、universal (1).md | writing-foundations | outline、manuscript、review |
| universal.md | viewpoint-control | outline、manuscript、review |
| universal (2).md | environment-writing | outline、manuscript |
| universal (3).md | dialogue-writing | outline、manuscript、review |
| villain-types.md | antagonist-design | summary、outline、review |
| common-rules.md | prose-style-review | review，仅手动选择 |

前五项为自动候选，是否应用由现有 Resolver 及阶段范围决定；没有“始终加载”。所有 Skill 默认可供 Freeform 按需读取，支持已有 `/skill use`、`/skill disable` 命令。每个 Skill 的正文和小型示例适配现有 Workflow 注入预算，没有增加检索服务、Agent 或 CLI 命令。

## 统一后的冲突处理

- **视角**：限定叙事才限制当前人物未知信息。用户选择全知或多视角时允许对应叙述，物理转场不等于自动进入他人内心。
- **禁词与数量**：撤销 common-rules 中零容忍与允许保留之间的冲突，以及数量表中的所有硬阈值。按语境、局部重复、整体节奏提出软建议，不据此判定 AI 来源或拒绝 Commit。
- **动作与感官**：不固定每一两句对白插动作，不要求每段三维齐全或固定感官数量。插入细节须有叙事作用，允许直接情绪、概述、比喻和连续对白。
- **人物语言**：身份只是参考，允许直接表达诉求、争吵与情绪变化，避免身份固定话术。
- **反派**：三种模型是选项，允许有依据的错误、矛盾、真诚善意与不同胜利方式，不强制万能布局或惨烈牺牲。
- **原项目组件**：不要求调用 prompt-crafter，不依赖缺失的 chapter-quality-checklist.md 或 character-setting-style.md。
- **产品边界**：资料不是小说事实，不改变大纲结构、Gate、Auto 或 Commit 权限。审阅只出报告，明确修订请求才生成 Artifact，不自动重写手动稿。

新任务使用整理版 Skill；来源资料保留供追溯，其中的绝对化口吻或冲突规则不作为应用要求。用户明确要求优先于方法建议，正式正文事实优先于大纲、概述和参考资料。

## 验证（2026-10-01）

全量确定性回归 152 项通过；随后补充了自动标签选择测试，Skill 专项 11 项通过。静态检查通过。专项覆盖六项发现、快照及支持文件、阶段范围、自动选择和双 Skill 完整注入预算。

真实模型 `deepseek-flash` 的 2 项测试通过（45.08 秒）：Freeform 实际读取内置对话 Skill 并保存 Artifact，Workflow 按阶段使用视角 Skill、重开 Session 后恢复冻结引用、最后 Commit 正确正文。测试使用临时 Project，不断言具体文学句子。

```powershell
.venv\Scripts\python -m pytest tests/unit tests/integration -q
.venv\Scripts\python -m pytest tests/unit/test_builtin_writing_skills.py -q
$env:RUN_REAL_LLM_TESTS = '1'
.venv\Scripts\python -m pytest tests/real_llm/test_builtin_writing_skills.py -m real_llm -q -s
```
