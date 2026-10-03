# ADR 0006：核心函数职责拆分

状态：已实施，2026-10-02。

## 问题与范围

Freeform `run()` 同时处理草稿确认、输入读取、工具定义、模型流、完成检查和结果保存。单章与批量 Workflow 在 `_build()` 内定义数十个业务闭包。Commit `_render()` 将 Artifact 校验、内容修改、章节结构维护和写入序列化放在一个分支树中。CLI `_command()` 和 Main Agent `_request()` 也混合了多个处理阶段。

本次按职责提取组件，保留现有业务规则、公开调用入口、磁盘格式和持久化协议。继续复用 ADR 0004 的事务恢复边界与 ADR 0005 的操作内读取快照。

## Freeform

`FreeformAgent.run()` 只编排一次请求：

1. 加载 Project / Session，建立临时 `FreeformRunScope`。
2. `FreeformDraftController` 处理待确认草稿，可直接返回结果。
3. `FreeformInputBuilder` 校验附件、准备 Skill、构造模型消息。
4. `FreeformToolFactory` 构造工具；`FreeformModelRunner` 构造并执行模型图。
5. `FreeformOperationPolicy` 检查请求操作是否实际完成；模型执行器最多补充一次纠正调用。
6. `FreeformRunRecorder` 整理展示、保存成功对话和精确 Artifact 引用。

临时 scope 仅持有本次运行所需的依赖和进度，运行结束后丢弃，不加入 Session 或 Workflow checkpoint。持久化 SessionState 仍由原 StateStore 管理。草稿短路、附件读取、模型构造及异常捕获的原先顺序保持不变。

工具的名称、参数、说明和顺序保持不变。Commit 意图、已展示的精确 revision、章节删除目标及 freshness 仍在工具执行时校验。只读分析子 Agent 保持原四个读取工具，通用子 Agent 仍禁用。模型构造入口保留在 facade，可继续注入或替换。

## Workflow

- `SingleChapterNodes`：单章准备、生成、审批、Decision、修订、Commit 与结束节点。
- `BatchChapterNodes`：多章和导入的对应节点，包括批量阶段推进。
- `WorkflowContextBuilder`：正式证据、邻章、人物参考、附件和工作 Artifact 的上下文构建。
- `single_state.py` / `batch_state.py`：原 TypedDict 状态字段和 Artifact 引用解码。

facade `_build()` 仅声明图的节点和边。服务生命周期、数据库打开/关闭、版本选择、Session 同步以及既有共享草稿协调留在 facade。节点通过明确的服务依赖使用这些操作；没有新增插件注册或动态发现机制。

节点名称、条件边、interrupt 在节点内的位置和执行次序、Gate ID 算法、thread config、SQLite 路径、状态字段、Workflow 版本判断保持原样。旧版 PLAN 与新版 SUMMARY / OUTLINE / MANUSCRIPT 图继续分别构建，不需 checkpoint 迁移。

每次上下文构建仍重新获取 ProjectReadSnapshot，并只在该次操作内复用。下一次读取和 Commit apply 继续重新校验磁盘；模型调用不持 Project 锁。

## Commit

`CommitContentResolver` 读取并验证指定 Artifact revision，校验 Project、scope、section、类型及状态，并生成 COMMITTED 状态的写入载荷。

`ChapterChangeReducer` 按请求顺序修改快照的工作副本。正文替换、插入、删除、重命名和章节创建、删除、移动、拆分、合并均在内存完成；依赖的 ID 由服务传入。Reducer 只依赖领域对象，不读取文件、访问 Artifact 或调用模型，也不修改原快照。

`CommitWritePlanner` 将工作副本转换为确定的文件路径及 bytes / 删除标记，维护文件编号、metadata 和派生 MANUSCRIPT.md。它执行序列化及内容校验，不读写磁盘。

`CommitService` 继续持有 Project 锁，负责 pending 恢复、prepare / apply、freshness、幂等凭据及事务应用。章节目标验证在 Artifact 解析前执行，保持原先错误优先级；多项 Change 的顺序和 Artifact 状态写入也保持原样。

## CLI 与 Main Agent

CLI `_command()` 只解析 shlex 并分发到 `CLICommandHandlers` 的命令族方法。命令处理器负责 Project / Session 选择、控制命令调用和呈现。附件解析、流式展示与普通聊天入口继续在 shell；创作路由仍属于 Main Agent。

Main Agent `_request()` 的步骤为：`accepts_workflow()` → 可选语义计划 → `WorkflowTargetResolver` → `WorkflowRequestBuilder` → 既有 `WorkflowSkillResolver`。目标解析负责操作优先级和章节范围，不创建 Artifact。超过 2,000 字的逐章 brief 仅在请求构造阶段保存为 TASK_BRIEF，随后用引用传入 Workflow。含糊目标仍先进入原路由 Decision，Skill 绑定在请求构造完成后执行一次。

现有 `RunResult`、`TaskContext`、`CLIResult` 导入入口保留兼容导出。业务意图识别规则没有扩展；ADR 0005 记录的否定创作措辞问题仍不属于这次结构重构。

## 结果

下表为函数定义起止行数，包含签名、注释和空白行，用于观察编排函数的规模，不是运行性能指标。

| 函数 | 重构前 | 重构后 | 原内嵌函数数 → 当前 |
| --- | ---: | ---: | ---: |
| FreeformAgent.run | 599 | 43 | 15 → 0 |
| CreateChapterWorkflow._build | 661 | 157 | 34 → 0 |
| BatchChapterWorkflow._build | 637 | 140 | 33 → 0 |
| CommitService._render | 180 | 24 | 0 → 0 |
| NovelCLI._command | 254 | 6 | 0 → 0 |
| MainAgent._request | 131 | 20 | 0 → 0 |

图装配函数保留显式节点和边，避免把可读的状态机配置隐藏在抽象层内。后续修改应在对应节点、工具或纯变更组件中完成；新增持久化字段或改变 interrupt 次序应独立做协议兼容设计。

## 验证

`tests/fixtures/responsibility_contracts.json` 从本次重构前的源码生成，记录两类图的 v1 / v2 节点和边、状态字段、工具 JSON Schema 与说明、聊天事件顺序。新增 `test_responsibility_boundaries.py` 对这些契约做回归，另验证取消/失败不记录成功、完成检查仅纠正一次、工具执行授权、临时 Session 隔离、纯内存规划及长 brief 的构造边界。

另外实际加载重构前源码，在临时 Project 写入等待 checkpoint，再使用新服务恢复并提交，13 个场景通过：

- 单章 v1：PLAN、MANUSCRIPT；单章 v2：SUMMARY、OUTLINE、MANUSCRIPT。
- 批量 v1 与 v2：各 SUMMARIES、OUTLINES、MANUSCRIPT。
- 导入：SUMMARIES、OUTLINES。

恢复时 Gate、候选引用与已完成生成均保留，加载本身不调用生成器。14 组新旧 Commit 渲染对比也通过，涵盖所有 Change 类型、Artifact 内容、章节重排与有序复合修改，输出文件载荷和结果引用一致。

真实模型 `deepseek-flash` 的两个验收场景通过：正式查询 → Artifact 生成 → revision 修订 → 精确 Commit，以及两章概述 → 两章大纲 → 正文 → Commit。均验证实际工具和结构化结果，所有写入仅发生在临时测试 Project。

最终验收：unit / integration 共 252 项通过（包含新增 12 项职责边界回归）；真实模型 2 项通过；Ruff 静态检查与变更文件的格式、差异空白检查通过。
