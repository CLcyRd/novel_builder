# 小说 Agent CLI 设计文档

> 当前版本：V1  
> 本文档定义小说 Agent 的 CLI 交互设计。  
> 当前阶段仅实现 CLI，不包含客户端、Web UI 或其他前端形态。  
> 本文档不包含代码。

---

# 1. 设计目标

CLI 是当前版本用户与小说 Agent 交互的唯一入口。

CLI 需要支持：

- Project 创建、打开和切换
- Session 创建、打开和切换
- 与 Agent 进行自然语言对话
- 在对话中附带 Project 内文件
- 查看 Agent 执行状态
- Workflow 人工确认
- Workflow 退出
- Freeform / Workflow Mode 切换
- Auto Approval 切换
- 大模型查看与切换
- Working Artifact 查看和 Commit
- 长任务取消
- 失败任务重试和 Workflow 恢复
- Markdown 渲染
- 流式输出
- 应用退出

CLI 的核心设计目标不是只实现一个“聊天终端”，而是成为小说 Agent 的完整控制入口。

---

# 2. CLI 设计原则

## 2.1 普通文本与控制命令分离

统一约定：

- 普通输入：发送给 Main Agent
- `/` 开头：CLI 控制命令
- `@` 文件引用：当前消息附带文件

例如：

```text
帮我写第五章大纲
```

发送给 Agent。

```text
/status
```

执行 CLI 控制命令。

```text
根据这个大纲重写第五章 @references/new-outline.md
```

表示发送 Prompt，并附带 Project 内文件。

---

## 2.2 用户明确指令优先

CLI Mode 只是默认行为。

例如当前：

```text
Mode: WORKFLOW
```

用户仍可以直接输入：

```text
不要进入工作流，直接把第三章最后一句删掉
```

Agent 应按当前明确指令处理。

---

## 2.3 Project 是所有文件操作边界

CLI 中用户引用的文件默认必须位于当前 Project Root 内。

不允许通过附件输入读取 Project 外任意文件。

---

## 2.4 控制状态必须可见

用户应可以随时知道：

- 当前 Project
- 当前 Session
- 当前模型
- 当前 Execution Mode
- Auto Approval 状态
- 当前 Task
- 当前 Workflow
- 当前 Workflow Stage
- Pending Gate
- 当前 Working Artifact

统一通过 `/status` 查看。

---

## 2.5 长任务必须可中断

Agent 正在执行：

- 长文本生成
- 多章节分析
- Subagent 分析
- Workflow
- Tool 调用

时，用户必须可以停止当前 Run，而不退出整个应用。

---

# 3. CLI 启动流程

CLI 启动后先完成环境检查。

检查内容包括：

- Project 工作目录是否可用
- `.env` 是否可读取
- 是否配置至少一个可用模型
- 默认模型配置是否有效
- 必要模型凭证是否存在

如果模型配置无效，应在进入聊天前提示用户。

---

## 3.1 未选择 Project

如果当前没有 Project：

```text
No project selected.

[1] Create project
[2] Open project
[3] Exit
```

用户必须选择 Project 后才能进入 Session。

---

## 3.2 已选择 Project 但没有 Session

进入 Project 后：

```text
Project: 深海回声

[1] Create session
[2] Open existing session
[3] Back
```

---

## 3.3 进入 Session

进入 Session 后显示简要状态：

```text
Project: 深海回声
Session: chapter-25
Model: model-a
Mode: FREEFORM
Auto Approval: OFF

Type /help for commands.
```

然后进入持续交互。

---

# 4. Project 命令

## 4.1 创建 Project

命令：

```text
/project create
```

创建时要求用户输入：

- Project 名称
- 初始 Session 名称

创建完成后：

- 自动进入新 Project
- 自动创建初始 Session
- 自动进入该 Session

---

## 4.2 Project 列表

```text
/project list
```

输出示例：

```text
Projects

1. 深海回声
2. 北境来信
3. 无人街区
```

---

## 4.3 打开 Project

