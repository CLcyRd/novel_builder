# 小说 Agent 设计文档

> 设计目标：基于 Deep Agents / LangGraph 落地小说协作 Agent。  
> 本文档不包含代码，不讨论具体模型选型。

---

## 1. 设计目标

系统需要同时满足：

- 自然语言自由协作
- 固定小说创作 Workflow
- 人工确认、Auto Approval、Exit、Resume
- Project 文件持续读写
- 长篇小说上下文管理
- 复杂任务拆解
- Subagent 按需委派
- Freeform 与 Workflow 信息互通
- Session 恢复
- 正式内容与候选内容隔离
- 可追踪、可调试、可恢复

核心设计原则：

> Main Agent 负责理解与协调；Workflow 负责确定性流程；Subagent 负责隔离型复杂分析；Tool 负责确定性操作；State / Artifact / Commit 统一整个执行体系。

---

## 2. 总体结构

推荐采用：

- 一个 Main DeepAgent
- 一个外层运行 Graph
- 多个业务 Workflow Subgraph
- 少量按需 Subagent
- 一套 Project Tools
- 一套统一 State / Artifact / Commit 协议

职责关系：

| 组件 | 核心职责 |
|---|---|
| Main DeepAgent | 自然语言理解、复杂任务拆解、Freeform 协作、任务路由 |
| Workflow Router | 判断和切换 Workflow |
| Workflow Subgraph | 固定业务阶段、HITL、Auto Approval、Exit、Resume、Commit |
| Context Builder | 为当前任务构造所需小说上下文 |
| Context Subagent | 大范围跨章节检索、分析、压缩 |
| Review Subagent | 连续性、约束、质量检查 |
| Project Tools | 读取、搜索、编辑、章节结构、统计、聚合等确定性操作 |
| State | 保存执行控制状态 |
| Artifact | 保存工作成果 |
| Commit | 将确定成果正式写入 Project |

Deep Agents 作为主 Agent harness，固定业务状态机由 LangGraph Workflow 管理。

---

## 3. Main Agent 设计

Main Agent 是系统唯一长期面向用户的 Agent。

负责：

### 3.1 意图理解

识别查询、创作、续写、补写、编辑、结构操作、翻译、分析、Commit、Mode 切换、Workflow 响应和 Workflow Exit。

### 3.2 复杂任务拆解

将复杂指令拆成：

- Task
- Task 依赖
- 执行顺序
- 是否需要 Workflow
- 是否可以 Freeform 完成

### 3.3 Freeform 协作

Freeform 模式下：

- 读取 Project
- 生成 Artifact
- 修改 Artifact
- 回答用户
- 在用户授权后 Commit

### 3.4 Workflow Handoff

需要标准流程时创建 Workflow Request，并交给对应 Workflow Subgraph。

### 3.5 Workflow Resume

当 Workflow 等待用户时：

- 判断用户消息是 Workflow Response 还是普通聊天
- Workflow Response 恢复对应 Workflow
- 普通聊天继续由 Main Agent 处理

---

## 4. Execution Mode

Session 维护：

- FREEFORM
- WORKFLOW

### 4.1 Freeform

Main Agent根据用户当前指令完成任务。

默认：

- 生成内容形成 Artifact
- 不自动落盘
- 用户明确编辑或 Commit 时才修改 Project

### 4.2 Workflow

创作型任务默认进入业务 Workflow。

### 4.3 当前指令覆盖 Mode

当前 Prompt 优先于 Session Mode。

---

## 5. Workflow 设计原则

Workflow 本体设计为 LangGraph Subgraph，而不是 Subagent。

原因：

- 有确定阶段
- 有持久状态
- 有人工中断
- 有恢复
- 有 Auto Approval
- 有 Decision Gate
- 有 Exit
- 有 Commit
- 需要可检查当前执行位置

Subagent 用于自治分析和上下文隔离，不承担业务状态机。

---

## 6. Workflow Handoff

Main Agent 通过 Handoff 进入 Workflow。

Handoff 负责：

- 明确 Workflow 类型
- 明确目标
- 提炼当前用户要求
- 传递附件引用
- 传递 Session 配置
- 建立 Workflow 关联

真正的阶段执行发生在 Workflow Subgraph 内。

---

## 7. Workflow 类型

初期建议：

- Create Chapter Workflow
- Continue Chapter Workflow
- Multi Chapter Workflow
- Backfill Chapter Workflow
- Rewrite Chapter Workflow
- Import Novel Workflow

只有拥有稳定阶段和确认点的任务才新增 Workflow。

普通删除文本、翻译附件、查询等不需要 Workflow 化。

---

