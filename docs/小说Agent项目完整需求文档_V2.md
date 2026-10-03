# 小说 Agent 项目完整需求文档

> 当前版本：V2  
> 本文档仅描述产品需求、业务规则、文件与状态语义。  
> 不包含客户端、CLI、UI、技术架构、模型设计或代码实现。

---

## 1. 项目目标

小说 Agent 是一个以小说 Project 为持续工作空间、以自然语言为主要交互方式的协作型智能体。

Agent 需要与用户共同完成：

- 从 0 创作小说
- 续写小说
- 补写缺失章节
- 导入已有小说
- 修改、删除、合并、拆分和调整章节
- 对章节或局部文本进行直接编辑
- 翻译、扩写、缩写和其他文本转换
- 读取对话中上传的文本附件
- 查询、总结、分析和讨论小说内容
- 执行包含多个步骤、多个目标和依赖关系的复杂指令
- 在自由协作与标准工作流之间切换
- 将确定后的工作成果正式 Commit 到 Project

Project 是小说正式内容的唯一持久化工作空间。

---

## 2. 核心概念

### 2.1 Project

Project 是一部小说的正式工作空间，用于保存：

- 小说全局信息
- 章节概述
- 章节大纲
- 章节正文
- 辅助资料
- Project 相关状态和 Session 信息

用户可以重新打开已有 Project，并继续已有 Session 或创建新 Session。

### 2.2 Session

Session 是用户与 Agent 的一次连续协作上下文。

规则：

- 一个 Session 对应一个 Project。
- 一个 Project 可以存在多个 Session。
- 继续已有 Session 时，可以继续原对话上下文和未完成任务状态。
- 创建新 Session 时，不继承旧 Session 的对话记忆。
- 新 Session 可以重新读取 Project 正式内容继续工作。

### 2.3 Task

Task 是用户当前要完成的一个明确目标。

一条复杂指令可以拆解为多个 Task，Task 可以存在依赖关系。

### 2.4 Workflow

Workflow 是具有固定阶段、确认点、暂停、恢复和退出能力的业务流程。

当前主要用于：

- 单章节创作
- 多章节创作
- 续写
- 补写
- 创作式重写
- 已有小说导入

### 2.5 Artifact

Artifact 是 Agent、Workflow 或用户在当前协作过程中产生的工作成果。

例如：

- 章节概述
- 大纲
- 正文
- 改写版本
- 翻译结果
- 局部修改结果
- 分析报告
- Review 结果

Artifact 在 Commit 前不等于 Project 正式内容。

### 2.6 Commit

Commit 是将确定的 Artifact 或明确编辑操作正式应用到 Project 的行为。

只有 Commit 或用户明确授权的直接编辑会改变 Project 正式内容。

### 2.7 对话附件

用户在已有对话中上传的 `.txt` / `.md` 文件称为对话附件。

附件默认只作为当前任务上下文，不自动写入 Project。

### 2.8 Project 导入文件

用户在创建或导入已有小说 Project 时提供的原始小说文件。

当前支持：

- `.txt`
- `.md`
- `.pdf`

Project 导入文件与对话附件属于不同场景。

---

## 3. Execution Mode

Session 支持两种执行模式。

### 3.1 Freeform Mode

Freeform 是自由协作模式。

默认行为：

- Agent 根据用户当前指令自由完成任务。
- 创作成果默认先作为 Artifact 返回给用户。
- 不强制进入概述 → 大纲 → 正文工作流。
- 用户明确要求直接编辑 Project 时，可直接修改。
- 用户明确要求 Commit 时，将指定 Artifact 正式写入 Project。

### 3.2 Workflow Mode

Workflow Mode 是标准工作流模式。

创作型任务默认进入相应 Workflow。

例如单章节创作：

章节概述 → Approval → 大纲 → Approval → 正文 → Approval → Commit。

### 3.3 优先级

执行优先级：

1. 用户当前明确指令
2. 当前 Execution Mode
3. 系统默认业务规则

