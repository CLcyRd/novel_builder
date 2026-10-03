# 小说 Agent Skill 需求与落地方案 V1

## 1. 结论与当前状态

Skill 在本项目中是**可复用的创作方法、风格规则或只读分析指引**，由 Main Agent 或 Workflow 在相关任务中读取；它不是新的长期 Agent、Workflow 状态机、Project 正式事实或写入权限。固定的创作阶段与 Approval/Decision/Resume 仍归 LangGraph Workflow，正式写入仍归 CommitService。

当前实现已具备 Freeform 虚拟 Skill 文件、CLI 管理命令、Workflow 冻结 Skill Context 和 S3.1 自动选择能力。[Freeform Agent](../src/novel_agent/agent/freeform.py) 在每次 run 构建 DeepAgent 时注入已启用 Skill；[Workflow](../src/novel_agent/workflow/create_chapter.py) 按阶段读取冻结版本。早期单文件 Skill 案例若未放入 `<skill-name>/SKILL.md` 目录，仍不会被目录服务发现。

Deep Agents 官方支持通过 `skills=["/skills/"]` 注册源目录，并按“名称/描述 → SKILL.md 正文 → 支持文件”逐层读取。默认 `StateBackend` 需要在本次 invocation 中注入虚拟文件；这适合本项目保留 Project 正式文件隔离的现有设计。[官方 Skills 文档](https://docs.langchain.com/oss/python/deepagents/skills)。

## 2. V1 产品要求

| 能力 | 规则 |
| --- | --- |
| Skill 来源 | 先支持应用内置 Skill 与当前 Project 的 `skills/<name>/SKILL.md`；不自动扫描用户主目录、网络或其他 Project。 |
| 格式 | `SKILL.md` 采用 YAML frontmatter，至少有唯一 `name`、明确的 `description`；可带同目录 `references/` 和 `assets/`。V1 不执行 `scripts/`。 |
| 发现与调用 | 内置 Skill 默认启用，Project Skill 默认仅发现、需显式启用。Main Agent 只看到已启用 Skill 的名称与描述；相关任务由模型按需读取正文。用户可在自然语言中点名已启用 Skill，也可用 CLI 为下一次用户请求指定一个 Skill。 |
| 适用范围 | Freeform 可用创作与分析 Skill；Workflow 可在生成/审阅阶段使用选定 Skill，但 Skill 不改变 Gate、章节目标、Commit 或源文件提取。 |
| 持久化 | Session 保存启用列表与待使用 Skill；Workflow Request 固定本次所用 Skill 的 ID、来源和内容摘要，正文快照存 Project 内部目录，保证重开 Session 后沿用同一版本。 |
| 可观察性 | CLI 能列出、查看、启停 Skill；runtime event 告知本次读取/应用的 Skill，Task/Artifact 记录 Skill ID 与内容摘要，不把 Skill 正文复制到小说 Artifact。 |
| 优先级 | Skill 仅指导写作方法。用户当前明确的写作要求优先于风格 Skill；Project 正文事实优先于大纲、概述和参考资料。Skill 不得推翻正式事实或 Project/Artifact/Commit 不变量。 |
| 安全 | Skill 视为 Project 内可编辑的指令数据，不授予新工具权限。只有既有受控服务可写正式 Project。拒绝路径越界、符号链接/联接、重复名称与异常大小的文件。 |

V1 不包含 Skill 市场、在线安装、跨 Project 自动共享、Skill 自动编写/执行脚本、让 Skill 直接调用 Commit，以及让 Skill 变成 Subagent 或 Workflow。将来若允许脚本，需另行设计沙箱与权限；不把“支持 Skill”直接等同于“可执行任意代码”。

## 3. 存储与加载边界

```text
src/novel_agent/builtin_skills/<name>/SKILL.md  # 随应用发布的只读库
<Project>/skills/<name>/SKILL.md               # Project 本地可移植库
<Project>/.novel-agent/skill-snapshots/<sha256>/...  # Task/Workflow 使用的冻结版本
```

新增 `SkillCatalog/SkillService`，负责扫描、frontmatter 解析、名称冲突、Project-root 路径检查、文件大小限制、启用策略和快照。Project Skill 与同名内置 Skill 冲突时，由 Project 版本覆盖；CLI 必须显示来源与摘要。扫描是确定性服务，不由模型自行遍历任意本地目录。`SkillService` 只读取文件；用户对 Skill 文件的编辑通过系统文件操作完成，V1 不提供 Agent 写 Skill 工具。

Main Agent 构造本次 run 的只读 Skill 文件映射，使用 `create_file_data()` 放入本次 `astream` 输入的 `files`，路径如 `/skills/scene-writing/SKILL.md`；构造 DeepAgent 时传 `skills=["/skills/"]`。不要将 Deep Agents 的文件系统根目录直接设成真实 Project root，否则内建 `write_file/edit_file` 可能绕过 CommitService。对虚拟 `/skills/**` 也配置写入拒绝；已配置的只读 `project_analyst` Subagent 只有明确分配的分析 Skill，不自动获得所有 Skill。官方文档说明自定义 Subagent 不继承 Main Agent 的 Skill，并给出了按路径拒绝写入的方式。[Skill 后端与权限](https://docs.langchain.com/oss/python/deepagents/skills)。

Main Agent 目前每次 run 重建 DeepAgent，没有跨 run 的 Deep Agents checkpointer；Session 记忆由应用自己的 SQLite 状态保存。因此 Freeform 下次 run 可重新读取启用列表。Workflow 有独立持久状态：开始时将本 Task 的 Skill 文件复制为内容寻址快照，Request/State 只保存引用与摘要；暂停、换模型和进程重启后继续使用该快照。已展示的 Artifact 保持原样；Skill 文件之后变化只影响新 Task，不触发对旧 Artifact 的静默重生成。

## 4. 分层改动

| 层 | 计划改动 |
| --- | --- |
| `domain/` | `SkillRef`、来源、摘要、选择语义；明确 Skill 不是正式小说事实或 Artifact。 |
| `services/` | `SkillCatalog/SkillService` 负责加载、启用、快照与冲突处理。 |
| `infrastructure/filesystem/` | 安全扫描与 UTF-8 读取、frontmatter、文件大小/路径校验；应用内置 Skill 通过包资源读取。 |
| `agent/` | 给 `create_deep_agent` 提供虚拟 Skill 源；仅暴露已启用目录，并记录实际读取/使用事件。原有 Project read 与 Commit 工具不变。 |
| `workflow/` | `WorkflowRequest` 持有 SkillRef；各生成阶段通过单独的 Skill Context 获取冻结指引，不把 Skill 文本存入 LangGraph State。导入 Workflow 的原始正文仍逐字来自源文件。 |
| `runtime/` | Session 设置、Task/Artifact 来源记录、Skill 读取事件；中断恢复时解析冻结快照。 |
| `cli/` | `/skill list`、`/skill show <name>`、`/skill enable|disable <name>`、`/skill use <name>`、`/skill clear`。`use` 仅指定下一项 Task，Workflow 开始后绑定该 Task；CLI 只解析命令，不决定写作方法。 |

文件内容中的 `/skill ...` 仍只是数据，不会被 CLI 当命令执行。普通聊天在 Workflow 等待时可使用 Skill，但不会因此确认 Gate 或替换该 Workflow 的冻结 Skill。

## 5. 里程碑与验收

### S1：只读 Skill 目录与 Freeform（最小可交付）

完成内置/Project Skill 发现、安全扫描、CLI 列表与启停、虚拟文件注入、Freeform 自动按需读取与显式选用。将现有示例复制或移动到规范的 `<name>/SKILL.md` 前需单独审查其适用描述；不能仅因为文件带 frontmatter 就自动启用。

实现时把 `pyproject.toml` 的 Deep Agents 版本下限提高到已验证支持本方案的版本，并同步更新 `uv.lock`；当前环境的 0.7.19 可作为兼容性基线。

确定性测试覆盖：目录发现、同名覆盖、禁用、非法 frontmatter、路径越界/链接、文件上限、虚拟文件不可写、无 Skill 时原 Freeform 行为。集成测试确认 Main Agent 可读取指定 Skill、Project 正式章节未被修改、runtime event 和 Artifact 来源引用正确。真实模型测试在临时 Project 使用简短中性写作 Skill，验证模型实际读取 Skill、生成 Artifact、无需 Commit 时正式文件不变；再验证显式用户要求覆盖风格建议。记录模型 ID 与调用轨迹，不断言固定文学句子。

### S2：Workflow Skill Context 与恢复

Workflow Request 冻结选中 Skill，生成器在概述/大纲/正文/审阅阶段按适用范围读取；不改 LangGraph Gate 拓扑。CLI 重开 Session 后能恢复同一 Skill 摘要。导入流程的原文提取和 Commit 内容不受创作 Skill 改写。

确定性测试覆盖：六类 Workflow 的传递、版本快照、暂停后源 Skill 被编辑或删除、Auto Approval 与 Decision Gate、导入正文不变、Commit freshness。真实模型测试至少覆盖单章/续写使用 Skill、暂停重启后继续、多章后续章节与 Skill 一致、导入原文不变。

### S3：体验与扩展（按需要）

评估 Skill 来源筛选、多个 Skill 的冲突提示、只读分析 Subagent 的专用 Skill、`/skill status` 与使用记录。脚本执行、远程安装和共享库另立需求，不纳入前两个里程碑。

#### S3.1：Workflow Skill Resolver（已落地）

Workflow handoff 使用 `WorkflowSkillResolver` 在已启用目录中筛选。Skill 默认 `activation: manual`；只有显式 `activation: auto` 才进入自动候选。可配置适用 Workflow、阶段、标签和明确引用的 `references/` 文件。用户显式 `/skill use` 或点名优先。确定性标签命中时按本次章节目标绑定；证据不足或候选过多时，模型只读取名称、描述、标签并返回候选 ID 与章节下标，结果经白名单校验。

启动时冻结自动候选版本。Workflow 生成节点按阶段与章节加载已绑定 Skill；已确认大纲出现相关标签时，可从冻结候选中激活后续阶段的 Skill。每个 Artifact 只记录实际使用的 SkillRef。注入文本有独立预算，不允许 Skill 挤占整个 Project 上下文；Skill 不改变 Gate、导入原文或 Commit 权限。验证见 [Workflow Skill Resolver](validation/Workflow-Skill-Resolver.md)。

## 6. 实施顺序

S1、S2 和 S3.1 已按上述边界实现。当前 CLI 命令及示例见 [CLI 使用说明](小说Agent_CLI使用说明.md)；验证记录见 [Skill S1–S2](validation/Skill-S1-S2.md) 和 [Workflow Skill Resolver](validation/Workflow-Skill-Resolver.md)。S3 其他体验项仍按需要扩展。