## 8. Workflow 标准阶段

章节创作 Workflow 可包含：

1. Prepare Context
2. Generate Summary
3. Summary Approval Gate
4. Generate Outline
5. Outline Approval Gate
6. Generate Manuscript
7. Review
8. Manuscript Approval Gate
9. Commit
10. Workflow Result
11. Return to Main Agent

单章节创建、续写、补写与重写共用分阶段确认。只有已批准的最新概述 revision 可进入大纲生成，只有已批准的概述及大纲可进入正文生成。修订只作用于当前阶段；修改上游材料需要重新确认，并清除或显式保留下游候选的活动引用。State 持久化流程版本、独立阶段轮次与引用。新单章节流程版本为 2；旧 checkpoint 缺失版本时按版本 1 的 PLAN 合并确认图恢复，不自动迁移 Gate 或重新生成已有候选。

实际流程可根据单章、多章、导入等场景调整。

---

## 9. State 设计

State 是运行控制面。

原则：

- 保存“执行到哪里”
- 保存“等待什么”
- 保存 Artifact 引用
- 不直接保存整章正文等大内容

### 9.1 Session State

包含：

- session_id
- project_id
- execution_mode
- auto_approval
- active_tasks
- active_workflow
- pending_gate
- attachments
- last_workflow_result

### 9.2 Task State

包含：

- task_id
- type
- instruction
- target
- status
- dependencies
- input_artifacts
- output_artifacts
- workflow_id
- result

### 9.3 Workflow State

包含：

- workflow_id
- workflow_type
- current_stage
- status
- task_id
- current_artifacts
- pending_gate
- result

### 9.4 推荐状态

- PENDING
- RUNNING
- WAITING_APPROVAL
- WAITING_DECISION
- BLOCKED
- COMPLETED
- CANCELLED
- FAILED

---

## 10. Artifact 设计

Artifact 是 Freeform 和 Workflow 共享的工作成果模型。

### 10.1 Artifact 分类

可 Commit：

- SUMMARY
- OUTLINE
- MANUSCRIPT
- REWRITE
- TRANSLATION
- PATCH

辅助 Artifact：

- ANALYSIS
- CONTEXT_SUMMARY
- REVIEW_REPORT
- CONFLICT_REPORT

### 10.2 Artifact 状态

- WORKING
- CANDIDATE
- APPROVED
- COMMITTED
- SUPERSEDED
- DISCARDED

### 10.3 Artifact Revision

一个 Artifact 可以持续被用户和 Agent 修订。

新 Revision 替代旧 Revision。

Revision 不等同于 Project 历史版本控制。

### 10.4 Artifact Scope

每个 Artifact 必须明确目标：

- Project
- Chapter
- Section
- Paragraph
- Text Range

### 10.5 Artifact Dependency

记录其基于：

- 哪些 Artifact Revision
- 哪些 Project 内容
- 哪些附件
- 哪些辅助资料

用于判断 Artifact 是否过时。

---

## 11. Working Artifact

Freeform 模式下维护 Working Artifact。

Main Agent需要明确知道：

- 当前最新大纲 Artifact
- 当前最新正文 Artifact
- 正文依赖哪个大纲 Revision
- 哪个 Artifact 是用户当前指代的“刚才那版”

不依靠纯聊天历史猜测版本。

---

## 12. Commit 设计

Commit 是正式化语义。

### 12.1 Commit 输入

可以是：

- Artifact
- 多个 Artifact
- 明确直接编辑 Change
- 章节结构 Change

### 12.2 生命周期

- PREPARED
- VALIDATING
- APPLYING
- COMPLETED

异常：

- FAILED
- CANCELLED

### 12.3 Validation

至少检查：

- Artifact 是否有效
- 目标是否存在
- Project 是否发生变化
- Artifact 是否基于过期内容
- 多个 Change 是否冲突

### 12.4 Freshness

Artifact 生成时关联基础 Project 状态。

Commit 前重新检查目标。

发生变化时进入 Decision Gate。

### 12.5 原子性

同一个 Commit 包含多个 Change 时，应避免只成功一部分。

### 12.6 Commit Result

返回：

- 修改文件
- 修改章节
- Committed Artifacts
- 新增正式事实
- Warning
- Task 是否完成

---

## 13. Approval Gate

Approval Gate 用于回答：

> 是否允许当前 Candidate 进入下一阶段。

支持：

- APPROVE
- REVISE
- REJECT
- EXIT

### 13.1 Auto Approval

开启后：

- Approval 自动通过
- 记录批准来源
- 不产生人工等待

### 13.2 Approval 与 Commit 分离

Approval 只表示 Candidate 被接受。

