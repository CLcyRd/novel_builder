# 小说 Agent CLI 使用说明

小说 Agent 是本地 Project 驱动的写作工具。Project 保存正式小说内容；Session 保存一次协作的设置、工作稿引用和待处理的 Workflow。你可以先在 Freeform 模式讨论、生成和修改工作稿（Artifact），确认后再 Commit；也可以让 Workflow 分阶段创作并在关键步骤等待审批。

本文按**当前 CLI 已实现的命令**编写。终端中的命令以 `/` 开头；其余输入作为对 Main Agent 的自然语言消息。

## 安装与启动

需要 Python 3.13、`uv`，以及至少一个已配置的真实模型。以下命令在仓库根目录的 PowerShell 中执行：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 编辑 .env：填写 NOVEL_DEFAULT_MODEL，以及所选模型的 MODEL_ID 和 API_KEY。
uv sync --frozen --extra agent --extra cli
uv run --frozen --extra agent --extra cli python -m novel_agent --workspace .\projects --env .\.env
```

例如选择 DeepSeek 时，设置 `NOVEL_DEFAULT_MODEL=deepseek`、`DEEPSEEK_MODEL_ID`、`DEEPSEEK_MODEL_API_KEY`；服务地址可沿用 `.env.example` 的 `DEEPSEEK_MODEL_BASE_URL`。Grok 使用对应的 `GROK_MODEL_*` 变量。**未使用的 provider 要把其 `*_MODEL_BASE_URL` 也留空**；当前读取逻辑会把“仅有服务地址、没有 ID 或密钥”视为配置不完整。不要把 `.env` 或密钥提交到版本库。

`--workspace` 指定 Project 存放目录，`--env` 指定模型配置文件。不传时分别默认为**当前工作目录**下的 `projects/` 和 `.env`。启动后输入 `/help` 可查看简要命令列表。Project 创建成功后会自动建立并选中名为 `Main` 的初始 Session。

## 第一次使用

在 `novel>` 提示符下依次输入：

```text
/project create 雾港
/status
请阅读当前 Project，帮我构思一段悬疑故事的开场，不要提交到正式章节。
/artifact list
```

章节工作稿生成后会显示可编辑 draft 文件路径，默认不在终端重复输出全文。打开文件阅读或局部修改，保存后输入 `y`；也可以输入 `n` 或修改意见让 Agent 更新同一个文件。每次确认会将保存的编辑同步为 Artifact revision，例如 `<artifact-id>@2`。只有 Commit 后，内容才成为 Project 正式文件。`MANUSCRIPT.md` 是从正式章节重建的合并视图，正式章节以 `chapters/` 内文件为准。

## 命令速查

| 命令 | 用途 |
| --- | --- |
| `/help` | 显示简要帮助。 |
| `/project`、`/project list` | 列出 workspace 中的 Project。 |
| `/project create <名称>` | 创建并打开 Project，同时创建初始 Session。 |
| `/project open <名称或路径>` | 打开 Project，并选中其初始 Session。 |
| `/project maintenance` | 恢复/收尾事务、重试遗留清理，显示事务数量、占用、回收字节与维护警告；不调用模型。 |
| `/session`、`/session list` | 列出当前 Project 的 Session 名称和 ID。 |
| `/session create <名称>` | 创建并选中一个新 Session。 |
| `/session open <名称或 ID 前缀>` | 打开唯一匹配的 Session。 |
| `/status` | 查看 Project、Session、模式、Auto Approval、模型、当前工作稿及活动 Workflow。 |
| `/mode`、`/mode freeform`、`/mode workflow` | 查看或设置当前 Session 的执行模式。 |
| `/auto`、`/auto on`、`/auto off` | 查看或切换 Auto Approval。 |
| `/model list`、`/model <别名>` | 列出可用模型或切换当前 Session 后续调用所用模型。 |
| `/skill list`、`/skill show <name>` | 列出当前 Project 可用 Skill 或查看其原文。 |
| `/skill enable|disable <name>` | 在当前 Session 启用或停用一个 Skill。 |
| `/skill use <name>`、`/skill clear` | 为下一次用户请求指定已启用 Skill，或清除指定。 |
| `/artifact`、`/artifact list` | 列出当前 Session 的 Artifact。 |
| `/artifact current` | 查看当前 Freeform 工作稿。 |
| `/artifact show <ID>[@revision]` | 查看指定 Artifact；可用唯一 ID 前缀，`@revision` 指定精确版本。 |
| `/artifact discard <ID>[@revision]` | 放弃指定 Artifact revision。 |
| `/commit current` | 同步当前章节 draft 的已保存编辑并 Commit；draft 已删除时提交已确认的 Artifact。 |
| `/commit [ID@revision ...]` | 把指定工作稿正式写入 Project；省略参数时也使用当前 Freeform 工作稿。 |
| `/workflow status`、`/workflow exit` | 查看或退出活动 Workflow。 |
| `/history` | 查看当前 Session 最近的用户消息与助手最终回复；工作稿显示精确版本引用。 |
| `/clear` | 清空当前终端画面，不删除任何 Session、对话或工作稿。 |
| `/exit` | 退出 CLI；等待中的 Workflow 会保留，供重新打开 Session 后继续。 |

Project 或 Session 名称含空格时可加引号，例如 `/project open "雾港 长篇"`。`/model` 直接接模型别名，例如 `/model grok`；当前版本没有 `/model use` 命令。

### 事务存储维护

旧项目第一次打开时会在 Project 锁内自动迁移到事务布局版本 2，中断后可重开继续。迁移后请继续使用新版程序，旧版恢复器无法处理新布局。

正常读取只检查未完成事务，完成事务的 staged 恢复副本会自动回收。正式 Commit 的轻量凭据长期保留，以保证相同 Commit ID 重试不会重复写入。Artifact 的真实历史修订和章节文件不会被事务维护删除。

文件被占用等原因可能导致清理暂缓，此时已完成的 Commit 仍然有效。打开对应 Project 后执行 `/project maintenance` 可重试清理，并查看待恢复/收尾数、正式 Commit 凭据数、待清理数、遗留暂存项、事务文件占用及本次净回收字节。统计的是文件内容字节，不是磁盘分配空间；命令之前已自动回收的数据不计入本次回收。未完成或冲突的恢复事务不会按年龄删除，恢复冲突需要先处理相关文件。

服务 API 为 `ProjectService.maintain_transactions(project)`，返回 `TransactionMaintenanceReport`。布局、故障边界与兼容说明见 [ADR 0004](decisions/0004-transaction-retention.md)。

## Freeform：讨论、工作稿与 Commit

Freeform 适合构思、问答、局部修改和自由写作。生成的小说内容通常先作为 Artifact 保存。可以用自然语言要求 Agent 修改指定工作稿，也可以明确要求提交；想精确控制提交版本时使用 `/commit`：

```text
/mode freeform
请为第一章起草一份大纲，保存为工作稿，不要提交。
/artifact list
/artifact show <实际 ID>@1
请修改刚才的大纲：加强结尾悬念，仍然不要提交。
/artifact list
/artifact show <实际 ID>@2
/commit <实际 ID>@2
```

上例中的 revision 和 ID 是示意，修改后的实际 ID/revision 以 CLI 输出为准。指定 ID 时可使用 `/commit <ID>@<revision>` 提交展示过的精确版本；若 draft 有尚未同步的手动编辑，指定版本提交会停止，避免把编辑悄悄替换为旧版。`/commit current` 与不带参数的 `/commit` 一样，会先读取当前 draft 的已保存内容，将该章节已有的概述、大纲、正文一起 Commit；draft 已在正文确认时删除，也能提交对应已确认版本。Commit 不会重新生成内容，且会检查 Project 是否已变化。若发生 freshness 冲突，先检查最新正式内容，再生成适配新状态的工作稿。自然语言“把这个工作稿正式提交到 Project”也可表达 Commit 意图。

### 阅读与手动修改 draft

Freeform 和六类 Workflow 共用章节 draft。路径相对于当前 Project 根目录，为 `working/<session-id>/<task-id>/<chapter-id>.draft.md`。每章一个文件，包含概述、大纲、正文三个区域；尚未生成的区域暂时为空。使用任意文本编辑器打开并保存即可，不需要新 CLI 命令。

- 只修改相应区域的内容；保留章节标题、区域标题和 `<!-- novel-agent:... -->` 边界标记，不要向尚未生成的区域填入内容。文件使用 UTF-8，支持 BOM 和 Windows 换行。
- 单章节 Workflow 概述阶段输入 `y`：保存并批准手动概述，再生成大纲；保留 draft。
- 大纲阶段输入 `y`：保存手动修改，保留 draft；后续正文读取更新后的大纲。在 Freeform 中，可继续输入“按刚确认的大纲为同一个章节写正文”。
- 正文阶段输入 `y`：先保存内容为 Artifact revision，再删除可编辑 draft。Freeform 的 `y` 只批准工作稿，仍须明确 Commit；Workflow 按原流程继续审核及 Commit。
- 成功 Commit 后删除对应 draft，Artifact 历史版本仍可用 `/artifact show` 查看。若在审核期间又保存了新编辑，文件会保留，后来的编辑不会悄悄进入刚才的 Commit。
- `n` 或修改意见：先保存已有手动编辑的 revision，再更新当前阶段到同一个 draft；不会直接覆盖未同步的编辑。生成期间又修改文件时，系统会要求明确选择。
- 已确认的大纲/概述在后续阶段再次被修改时，系统显示 `decision[1/2/3]>`：`1` 根据新依据重新生成，`2` 保留手动稿继续，`3` 退出。新单章节 Workflow 明确选择后先返回被修改的上游阶段确认；选择 `1` 清除其下游工作稿区域，再生成，选择 `2` 保留文本并重新确认受影响阶段。Auto Approval 不代选 Decision；明确选择之后，Auto 仍可推进后续 Approval。多章节、导入与旧版单章节的原有阶段安排保留。
- Freeform 输入 `e` 可退出当前 draft 确认；Workflow 输入 `e` 或 `/workflow exit` 退出流程。退出时保留 draft；重开 Session 后可恢复等待中的 Workflow。导入的源文件始终保留。

`/artifact show` 仍可在终端查看完整 Artifact，但它展示已同步的 revision；刚在编辑器保存的内容要在 `y`、修改请求或 `/commit current` 时同步。编辑器内尚未保存的内容无法读取。默认 draft 上限为 2 MB，由 `src/novel_agent/config/setting.py` 的 `draft_max_bytes` 配置。

要删除一个**正式章节**，可以直接输入“删除第二章”或“把第二章删掉”。Agent 按 Project 的章节顺序确定目标，再通过受控 Commit 删除对应章节文件、更新章节索引并重建 `MANUSCRIPT.md`。这是明确授权的直接编辑，不产生待提交的工作稿，也无需再输入 `/commit`。删除结果会报告章节 ID、Commit ID 和变更文件；如果 Project 在定位后发生变化，删除会停止。章节结构 Commit 后，文件名会重新对齐当前顺序：第二章为 `chapters/0002.md`。其他章节正文不会自动改写，但其文件路径可能改变；旧格式 Project 会在下一次章节结构 Commit 时对齐。

一个 Project 可以有多个 Session。`/session create 修订讨论` 会创建独立会话；会话之间不共享对话记忆，但读取同一个 Project 的正式章节。重新运行 CLI 后，先 `/project open 雾港`，再按需 `/session open 修订讨论`。

输入 `/history` 可查看当前 Session 最近 20 条用户消息与助手最终回复，重开 Session 后仍可查看。历史记录保存展示过的 `Artifact ID@revision` 引用；单条消息超过 4000 字符时会标示截断，完整工作稿仍从 Artifact 查看。记录保留上限、单条长度和展示条数集中配置在 `src/novel_agent/config/setting.py`。`/clear` 仅清空终端显示，不会影响 `/history`、正在等待的 Workflow 或正式 Project 内容。

## Workflow：分阶段创作与审批

设置 `/mode workflow` 后，创作请求会交给相应业务 Workflow。当前支持：创作章节、续写下一章、连续创作多章、补写缺失章节、重写已有章节、导入现有小说。输入示例：

```text
/mode workflow
创作第一章，约 1500 字，结尾留一个悬念。
```

新单章节 Workflow（创作、续写、补写、重写）先只生成概述并等待 `SUMMARY` Approval；输入 `y` 后生成大纲并等待 `OUTLINE` Approval；再次输入 `y` 后生成正文、审阅并等待 `MANUSCRIPT` Approval；第三次 `y` 后进入 Commit。概述或大纲阶段输入 `n` / 修改意见只修订当前阶段，不重做已确认的其他阶段。每章仍使用同一个 draft，尚未生成区域留空。多章节和导入保留原有阶段安排。CLI 会给出当前 Gate 的 draft 路径；`/workflow status` 也会显示工作稿路径和 Artifact 引用。打开文件阅读或修改，再确认；需要在终端查看时仍可使用 `/artifact show <ID>@<revision>`。

旧版已经暂停的单章节 Workflow 在升级后仍按原来的 `PLAN`（概述与大纲合并确认）恢复完成，不自动改动其候选或 Gate；新启动任务采用分阶段确认。

创作整部小说时，可以分别说明**全书计划章数**和**本次先写的章节**。例如“写一部 5 章的悬疑小说，先写前 3 章，后两章以后再写”：本次进入三章创作 Workflow，第四、第五章不会预先写入正式 Project。也可用“第一章：……第二章：……”分别给出每章要求；这些要求会送到对应章节的生成阶段。明确总章数与逐章列举不一致时，CLI 会显示 `decision[reply]>`，请补充缺失章节要求、说明由 Agent 规划剩余章节，或说明本次只写哪些章节。此澄清状态随 Session 保存；可输入“取消”放弃请求。普通问答不会替你作出选择，`/workflow status` 会显示待澄清原因。

新生成的章节大纲默认依次使用“本章目标与冲突、背景、情节结构、出场人物、场景规划、叙事策略、连续性检查、辅助资料”；存在直接相关的前章正式正文时，末尾再加“前情提要”。每个场景描述地点、参与者、目标、阻碍、行动、结果和衔接；文风与结尾钩子按用户要求决定。Freeform 大纲也使用这一默认结构，用户明确指定其他格式时以用户要求为准。当前仅通过生成提示引导格式，不执行栏目完整性校验；已有大纲不会自动改写。

等待 Approval 时，提示符是 `approve[y/n/e]>`：

| 输入 | 效果 |
| --- | --- |
| `y` | 读取 draft 已保存的编辑，批准当前阶段并继续 Workflow；正文确认后删除 draft。批准某一阶段本身不等于 Commit。 |
| `n` | 拒绝当前候选并请求重新生成。 |
| `e` 或 `/workflow exit` | 退出该 Workflow，保留 Session；已完成的 Commit 不会回滚。 |
| 明确的修改意见，如“请把第二场景改成夜晚，并弱化旁白” | 修改当前候选，产生新 revision 后再次等待确认。 |

`/auto on` 会自动通过 Approval Gate；如果当前正等待 Approval，也会立即继续。`/auto off` 恢复后续人工确认。Auto Approval **不会替用户做 Decision Gate 选择**。当 Project 内容变化导致 Workflow 需要决策时，提示符为 `decision[1/2]>`：输入 `1` 基于最新 Project 重新生成，或输入 `2` 退出。Decision Gate 不接受 `y/n` 作为选择。

生成阶段也可能因必要条件缺失、无法解决的事实冲突或关键材料超出上下文预算暂停到 Decision。CLI 显示具体原因，问题报告不会当作小说正文保存。输入 `1` 重试、`2` / `e` 退出，或给出明确条件，例如“采用林舟是守塔人的新设定，重新生成”。这次决策会成为当前任务的最新指令，后续大纲、正文及审阅也使用它；普通问答与 `y` 不会代替该选择。已经生成的候选保留在 draft 和 Artifact 中，可继续手动修改；重试读取已保存的编辑，审阅失败的重试不会重新生成正文。退出并重开 Session 后仍等待原 Decision。

Workflow 正文展开使用最新确认大纲，包括手动修改；修订优先改反馈涉及部分，审阅只给出报告与局部建议，不自动替换正文或批准。授权的新创作可以提出候选剧情，但不能捏造已有正式事实或资料来源。审阅结论“通过”仍不等于 Approval 或 Commit。

上下文预算在 `src/novel_agent/config/setting.py` 的 `generation_context_max_chars` 中统一设置，默认 18,000 个内容字符（不是 token 数，也不包含提示模板和元数据）。当前候选大纲、正文、重写基稿、逐章要求和已选 Skill 优先完整保留；其他正式章节与参考材料按阶段和事实层级分配余量，截取片段会标记为不完整。关键材料本身超预算时不会静默截断，可以缩小任务或调整配置后重启 CLI，再输入 `1` 重试。预算应与实际模型可用上下文相匹配。

Workflow 等待期间可以照常提问，例如“目前第一章的主角是谁？”。普通聊天不会批准或恢复正在等待的 Workflow。要修改候选，请写明确的修改指令；要批准或退出，请用上述 Gate 输入或命令。`/workflow status` 可随时查看当前阶段、Gate 和 Artifact 引用。

退出 CLI 后，等待中的 Workflow 不会消失。恢复步骤：

```text
/project open 雾港
/session open Main
/workflow status
y
```

如果原流程属于其他 Session，打开对应 Session 后再查看。`/exit` 是退出应用，`/workflow exit` 是退出工作流。暂停时切换模型（如 `/model grok`）只影响后续模型调用，不会改写已生成的 Artifact。

续写需要已有正式前章；补写需要可作为约束的后续正式章节；重写需要目标章节存在。导入时先将源文件放到当前 Project 的 `sources/` 下，再发出自然语言请求，例如“导入小说 sources/旧稿.txt”。导入源支持 `.txt`、`.md`、`.pdf`；PDF 只提取文字，不提供 OCR。

## Skill：复用写作方法

新增六个内置写作方法 Skill：`writing-foundations`（人物行动与节奏）、`viewpoint-control`（视角与转场）、`environment-writing`（环境）、`dialogue-writing`（对白）、`antagonist-design`（反派）和 `prose-style-review`（文风审阅）。前五个允许 Workflow 按需自动选择；文风审阅仅手动选择并限于 Workflow 的审阅阶段，不自动改写工作稿。内置启用不意味着每次都注入，用户文风与正式 Project 事实仍优先。

例如输入 `/skill use dialogue-writing`，再请求对白场景大纲或正文；输入 `/skill use viewpoint-control`，再明确要求限定、多视角或全知叙事。需要文风报告时，用 `/skill use prose-style-review` 后提出审阅请求。Freeform 明确要求润色时可以产生修订 Artifact，但不会仅因审阅而替换 draft 或 Commit。可用 `/skill disable <name>` 停用某项。来源与规则修正见 [资料整理说明](开源项目资料/README.md)。

Skill 是写作/分析指引，不会给 Agent 新增 Project 写入权限。内置 Skill 默认启用；Project 自定义 Skill 放在 `skills/<name>/SKILL.md`，默认需要先 `/skill enable <name>`。`SKILL.md` 使用 UTF-8 与 YAML frontmatter，至少包含与目录同名的 `name` 和单行 `description`：

```markdown
---
name: quiet-scenes
description: 用于需要克制旁白的安静场景写作。
---