用户可以在当前任务中明确覆盖默认 Mode 行为。

### 3.4 长期流程偏好

当前版本不处理“以后都这样做”等长期偏好。

Mode 仅作为当前 Session 的执行策略。

---

## 4. Auto Approval

### 4.1 定义

Workflow Mode 下可开启 Auto Approval。

开启后：

- Approval Gate 自动通过。
- Workflow 不因普通 Approval 暂停。

### 4.2 Decision Gate

Auto Approval 不能替用户做需要明确选择的决定，例如：

- 是否修改历史事实
- 冲突时选择哪一种处理方式
- 多个无法推断的目标中选择哪一个

Decision Gate 始终需要用户输入。

### 4.3 作用范围

Auto Approval 仅作用于当前 Session。

新 Session 默认关闭。

### 4.4 当前等待 Approval 时开启

如果 Workflow 当前正在等待 Approval，用户开启 Auto Approval：

- 当前 Approval 自动通过。
- Workflow 继续执行。

---

## 5. Workflow Exit

用户可以在 Workflow 等待确认或执行过程中明确退出当前 Workflow。

退出：

- 终止当前 Workflow。
- 不结束 Session。
- 不关闭 Project。
- 不回滚已经 Commit 的正式内容。
- 未 Commit 的 Candidate 不进入 Project。

如果其他任务依赖被退出的 Workflow：

- 依赖任务取消或保持阻塞。
- 无依赖任务可以继续。

---

## 6. 核心业务原则

### 6.1 Project 是正式 Source of Truth

事实优先级：

正文 > 大纲 > 章节概述 > 普通附件或临时资料。

### 6.2 State 不等于内容

State 主要描述：

- 当前执行什么
- 执行到哪里
- 等待什么
- 关联哪些 Artifact

### 6.3 Artifact 不等于正式内容

Artifact 可以修改、被替代、被批准或被放弃。

只有 Commit 后才成为 Project 正式内容。

### 6.4 Approval 不等于 Commit

某个 Artifact 被批准，不代表已经写入 Project。

### 6.5 Commit 不重新生成内容

Commit 应应用用户已经看到并确定的 Artifact。

如果用户要求修改后再 Commit，应先产生新 Artifact，再执行 Commit。

### 6.6 用户指令优先

用户明确要求直接编辑、直接生成正文、跳过大纲、进入完整 Workflow 或只返回内容不写 Project，均优先于默认模式。

### 6.7 不自动级联修改

修改、删除、合并、移动章节后，如果影响其他章节：

- Agent 可以提示风险。
- 未经用户明确要求，不自动修改其他章节。

### 6.8 小说完成由用户判断

Agent 不自动判断小说已完成。

---

## 7. 复杂指令

Agent 需要支持包含多个任务的自然语言指令。

每个子任务至少识别：

- 操作类型
- 操作对象
- 操作范围
- 用户约束
- 参考内容
- 是否修改 Project
- 与其他任务的依赖关系

默认按用户表达顺序执行。

后续任务依赖某个尚未确认或尚未完成的任务时，后续任务等待。

独立任务可以继续执行。

---

## 8. 从 0 创作

### 8.1 未指定章节数

默认按单章节处理。

### 8.2 单章节 Workflow

1. 生成章节概述 Artifact。
2. 单独确认概述，批准该精确 revision。
3. 根据已批准概述生成章节大纲 Artifact。
4. 单独确认大纲，批准该精确 revision。
5. 根据已批准概述和大纲生成正文 Artifact，并审阅。
6. 确认正文。
7. Commit 到 Project。

概述、大纲阶段的拒绝或反馈只修订当前阶段；上游修改使下游候选失效。用户手动修改已批准的上游内容时进入 Decision，明确选择后回到相应阶段重新确认。当前 draft 绑定不保留失效下游内容，历史 Artifact 仍可查看。概述和大纲确认保留同一章节 draft，正文确认或成功 Commit 后删除；Approval 本身不等于 Commit。已有未标记版本的旧 Workflow 按原合并确认流程恢复，新 Workflow 使用分阶段确认。