```text
/project open
```

显示可选择 Project。

用户选择后进入该 Project。

如果当前 Session 存在未完成任务，需要先提示当前状态。

---

## 4.4 查看当前 Project

```text
/project current
```

显示：

- Project 名称
- Project Root
- Session 数量
- 当前 Session

---

## 4.5 切换 Project

支持：

```text
/project switch
```

语义与 `open` 类似。

切换后当前 Session 上下文随之切换。

---

## 4.6 后续可扩展能力

非 V1 必需，但保留：

```text
/project rename
/project archive
/project delete
```

---

# 5. Session 命令

## 5.1 创建 Session

```text
/session create
```

必须输入：

- Session 名称

创建完成后自动进入该 Session。

新 Session：

- 不继承其他 Session 的对话记忆
- 重新通过当前 Project 获取正式小说信息
- 默认 Mode 根据系统默认配置初始化
- Auto Approval 默认关闭

---

## 5.2 Session 列表

```text
/session list
```

输出：

```text
Sessions

1. chapter-25
2. rewrite-ending
3. import-original
```

可以额外显示：

- 最近使用时间
- 是否存在 Active Workflow
- 是否存在 Pending Gate

---

## 5.3 打开 Session

```text
/session open
```

选择已有 Session 后进入。

需要恢复：

- 对话历史
- 当前 Mode
- Auto Approval
- Active Task
- Active Workflow
- Pending Gate
- Working Artifact

---

## 5.4 当前 Session

```text
/session current
```

显示当前 Session 信息。

---

## 5.5 后续可扩展能力

```text
/session rename
/session archive
/session delete
```

---

# 6. 普通对话输入

用户直接输入自然语言：

```text
帮我总结第三章发生了什么
```

CLI 将完整文本发送给 Main Agent。

---

## 6.1 Freeform 下创作

例如：

```text
给我第五章写一个大纲
```

Agent：

- 生成 Outline Artifact
- 通过流式 Markdown 返回内容
- 不自动写入 Project

---

## 6.2 用户明确直接编辑

例如：

```text
把第三章最后一句删掉
```

Agent识别为直接 Project 修改请求。

如果不存在额外业务确认要求，则直接执行。

---

## 6.3 自然语言 Commit

例如：

```text
把刚才的大纲和正文提交到第五章
```

Main Agent识别 Commit Intent，并将明确的 Working Artifact Commit 到 Project。

---

# 7. 对话附件

当前版本用户需先将文件放入 Project 文件夹。

---

## 7.1 文件引用语法

推荐：

```text
@relative/path/file.md
```

例如：

```text
根据这个大纲续写下一章 @references/outline.md
```

---

## 7.2 支持多附件

例如：

```text
根据这些资料写第五章 @outline.md @characters.md @notes.txt
```

---

## 7.3 支持格式

当前对话附件支持：

- `.txt`
- `.md`

---

## 7.4 文件验证

文件是否存在由 CLI / 系统确定性检查。

如果不存在：

```text
File not found:
references/outline.md
```

不将不存在的文件交给 Agent 判断。

---

## 7.5 Project Root 限制

附件路径必须位于当前 Project Root 内。

例如：

```text
@references/a.md
```

合法。

尝试访问 Project 外路径应拒绝。

---

## 7.6 文件名带空格

CLI 需支持带空格的文件路径。

具体输入规范实现时统一定义，例如：

```text
@"references/new outline.md"
```

---

## 7.7 文件不自动 Commit

附件只作为当前对话输入。

除非用户明确要求：

- 写入 Project
- 替换正式内容
- 保存为辅助资料

否则不修改 Project。

---

# 8. 执行状态输出

Agent 接收到用户任务后，在最终结果前需要持续输出执行状态。

状态信息与最终 Assistant 内容必须分离。

---

## 8.1 推荐状态类型

至少支持：

```text
Thinking
Reading
Searching
Calling Tool
Delegating
Generating
Reviewing
Waiting
Committing
Completed
Retrying
Cancelled
Failed
```