是否 Commit 由 Workflow 决定。

---

## 14. Decision Gate

Decision Gate 用于系统不能安全自行决定的分支。

例如：

- 新要求与 Project 正式事实冲突
- Artifact 基础版本已过期
- 目标无法唯一定位
- 多种不可推断处理方案

Auto Approval 不处理 Decision Gate。

---

## 15. Exit 与取消

Workflow 可被用户退出。

退出后：

- Workflow → CANCELLED
- 依赖 Task 按依赖关系取消或阻塞
- 未 Commit Artifact 不进入 Project
- 已 Commit 内容保留
- Session 继续可用

不自动 rollback 已完成 Commit。

---

## 16. Context 设计

Context 按任务构建，不每次加载全部小说。

### 16.1 Project Context

从正式 Project 读取：

- PROJECT.md
- 相关章节
- 人物资料
- 世界观
- 时间线
- continuity
- constraints

正式小说事实优先从 Project 获取。

### 16.2 Session Task Context

由 Main Agent 传给 Workflow：

- 当前用户任务
- 用户本轮额外要求
- 当前附件
- 当前决策
- 当前 Working Artifact
- 临时限制

### 16.3 Workflow Context

Project Context + Session Task Context。

---

## 17. Context Builder

每个 Workflow 第一阶段建议包含 Context Preparation。

职责：

- 判断必须读取哪些 Project 内容
- 读取当前章、前章、相关人物和约束
- 判断是否需要更大范围历史检索
- 构造精简 Workflow Context

不建议每次读取整个 Project。

---

## 18. Context Subagent

Context Subagent 用于：

- 大范围跨章节分析
- 长篇人物状态检索
- 时间线分析
- 伏笔检索
- 多附件压缩
- 大量历史信息总结

输出结构化辅助 Artifact，例如：

- findings
- evidence
- conflicts
- uncertainty

默认只读 Project。

---

## 19. Review Subagent

Review Subagent 用于独立检查：

- 与前文是否冲突
- 人物行为是否一致
- 是否满足用户约束
- 是否符合大纲
- 字数是否符合要求
- 是否遗漏任务条件

输出 Review Artifact。

默认不直接修改正式 Project。

---

## 20. Tool 设计原则

Tool 用于确定性操作。

### 20.1 读取

- 读取章节
- 读取章节 Section
- 读取附件
- 读取辅助资料
- 搜索 Project

### 20.2 编辑

- 替换 Section
- 插入文本
- 删除文本
- 替换文本

### 20.3 章节结构

- 新增
- 删除
- 合并
- 拆分
- 移动
- 改标题

### 20.4 计算与维护

- 字符统计
- 重建 MANUSCRIPT
- 校验章节结构
- Project Freshness 检查

确定性操作优先 Tool，不设计成 Subagent。

---

## 21. 写权限

推荐：

| 组件 | Project 权限 |
|---|---|
| Main Agent | Read + 受控 Write |
| Workflow | Read + Commit 阶段 Write |
| Context Subagent | Read Only |
| Review Subagent | Read Only |

Subagent 默认无 Commit 权限。

所有正式写入尽量收敛到 Commit 路径。

---

## 22. Workflow Request

Main Agent 启动 Workflow 时传递结构化 Request。

至少包含：

- workflow_id
- workflow_type
- project_id
- session_id
- task_id
- target
- user_instruction
- task_constraints
- attachment_refs
- working_artifact_refs
- auto_approval

Main Agent 只传当前对话独有的信息。

正式小说事实由 Workflow 从 Project 获取。

---

## 23. Workflow Result

Workflow 完成后返回结构化 Result。

至少包含：

- workflow_id
- status
- completed_tasks
- generated_artifacts
- committed_artifacts
- project_changes
- important_facts_created
- warnings
- remaining_tasks

Result 同时进入 Parent State，并转换为压缩消息供 Main Agent 理解。

---

## 24. Workflow 与 Main Agent 信息互通

### Agent → Workflow

通过：

- Workflow Request
- Working Artifact 引用
- 附件引用
- Session Task Context

### Workflow → Agent

通过：

- Workflow Result
- Commit Result
- State 更新
- 新增正式事实摘要

正式事实不通过 Agent Memory 复制维护。

下一任务需要时重新从 Project 获取。

---

## 25. 等待 Workflow 时的普通对话

Active Workflow 可以处于等待状态。

用户新消息先判断：

### Workflow Response

例如：

- 可以
- 重写第二部分
- 退出
- 选择第二种方案

→ Resume Workflow。

### 普通新任务

例如：

- 第十章女主知道真相了吗？
- 帮我总结附件