### 8.3 多章节 Workflow

1. 一次生成所有章节概述。
2. 用户确认全部章节概述。
3. 逐章生成章节大纲并依次确认。
4. 所有大纲确认后逐章生成正文。
5. 逐章确认正文。
6. 按 Workflow 规则 Commit。

Freeform Mode 下，用户可以不进入上述流程。

---

## 9. 续写

“续写”默认表示创建当前最后章节之后的下一章节。

Workflow Mode 下：

1. 读取当前 Project 和必要背景。
2. 生成新章节概述。
3. Approval 概述。
4. 生成大纲。
5. Approval 大纲。
6. 生成正文并审阅。
7. Approval 正文。
8. Commit。

如果用户要求继续已有章节：

- 识别为章节修改，而非新增下一章。
- 按当前 Mode 和用户明确指令决定 Freeform 编辑或 Workflow 重写。

---

## 10. 补写缺失章节

支持根据前后现有章节补写缺失章节。

Agent需要：

- 读取后续或前后相关章节
- 读取正式人物、世界观、连续性约束
- 分析已有事实
- 反推缺失章节需要满足的剧情条件
- 生成目标章节内容
- 避免与已有正式事实产生明显冲突

Workflow Mode 下进入标准创作流程。

---

## 11. 章节修改

### 11.1 Freeform 修改

Freeform Mode 默认不强制重新生成概述和大纲。

支持：

- 修改结尾
- 删除段落
- 替换句子
- 增加文字
- 移动内容
- 局部扩写

Agent 可以产生候选 Artifact，或在用户明确授权直接编辑时修改 Project。

### 11.2 Workflow 修改

用户明确要求重新设计、重写章节或当前为 Workflow Mode 时，可进入创作式修改：

1. 新概述
2. Approval 概述
3. 新大纲
4. Approval 大纲
5. 新正文并审阅
6. Approval 正文
7. Commit

### 11.3 文本定位

支持通过章节编号、章节标题、段落位置、相对位置、明确文本、文本锚点和当前对话上下文定位目标。

若无法唯一定位，需要用户明确。

---

## 12. 章节结构操作

当前范围包括：

- 新增章节
- 插入章节
- 删除章节
- 合并章节
- 拆分章节
- 调整章节顺序
- 修改章节标题

结构操作可能影响后续剧情时：

- 提示风险
- 不自动级联改写

具体重编号规则后续设计。

---

## 13. Project 导入

### 13.1 支持格式

- `.txt`
- `.md`
- `.pdf`

### 13.2 PDF

只要求提取可直接获得的文字，不支持 OCR。

无有效文字时不进入 Project 转换。

### 13.3 章节识别

- 有可识别标题时按标题拆章。
- 无标题时整篇作为一个章节。
- 不根据语义自动拆章。

章节标题识别规则后续设计。

### 13.4 导入 Workflow

1. 提取正文
2. 识别章节
3. 整理正式正文
4. 一次生成全部章节概述
5. 用户确认
6. 每 10 章一批生成大纲
7. 用户逐批确认
8. 完成 Project 初始化

---

## 14. 对话附件

### 14.1 支持格式

- `.txt`
- `.md`

### 14.2 默认语义

对话附件：

- 可被读取和分析
- 可作为创作上下文
- 不自动进入 Project
- 不自动覆盖 Project 正式事实

### 14.3 多附件

支持一个任务同时读取多个附件。

Agent 根据用户指令、文件内容和 Project 当前状态判断附件作用。

### 14.4 与 Project 冲突

附件和 Project 正式事实冲突时：

- Agent 应指出冲突。
- 不自动覆盖 Project。

---

## 15. 翻译与文本转换

支持：

- 翻译
- 叙事视角转换
- 文风调整
- 表达优化
- 扩写
- 缩写