---

## 8.2 示例

```text
● Thinking
● Reading chapters/0012.md
● Reading references/characters.md
● Searching previous appearances of 林默
● Delegating continuity analysis
● Generating outline
● Reviewing result
✓ Completed

# 第十三章大纲

...
```

---

## 8.3 状态不能污染 Artifact

类似：

```text
● Reading...
● Calling tool...
```

只属于执行事件。

不能写入：

- 大纲 Artifact
- 正文 Artifact
- Project 文件

---

# 9. Verbose 模式

建议增加：

```text
/verbose on
/verbose off
/verbose
```

---

## 9.1 普通模式

显示高层状态：

```text
● Reading project
● Generating
● Reviewing
✓ Completed
```

---

## 9.2 Verbose 模式

显示更详细执行事件：

```text
● Tool: read_chapter chapters/0012.md
● Tool: search_project "林默"
● Subagent: continuity-analysis
● Tool: count_characters
```

主要用于：

- 调试
- 高级用户
- 排查任务执行过程

---

# 10. Markdown 渲染

Agent 最终输出使用 Markdown 渲染。

至少支持：

- 标题
- 加粗
- 斜体
- 列表
- 引用
- 表格
- 代码块
- 分隔线

---

# 11. 流式输出

Agent 内容默认流式输出。

---

## 11.1 普通文本

按 chunk 持续显示。

---

## 11.2 Markdown Block

对于以下结构：

- 表格
- 代码块
- 引用块
- 多层列表

可以在完整 Block 生成后再做最终渲染，以避免流式渲染频繁错位。

---

## 11.3 状态与内容双通道

逻辑上区分：

```text
Execution Event
Assistant Content
```

Execution Event 显示执行过程。

Assistant Content 才作为对话消息和 Artifact 候选内容。

---

# 12. `/status`

`/status` 是 V1 核心命令。

命令：

```text
/status
```

推荐输出：

```text
Project        深海回声
Session        chapter-25
Model          model-a

Mode           WORKFLOW
Auto Approval  OFF
Verbose        OFF

Active Task
  Continue Chapter 25

Workflow
  Type         ContinueChapterWorkflow
  Stage        OUTLINE_APPROVAL
  Status       WAITING_APPROVAL

Working Artifacts
  Summary      art_0023
  Outline      art_0024

Pending
  Waiting for outline approval
```

没有 Active Task 时明确显示：

```text
Active Task: None
Workflow: None
```

---

# 13. Execution Mode

命令：

```text
/mode
/mode freeform
/mode workflow
```

---

## 13.1 查询

```text
/mode
```

显示：

```text
Execution Mode: FREEFORM
```

---

## 13.2 切换 Freeform

```text
/mode freeform
```

后续任务默认自由协作。

---

## 13.3 切换 Workflow

```text
/mode workflow
```

后续创作型任务默认进入标准 Workflow。

---

## 13.4 Active Workflow 时切换

如果当前已经有 Active Workflow：

Mode 切换只影响后续新任务。

不自动取消或改变已经运行的 Workflow。

---

# 14. Auto Approval

命令：

```text
/auto
/auto on
/auto off
```

---

## 14.1 查询

```text
/auto
```

显示当前状态。

---

## 14.2 开启

```text
/auto on
```

Workflow 中后续 Approval Gate 自动通过。

如果当前正处于 WAITING_APPROVAL：

- 当前 Approval 自动通过
- Workflow 立即继续

---

## 14.3 关闭

```text
/auto off
```

后续 Approval Gate 恢复人工确认。

---

## 14.4 Decision Gate

无论 Auto 是否开启：

Decision Gate 都必须等待用户选择。

---

# 15. Workflow 命令

至少支持：

```text
/workflow status
/workflow exit
```

---

## 15.1 Workflow Status

输出：