写作时优先呈现人物行动与场景细节。不要把 Skill 当成小说世界的事实。
```

在 CLI 输入 `/skill list` 查看状态，`/skill show quiet-scenes` 阅读原文，`/skill enable quiet-scenes` 启用。启用后 Main Agent 可在相关 Freeform 任务中按需读取；`/skill use quiet-scenes` 会明确指定下一次用户请求，成功执行后自动清除。Workflow 开始时会固定选定 Skill 的版本，暂停后即使原文件变化，恢复仍使用开始时的版本。已生成 Artifact 不会因为 Skill 修改而重写；导入 Workflow 的原始正文也不会被 Skill 改写。

Workflow 可按创作任务自动选用 Skill。**仅启用不等于允许自动注入**：Skill 的 frontmatter 还须标记 `activation: auto`；未标记时默认为 `manual`，仍可通过 `/skill use` 或在请求中点名。可选元数据如下，多个值用英文逗号分隔：

```markdown
---
name: continuity
description: 用于悬疑章节的人物知情顺序与线索连续性检查。
activation: auto
workflows: CREATE_CHAPTER, CONTINUE_CHAPTER, MULTI_CHAPTER
stages: outline, manuscript, review
tags: 线索, 人物知情, 连续性
resources: references/checklist.md
---
根据已确认的章节事实核对线索与人物知情顺序。
```

Project Skill 仍须先 `/skill enable continuity`。`/skill list` 会显示 `manual` 或 `auto`。Workflow 根据任务及逐章要求选择候选；章节生成时也可根据已确认的大纲，从启动时冻结的候选中启用适用 Skill。`stages` 限定注入阶段，`resources` 只读取明确列出的 `references/` 文本文件。每个 Artifact 记录该阶段实际使用的 Skill 版本；普通聊天、候选文件变化和模型切换不会重选正在运行的 Workflow。显式 `/skill use` 优先于自动选择。

Skill 只支持文本 `SKILL.md`，以及 `references/`、`assets/` 下的 `.md`/`.txt` 支持文件；目前不执行 `scripts/`。如果 Skill 内容与 Project 正式正文冲突，以正式正文为准。Skill 产生的候选仍需遵守原有 Artifact、Approval 和 Commit 规则。

## Project 内附件

聊天输入可带一个或多个当前 Project 内的 `.txt` / `.md` 文件：

```text
请结合 @references/人物设定.md 分析主角动机。
请比较 @sources/前传.txt 和 @"references/世界 设定.md"，指出冲突。
```

路径相对于当前 Project 根目录；CLI 会在调用 Agent 前检查文件是否存在、格式是否支持、规范化后是否仍位于 Project 内。Project 外路径会被拒绝。附件内容仅作为本次输入；文件或模型输出里出现 `/command` 也不会被当成 CLI 命令执行。导入 Workflow 的源路径写在自然语言请求中，不使用 `@` 附件语法。

## 输出与常见问题

普通聊天的 Markdown 响应会流式显示；读取、生成、提交等 runtime event 单独显示，不会写进小说正文。运行期间按 `Ctrl+C` 可取消当前 run，随后可用 `/status` 和 `/workflow status` 检查状态；在空闲输入提示符按 `Ctrl+C` 则退出 CLI。

- **模型配置错误或启动失败**：检查 `--env` 路径、`NOVEL_DEFAULT_MODEL`、所选模型的 `MODEL_ID` 和 `API_KEY`；用 `/model list` 核对已配置别名。
- **提示先打开 Project/Session**：执行 `/project create <名称>` 或 `/project open <名称>`；后者默认选中初始 Session。
- **Session 不存在或不唯一**：先 `/session list`，再用完整名称或足够长的唯一 ID 前缀打开。
- **附件路径被拒绝**：确认文件在当前 Project 内、扩展名为 `.txt` 或 `.md`，且路径与文件名一致。
- **工作稿未出现在正式章节**：用 `/artifact list` 查看其状态，检查是否已 Commit。Approval 和生成 Artifact 都不会单独写入正式章节。
- **Commit 冲突**：Project 在工作稿生成后已变化；先查看最新正式内容，再修订或重新生成候选，避免覆盖新内容。

更详细的产品语义见[完整需求](小说Agent项目完整需求文档_V2.md)、[Agent 设计](小说Agent设计文档_DeepAgent.md)和[CLI 设计](小说Agent_CLI设计文档_V1.md)。设计文档包含未来方向；日常操作请以本说明和 CLI `/help` 为准。