对象可以是：

- 整章
- 局部文本
- Artifact
- 对话附件

如果用户只要求查看结果，不修改 Project。

如果明确要求更新 Project，可直接编辑或 Commit 对应 Artifact。

---

## 16. 查询、分析和讨论

支持：

- 章节查询
- 人物查询
- 人物关系分析
- 剧情总结
- 伏笔分析
- 创作讨论
- 创作建议
- 附件分析

这类任务本身不修改 Project。

---

## 17. 字数规则

### 17.1 用户指定章节字数

正文字符数控制在目标值 ±10%。

### 17.2 统计范围

包括中文字符、标点、英文字母、数字和其他正文字符。

### 17.3 仅给总字数

由 Agent 按章节合理分配，各章节按其规划目标执行 ±10%。

### 17.4 未给字数

Agent 自行规划，不存在固定验收基准。

---

## 18. State 模型需求

### 18.1 Session State

需要记录：

- Session 与 Project 关联
- Execution Mode
- Auto Approval
- 活跃 Task
- 活跃 Workflow
- Pending Gate
- 最近 Workflow 结果
- 当前附件引用

### 18.2 Task State

Task 至少支持：

- PENDING
- RUNNING
- WAITING_APPROVAL
- WAITING_DECISION
- BLOCKED
- COMPLETED
- CANCELLED
- FAILED

### 18.3 Workflow State

需要记录：

- Workflow 类型
- 当前阶段
- 当前状态
- 当前 Artifact 引用
- Pending Gate
- Workflow 结果

大块正文、大纲等内容不直接作为 State 保存。

---

## 19. Artifact 模型需求

### 19.1 Artifact 类型

至少支持：

- SUMMARY
- OUTLINE
- MANUSCRIPT
- REWRITE
- TRANSLATION
- PATCH
- ANALYSIS
- CONTEXT_SUMMARY
- REVIEW_REPORT

### 19.2 Artifact 状态

至少支持：

- WORKING
- CANDIDATE
- APPROVED
- COMMITTED
- SUPERSEDED
- DISCARDED

### 19.3 Revision

同一个工作产物允许存在连续修订。

Revision 用于当前协作过程，不等同于 Project 历史版本控制。

### 19.4 Scope

Artifact 需要明确目标范围，例如 Project、Chapter、Section、Paragraph、Text Range。

### 19.5 Dependency

Artifact 可以记录其基于的 Artifact、Project 内容和附件，以识别后续内容是否建立在旧版本基础上。

---

## 20. Commit 模型需求

### 20.1 定义

Commit 是将 Artifact 或明确 Change 正式应用到 Project 的业务操作。

### 20.2 一次 Commit

一次 Commit 可以包含：

- 一个 Artifact
- 多个 Artifact
- 多个章节 Change
- 结构操作

### 20.3 Commit 生命周期

至少包含：

- PREPARED
- VALIDATING
- APPLYING
- COMPLETED

异常状态：

- FAILED
- CANCELLED

### 20.4 Freshness Check

正式写入前，需要确认目标 Project 内容没有在 Artifact 生成后发生非预期变化。

如果发生变化：

- 不直接覆盖。
- 进入 Decision Gate。

### 20.5 原子性

同一次 Commit 涉及多个正式修改时，应避免 Project 停留在半完成状态。

### 20.6 Commit 后

需要：

- 更新 Artifact 状态
- 更新 Task / Workflow 状态
- 重新生成必要的 Derived View
- 产生 Commit Result

Commit 不一定意味着 Task 完成。

---

## 21. Approval 与 Decision

### 21.1 Approval Gate

回答“是否允许继续”。

用户可以：

- Approve
- Revise
- Reject
- Exit

Auto Approval 可以自动处理。

### 21.2 Decision Gate

用于无法由系统自动决定的多选问题，例如冲突处理、目标无法唯一定位或 Project 已变化。