```text
Workflow      Continue Chapter
Target        Chapter 26
Stage         MANUSCRIPT_APPROVAL
Status        WAITING_APPROVAL

Artifacts
  Summary     APPROVED
  Outline     APPROVED
  Manuscript CANDIDATE
```

---

## 15.2 Workflow Exit

```text
/workflow exit
```

含义：

- 退出当前 Workflow
- 不退出 Session
- 不退出应用
- 已 Commit 内容保留
- 未 Commit Artifact 不进入 Project

---

## 15.3 无 Workflow

如果没有 Active Workflow：

```text
No active workflow.
```

---

# 16. Workflow Approval

当 Workflow 进入 Approval Gate：

```text
Approve current outline? [y/n/e]
```

支持：

```text
y
n
e
```

以及自然语言反馈。

---

## 16.1 `y`

表示：

```text
APPROVE
```

继续 Workflow。

---

## 16.2 `n`

表示当前 Candidate 不接受。

系统应进入 Reject / Revise 处理，而不是 Commit。

---

## 16.3 `e`

表示：

```text
EXIT WORKFLOW
```

---

## 16.4 自然语言反馈

例如：

```text
第二个场景删掉，男女主不要这么早见面
```

解释为：

```text
REVISE
```

并将文本作为 Revision Feedback。

Workflow修改 Candidate 后重新进入 Approval。

---

# 17. Decision Gate

Decision Gate 不能使用普通 y/n。

例如：

```text
Conflict detected:

Chapter 12 states that 陈医生 is dead,
but the current task requires him to appear in Chapter 15.

[1] Keep historical fact and adjust current chapter
[2] Change historical setting
[3] Exit workflow
```

用户输入：

```text
1
```

恢复 Workflow。

也允许用户输入自然语言选择。

Auto Approval 不处理 Decision Gate。

---

# 18. Working Artifact 命令

V1 建议至少支持：

```text
/artifact list
/artifact show <id>
/artifact current
/artifact discard <id>
```

---

## 18.1 Artifact List

示例：

```text
ID        Type        Target       Revision   Status
a12       OUTLINE     Chapter 5    3          CANDIDATE
a13       MANUSCRIPT  Chapter 5    1          CANDIDATE
```

---

## 18.2 Artifact Show

```text
/artifact show a12
```

显示：

- Artifact 类型
- Target
- Revision
- Status
- 内容

内容使用 Markdown 渲染。

---

## 18.3 Artifact Current

显示当前 Session 中最主要的 Working Artifact。

---

## 18.4 Artifact Discard

```text
/artifact discard a12
```

将 Artifact 标记为 Discarded。

不修改 Project。

---

# 19. Commit

Commit 同时支持：

- 自然语言
- 显式命令

---

## 19.1 自然语言

例如：

```text
把刚才的大纲和正文提交
```

---

## 19.2 显式 Commit

```text
/commit a12 a13
```

适合存在多个 Artifact 时进行确定性选择。

---

## 19.3 Commit 前检查

Commit 前执行：

- Artifact 是否存在
- Artifact Revision 是否有效
- Target 是否存在
- Project Freshness
- Changes 是否冲突

---

## 19.4 Project 已变化

如果 Artifact 生成后对应 Project 内容已经变化：

进入 Decision Gate。

不静默覆盖。

---

## 19.5 Commit 成功

示例：

```text
✓ Commit completed

Changed:
- chapters/0005.md
- MANUSCRIPT.md

Artifacts:
- a12 OUTLINE
- a13 MANUSCRIPT
```

---

# 20. Model 管理

模型信息在启动前配置到 `.env`。

CLI 启动时读取可用模型配置。

---

## 20.1 模型列表

```text
/model list
```

示例：

```text
Available Models

1. model-a
2. model-b
3. model-c

Current: model-a
```

---

## 20.2 当前模型

```text
/model current
```

---

## 20.3 切换模型

```text
/model use model-b
```

切换后影响后续模型调用。

---

## 20.4 Workflow 中切换模型

如果 Workflow 当前暂停：

- 切换后续节点使用的模型
- 已生成 Artifact 不重新生成