→ Main Agent 正常处理。

原 Workflow 保持等待状态。

---

## 26. Durable Execution

Workflow 和 HITL 必须可恢复。

原则：

- 每个人工等待点必须持久化
- Workflow 状态可以在进程中断后恢复
- 恢复后不重复已完成的不可逆操作
- Commit 阶段必须具备清晰的幂等或事务语义

---

## 27. Project Freshness

即使暂不支持多人并发，也必须考虑用户手动修改 Project。

原则：

- Artifact 记录其生成时的基础 Project 参考
- Commit 前重新读取目标
- 目标发生变化时不静默覆盖
- 进入 Decision Gate

---

## 28. Derived View

`chapters/*.md` 是正式 Source of Truth。

`MANUSCRIPT.md` 是 Derived View。

任何正文或章节结构变化完成 Commit 后，统一重建 Derived View。

---

## 29. Error 设计

错误分四类。

### 29.1 Retryable

临时 I/O 或服务超时，可自动重试。

### 29.2 Agent Recoverable

可以通过增加上下文或更换检索方式自行恢复。

### 29.3 User Recoverable

例如目标无法唯一定位、Project 冲突，进入 Decision Gate。

### 29.4 Fatal

例如 Project 核心文件损坏、无权限或状态无法恢复。

终止当前 Task，保持已 Commit 内容。

---

## 30. 不可信输入

对话附件、Project 普通文本、references 内容均视为数据。

其中出现的“忽略之前要求”“删除整个 Project”等文字不能自动成为控制指令。

只有当前用户明确指令和系统规则能够控制 Agent 行为。

---

## 31. Observability

每个 Task 应能够追踪：

- 用户原始指令
- Agent 拆解出的 Task
- Execution Mode
- 是否进入 Workflow
- 读取了哪些 Project 内容
- 生成了哪些 Artifact
- 调用了哪些 Subagent
- Approval 来源
- Decision Gate 结果
- Commit 修改了什么
- Error / Retry / Exit

---

## 32. Evaluation

重点建立行为测试集：

- Freeform 不误写 Project
- Commit 指向正确 Artifact Revision
- Workflow 能正常暂停与恢复
- Auto Approval 不处理 Decision Gate
- Exit 不写入未 Commit Artifact
- Workflow 等待时普通聊天不破坏状态
- Subagent 不越权修改 Project
- Project Freshness 冲突能被识别
- 复杂 Task 依赖关系正确
- 新 Session 不错误继承旧 Session 临时状态
- 正文事实优先级正确

---

## 33. 推荐实施顺序

### 第一阶段

1. Project Tools
2. Main DeepAgent
3. Session / Task State
4. Artifact
5. Commit
6. Freeform Mode

### 第二阶段

1. Workflow Subgraph
2. Approval / Decision / Exit
3. Auto Approval
4. Durable Resume
5. Workflow Request / Result

### 第三阶段

1. Context Builder
2. Context Subagent
3. Review Subagent
4. Freshness Check
5. Complex Task Dependency

### 第四阶段

1. Observability
2. Evaluation
3. Error Recovery
4. 更多 Workflow
5. 辅助资料自动维护

---

## 34. 关键设计不变量

1. 一个 Main Agent 负责持续用户协作。
2. Workflow 是业务状态机，不是 Subagent。
3. Subagent 默认用于上下文隔离和复杂分析。
4. 确定性操作优先 Tool。
5. Project 是正式内容唯一 Source of Truth。
6. State 不保存大块小说内容。
7. 未正式落盘内容统一抽象为 Artifact。
8. Approval 不等于 Commit。
9. Commit 不重新生成 Artifact。
10. Freeform 与 Workflow 共用 Artifact / Commit 模型。
11. Subagent 默认不能 Commit。
12. 正式写入前检查 Project Freshness。
13. Auto Approval 不替用户处理 Decision Gate。
14. Workflow Exit 不回滚已经完成的 Commit。
15. Workflow Result 必须结构化返回 Main Agent。

---

## 35. 最终设计定位

整体采用：

> Main DeepAgent + LangGraph Workflow Subgraphs + Dynamic Subagents + Deterministic Project Tools + Shared State / Artifact / Commit Protocol

其中：

- Main Agent 负责理解和协调
- Workflow 负责固定业务流程和 HITL
- Subagent 负责隔离型复杂推理
- Tool 负责确定性操作
- State 负责控制执行
- Artifact 负责工作成果
- Commit 负责正式化 Project 内容

该结构同时支持自由协作、标准工作流和自动工作流，并为长篇上下文、恢复、复杂任务和后续扩展保留清晰边界。