Auto Approval 不处理 Decision Gate。

---

## 22. Project 文件结构

推荐结构：

```text
novel-project/
├── PROJECT.md
├── MANUSCRIPT.md
├── chapters/
│   ├── 0001.md
│   ├── 0002.md
│   └── ...
├── sources/
├── references/
└── .novel-agent/
    ├── project.json
    ├── state.json
    ├── sessions/
    ├── drafts/
    ├── cache/
    └── tmp/
```

每个章节文件包含：

- 章节概述
- 章节大纲
- 正文

`chapters/` 是正式章节内容。

`MANUSCRIPT.md` 是自动聚合正文，不是 Source of Truth。

`.novel-agent/` 保存内部状态、Artifact、Session 等非正式内容。

---

## 23. 辅助资料

`references/` 可保存：

- 人物资料
- 世界观
- 地点资料
- 时间线
- 连续性记录
- 创作约束
- 调研资料

辅助资料不高于正文正式事实。

---

## 24. 上下文记忆

当前版本主要包括：

- 当前 Session 对话
- 当前任务状态
- 当前 Workflow 状态
- 当前 Artifact 引用

新 Session 不继承旧对话记忆，重新通过 Project 建立正式小说上下文。

---

## 25. 异常与边界

需要覆盖：

- 用户引用不存在章节
- 文本锚点不存在
- 文本锚点多次匹配
- 对话附件为空或无法读取
- PDF 无有效文字
- 附件和 Project 冲突
- 用户修改了 Artifact 所基于的 Project 内容
- 子任务失败导致依赖任务无法继续
- Workflow 中途退出
- Workflow 等待确认期间插入普通聊天
- 多任务同时存在不同状态

---

## 26. 当前不在范围内

当前暂不要求：

- Project 历史版本管理
- 自动回滚历史 Commit
- 多版本 Diff
- 多用户同时编辑冲突合并
- OCR
- 跨 Session 用户长期记忆
- 长期流程偏好
- Agent 自动判断小说结束

---

## 27. 待设计项

1. 章节标题识别规则
2. 章节插入、删除、合并、拆分后的编号规则
3. Approval 自然语言识别细则
4. Decision Gate 通用表达规则
5. Artifact 持久化生命周期
6. Working Artifact 清理规则
7. 对话附件大小与数量限制
8. Project Freshness 判断规则
9. Complex Task 部分失败后的细节策略
10. 辅助资料标准化格式

---

## 28. 核心验收原则

1. Freeform 下生成内容不会自动写入 Project。
2. 用户明确 Commit 后才将指定 Artifact 正式写入。
3. 用户明确直接编辑时无需强制进入 Workflow。
4. Workflow Mode 下创作型任务默认进入标准流程。
5. Auto Approval 只自动通过 Approval Gate。
6. Decision Gate 始终需要用户选择。
7. 用户可退出 Workflow，已 Commit 内容保留，未 Commit 内容不写入。
8. 普通聊天不会破坏等待中的 Workflow。
9. Project 正文事实优先于大纲、概述和附件。
10. Workflow 与 Freeform 共用统一 Artifact / Commit 模型。
11. Subtask 依赖尚未完成时不能错误继续。
12. Commit 前需要检查目标 Project 的最新状态。
13. Commit 不重新生成用户已经确定的 Artifact。
14. 删除、合并等结构操作不自动改写其他章节。
15. 新 Session 可通过 Project 恢复正式小说上下文。

---

## 29. 当前产品定义

小说 Agent 是一个以 Project 为正式工作空间、以自然语言作为协作入口的小说工作 Agent。

系统同时支持：

- Freeform 自由协作
- Workflow 标准流程
- Auto Approval 自动流程
- Artifact 工作产物
- Commit 正式落盘
- 复杂任务拆解
- 直接编辑
- 结构操作
- 文件附件
- 分析与创作

标准小说创作流程是 Agent 的一种可调用业务流程，而不是所有任务的唯一执行方式。