如果 Agent 正在执行中：

- 不在当前正在执行的模型调用中途切换
- 切换对后续调用生效

---

# 21. Session History

建议支持：

```text
/history
```

或：

```text
/session history
```

显示当前 Session 最近对话。

---

## 21.1 历史内容

包括：

- User 消息
- Assistant 最终消息

默认不需要展示所有底层 Tool 事件。

Verbose / Debug 场景可另外查看执行 Trace。

---

# 22. 当前执行取消

Agent 正在运行时：

```text
Ctrl+C
```

默认行为：

> 取消当前 Run，而不是退出整个应用。

---

## 22.1 取消结果

```text
Current run cancelled.
Session remains active.
```

---

## 22.2 对 Workflow 的影响

如果只是取消某个正在执行的 Workflow Run：

- 保留已持久化 Workflow State
- 不写入未 Commit Candidate
- Workflow 后续是否 Resume 根据状态判断

如果用户明确输入：

```text
/workflow exit
```

才真正取消整个 Workflow。

---

## 22.3 第二次 Ctrl+C

实现时可考虑：

- 第一次取消当前 Run
- 连续第二次才退出应用

但 V1 可以先统一要求 `/exit` 退出应用，以避免误退出。

---

# 23. Retry 与 Resume

## 23.1 Retry

```text
/retry
```

用于：

- 最近失败 Task
- Retryable Tool Error
- 模型临时错误

不要求用户重新输入完整 Prompt。

---

## 23.2 Resume

```text
/resume
```

用于异常中断后的 Workflow / Task 恢复。

正常 Approval 后的 Resume 由系统自动完成，不要求用户手动输入 `/resume`。

---

# 24. Help

必须支持：

```text
/help
```

输出按类别分组。

示例：

```text
Project
  /project create
  /project list
  /project open
  /project current

Session
  /session create
  /session list
  /session open
  /session current

Agent
  /status
  /mode
  /auto
  /model
  /verbose

Workflow
  /workflow status
  /workflow exit

Artifacts
  /artifact list
  /artifact show
  /artifact current
  /artifact discard
  /commit

Recovery
  /retry
  /resume

General
  /history
  /help
  /clear
  /exit
```

---

## 24.1 单命令帮助

建议支持：

```text
/help commit
```

显示：

- 命令用途
- 参数
- 示例

---

# 25. Clear

建议支持：

```text
/clear
```

只清理终端显示。

不清理：

- Session
- 对话历史
- Task
- Workflow
- Artifact
- Project

---

# 26. Exit Application

命令：

```text
/exit
```

退出 CLI 应用。

---

## 26.1 有运行中任务

如果有 Active Run：

先停止当前 Run。

---

## 26.2 有 Waiting Workflow

如果 Workflow 正处于等待状态：

退出应用不等于退出 Workflow。

Workflow State 保持。

下次进入该 Session 后恢复等待状态。

---

## 26.3 App Exit 与 Workflow Exit 区分

```text
/exit
```

退出程序。

```text
/workflow exit
```

退出当前 Workflow。

二者语义必须严格区分。

---

# 27. Prompt 输入状态

CLI 输入提示符应根据当前状态变化。

普通状态：

```text
novel>
```

等待 Approval：

```text
approve[y/n/e]>
```

等待 Decision：

```text
decision[1/2/3]>
```

这样用户可以明确感知当前交互状态。

---

# 28. Workflow 等待期间普通聊天

Workflow 等待 Approval / Decision 时，用户仍可能输入普通问题。

系统需要先判断：

- 是 Workflow Response
- 还是普通 Agent 消息

例如当前：

```text
approve[y/n/e]>
```

用户输入：

```text
第三章女主知道真相了吗？
```

不能直接当 Reject 或 Revision。

应识别为普通聊天：

- Main Agent 回答问题
- Workflow 保持 WAITING_APPROVAL

随后继续显示：

```text
approve[y/n/e]>
```

---

# 29. 多任务状态

CLI 不要求 V1 并行执行多个模型 Run，但需要能够展示多个逻辑 Task。

例如：

```text
/status
```

可能显示：

```text
Tasks

T1  Backfill Chapter 1   WAITING_APPROVAL
T2  Summarize Chapter 10 COMPLETED
T3  Rewrite Chapter 2    BLOCKED_BY T1
```

V1 可以串行调度，但状态模型不要限制为只能存在一个 Task。

---

# 30. 错误展示

错误需要区分可恢复性。

---

## 30.1 Retryable Error

例如模型暂时不可用：

```text
Temporary model error.
Use /retry to retry the last task.
```

---

## 30.2 用户可修复错误

例如附件不存在：

```text
File not found:
references/outline.md
```

---

## 30.3 Decision Required

例如目标文本有多个匹配：

```text
3 matching paragraphs found.

[1] Chapter 5 paragraph 3
[2] Chapter 5 paragraph 8
[3] Chapter 7 paragraph 2
```

---

## 30.4 Fatal Error

例如 Project 核心文件损坏：

```text
Project state could not be loaded.
No project changes were applied.
```

---

# 31. Model / Tool 执行事件与最终输出

CLI 必须避免把工具细节和最终小说文本混在一起。

推荐视觉上：

```text
● Thinking
● Reading chapter 12
● Reviewing continuity

────────────────────────

# 第十三章

正文……

────────────────────────

✓ Artifact created: a31
```

最终小说内容是 Assistant Content。

执行状态属于 Runtime Event。

---

# 32. Project 文件修改提示

当 Agent 正式修改 Project 后，需要明确告诉用户。

例如：

```text
✓ Project updated

Changed:
- chapters/0005.md
- MANUSCRIPT.md
```

不应只输出：

```text
完成。
```

用户必须知道哪些文件被改变。

---

# 33. CLI 状态栏建议

如果终端能力允许，可以在输入前显示轻量状态：

```text
[深海回声 | chapter-25 | FREEFORM | model-a]
novel>
```

Workflow 状态：

```text
[深海回声 | chapter-25 | WORKFLOW | WAITING_APPROVAL]
approve[y/n/e]>
```

不是强制要求，但建议实现。

---

# 34. V1 命令总表

## Project

```text
/project create
/project list
/project open
/project current
/project switch
```

## Session

```text
/session create
/session list
/session open
/session current
```

## Agent

```text
/status
/mode
/mode freeform
/mode workflow
/auto
/auto on
/auto off
/model list
/model current
/model use <name>
/verbose
/verbose on
/verbose off
```

## Workflow

```text
/workflow status
/workflow exit
```

## Artifact

```text
/artifact list
/artifact show <id>
/artifact current
/artifact discard <id>
/commit <artifact_id...>
```

## Recovery

```text
/retry
/resume
```

## Utility

```text
/history
/help
/help <command>
/clear
/exit
```

---

# 35. 快捷输入

除 `/` 命令外支持：

## Prompt

```text
帮我写第五章
```

## Prompt + Attachment

```text
根据这个大纲写正文 @outline.md
```

## Multiple Attachments

```text
结合这些资料继续写 @outline.md @characters.md
```

## Workflow Approval

```text
y
n
e
```

## Workflow Revision

```text
第二个场景删掉
```

## Decision

```text
1
```

---

# 36. 典型使用流程：Freeform

```text
/project open
→ 深海回声

/session open
→ rewrite-ch5

/mode freeform

novel> 给我重新设计第五章大纲

● Reading chapter 5
● Generating

# 第五章大纲
...

✓ Artifact created: a12

novel> 第二个场景再紧张一点

● Revising artifact

# 第五章大纲
...

✓ Artifact updated: a12 revision 2

novel> 按这个写正文

...

✓ Artifact created: a13

novel> /commit a12 a13

● Validating
● Committing
● Rebuilding manuscript

✓ Commit completed
```

---

# 37. 典型使用流程：Workflow

```text
/mode workflow

novel> 续写下一章

● Preparing context
● Reading previous chapter
● Reading character references
● Generating summary

# 第十三章概述
...

Stage: SUMMARY
Approve? [y/n/e]
> y

● Generating outline

# 第十三章大纲
...

Stage: OUTLINE
Approve? [y/n/e]
> y

● Generating manuscript
● Reviewing

# 第十三章
...

Approve? [y/n/e]
> y

● Committing
● Rebuilding manuscript

✓ Workflow completed
```

新单章节流程分别确认概述、大纲与正文，`n` / 修改意见只修订当前阶段；CLI 显示阶段与下一步。上述正文在现行 CLI 中由同一个章节 draft 承载，终端提供路径，用户也可用 `/artifact show` 查看版本。概述和大纲确认保留 draft，正文确认或成功 Commit 后删除。旧的未标记版本 checkpoint 保留 PLAN 合并确认恢复；多章节与导入的阶段安排不受此调整影响。

---

# 38. 典型使用流程：Workflow + Auto Approval

```text
/mode workflow
/auto on

novel> 写后面三章

● Generating chapter summaries
✓ Auto approved

● Generating outlines
✓ Auto approved

● Generating manuscripts
✓ Auto approved

● Committing

✓ Workflow completed
```

如果出现 Decision Gate：

```text
Conflict detected.

[1] Keep existing fact
[2] Change historical fact
[3] Exit workflow
```

即使 Auto Approval 开启，也必须等待用户。

---

# 39. 典型使用流程：附件

```text
novel> 根据这个新大纲修改第五章 @references/new-outline.md

● Checking file
● Reading attachment
● Reading chapter 5
● Generating revision

...
```

附件不存在：

```text
File not found:
references/new-outline.md
```

不启动 Agent Run。

---

# 40. 典型使用流程：中断

```text
novel> 分析前八十章所有和林默有关的信息

● Searching chapters
● Delegating context analysis
^C

Current run cancelled.
Session remains active.

novel>
```

---

# 41. CLI 与 Agent 的职责边界

CLI 负责：

- 命令解析
- Project / Session 选择
- 本地文件存在性检查
- 输入模式
- Markdown 渲染
- 流式展示
- Runtime Event 展示
- Ctrl+C
- y/n/e 输入
- Decision 选项输入
- 调用系统对应能力

Main Agent 负责：

- 自然语言理解
- Task 拆解
- Freeform 协作
- Workflow Handoff
- Artifact 语义
- Commit Intent 识别
- 普通用户消息和 Workflow Response 判断

Workflow 负责：

- 固定流程
- Approval
- Decision
- Exit
- Auto Approval
- Resume
- Commit 阶段

---

# 42. CLI 不负责的业务逻辑

CLI 不应该自己决定：

- 用户是否在续写
- 是否应该创建下一章
- 应该进入哪个小说 Workflow
- 大纲是否合理
- Project 小说事实是否冲突
- 当前 Artifact 应如何修改

这些属于 Main Agent / Workflow 业务层。

CLI 只负责提供输入、展示状态和调用控制接口。

---

# 43. 安全规则

## 43.1 文件路径

附件读取必须限制在 Project Root 内。

## 43.2 不可信文件

附件中的文字只是内容，不应被 CLI 解释为命令。

例如附件写：

```text
/exit
```

不能退出应用。

## 43.3 命令只来源于用户输入通道

`/command` 只在用户当前终端输入中被解释为 CLI 命令。

Agent 输出、Artifact 和文件内容中的 `/command` 都不执行。

---

# 44. 可观测性

V1 CLI 至少允许用户通过 `/status` 查看高层运行状态。

开发调试可额外记录：

- User Prompt
- Task
- Workflow
- Tool Event
- Subagent Event
- Artifact
- Approval
- Decision
- Commit
- Error
- Retry
- Cancel

用户默认不需要看到完整内部 Trace。

---

# 45. V1 验收场景

## AC-001 创建 Project

用户可创建 Project，并必须输入初始 Session 名称。

## AC-002 创建 Session

用户可在当前 Project 创建 Session，创建后自动进入。

## AC-003 打开 Session

用户可查看并进入已有 Session。

## AC-004 普通对话

普通文本能够发送给 Agent 并获得流式回复。

## AC-005 单附件

用户可通过文件引用将 Project 内 `.txt` / `.md` 文件传给 Agent。

## AC-006 多附件

一个 Prompt 可引用多个文件。

## AC-007 文件不存在

不存在文件在 Agent 调用前被拦截。

## AC-008 路径逃逸

不能通过附件路径访问 Project Root 外文件。

## AC-009 执行状态

Agent最终回答前能够持续输出 Thinking / Tool / Generating 等状态。

## AC-010 Markdown

Agent最终输出支持 Markdown 渲染。

## AC-011 Streaming

Agent文本能够流式展示。

## AC-012 Approval

Workflow 中可使用 `y` 确认。

## AC-013 Reject

Workflow 中可使用 `n` 拒绝当前 Candidate。

## AC-014 Revision Feedback

Workflow Approval 时输入普通文本可以作为修改意见。

## AC-015 Workflow Exit

Workflow 中输入 `e` 或 `/workflow exit` 可以退出当前 Workflow。

## AC-016 Auto Approval

`/auto on` 后 Approval 自动通过。

## AC-017 Decision Gate

Auto Approval 开启时 Decision Gate 仍等待用户。

## AC-018 Mode

用户可在 Freeform / Workflow Mode 之间切换。

## AC-019 Model

用户可查看并切换 `.env` 中已配置模型。

## AC-020 Status

`/status` 能显示当前 Project、Session、Model、Mode、Auto、Task、Workflow 和 Artifact 信息。

## AC-021 Artifact List

用户可以查看当前 Working Artifact。

## AC-022 Explicit Commit

用户可通过 `/commit` 提交明确 Artifact。

## AC-023 Natural Language Commit

用户也可以通过自然语言要求 Commit。

## AC-024 Cancel Run

执行过程中 Ctrl+C 仅停止当前 Run，不退出应用。

## AC-025 Retry

失败任务可通过 `/retry` 重试。

## AC-026 Workflow Resume

Session重新进入后，可以恢复 Waiting Workflow。

## AC-027 History

用户可以查看当前 Session 对话历史。

## AC-028 Clear

`/clear` 只清屏，不清理 Session 状态。

## AC-029 App Exit

`/exit` 正常退出 CLI。

## AC-030 Exit With Waiting Workflow

退出 CLI 不会自动取消等待中的 Workflow。

---

# 46. V1 优先级

## P0

必须实现：

- Project create / list / open
- Session create / list / open
- 普通聊天
- 单/多附件
- 文件验证
- `/status`
- `/mode`
- `/auto`
- `/model`
- Workflow Approval
- Workflow Exit
- Artifact list / show
- Commit
- Markdown
- Streaming
- Execution Events
- Ctrl+C Cancel Run
- `/help`
- `/exit`

## P1

建议尽快实现：

- `/history`
- `/retry`
- `/resume`
- `/verbose`
- `/clear`
- Project / Session current
- Artifact discard

## P2

后续增强：

- Project / Session rename
- Project / Session archive
- Project / Session delete
- 更丰富的终端状态栏
- 历史搜索
- Artifact 复杂筛选

---

# 47. 最终 CLI 心智模型

用户只需要理解四类交互：

### 1. 直接说话

```text
帮我继续写下一章
```

### 2. 附带文件

```text
根据这个大纲写正文 @outline.md
```

### 3. 控制 Agent

```text
/status
/mode workflow
/auto on
/model use model-b
```

### 4. 管理正式内容

```text
/artifact list
/commit a12 a13
```

系统内部仍然遵循：

> Main Agent + Workflow + Artifact + Commit

CLI 只是将这些能力以最简单、明确、可恢复的方式暴露给用户。
