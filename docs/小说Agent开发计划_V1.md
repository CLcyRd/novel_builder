# 小说 Agent 开发计划 V1

编写日期：2026-09-26  
状态：Phase 1 的 M0–M1.4 已落地并通过阶段测试。初期记录见 [M0–M1.1 验证记录](validation/M0-M1.1.md)；本阶段结果见 [Phase 1 验证记录](validation/Phase1-M1.2-M1.4.md)。

本文件保留最初的仓库勘察快照，并持续记录项目理解、差距分析、代码库分层设计、实施里程碑和验证计划。M0 与 M1.1 已按方案实施；后续继续按 Phase 1 门槛推进。

## 1. 依据与项目理解

已完整阅读：

- [AGENTS.md](../AGENTS.md)
- [小说Agent项目完整需求文档_V2.md](小说Agent项目完整需求文档_V2.md)
- [小说Agent设计文档_DeepAgent.md](小说Agent设计文档_DeepAgent.md)
- [小说Agent_CLI设计文档_V1.md](小说Agent_CLI设计文档_V1.md)

这是一个以本地小说 Project 为正式内容来源、以 Session 为协作上下文的 CLI 产品。Main Agent 负责理解、拆解和协调；固定创作 Workflow 负责阶段推进；Subagent 负责隔离分析；确定性 Service/Tool 负责文件与结构操作。

关键内容流是：读取正式 Project → 生成或修订 Artifact → 明确选择 Revision → 校验并 Commit → 重建派生视图。普通查询没有正式写入副作用；明确直接编辑可以跳过创作 Workflow，但仍走受控 Change/Commit 路径。

### 1.1 规范差异的处理

| 差异或容易误读之处 | 本计划采用的解释 |
|---|---|
| Agent 设计 §33 将 Main Agent 放入第一阶段、Freshness 放入第三阶段 | 按本次用户指令及 AGENTS.md：Phase 1 先完成 Project、Artifact、Commit、Freshness；Phase 2 才接 Main Agent；Phase 3 才做 Workflow |
| 需求称“上传附件”，CLI §7 要求文件已在 Project 内 | V1 聊天附件通过 Project-local `@file` 引用，仅 `.txt/.md`；不实现外部文件上传入口 |
| 导入支持 PDF，聊天附件仅支持文本 | PDF 仅属于 Phase 3 Import Novel；提取已有文字，不做 OCR，不扩展聊天附件格式 |
| CLI §16.4 的自然语言反馈与 §28 的普通聊天 | 由 Main Agent 判断是否为明确反馈；普通问答不修改 Pending Gate，不隐式恢复 Workflow |
| CLI 将 `/resume` 列为 P1，但本次要求进程重启恢复 | Phase 3 必须具备持久恢复能力，Phase 4 必须完成 CLI 恢复入口；不能以 P1 为由推迟阶段验收 |
| “批准”与“提交” | APPROVED 只允许进入下一阶段；实际正式写入由独立 Commit 完成 |

以上是执行优先级的明确说明，不修改原始设计文档。

## 2. Repository reconnaissance：当前事实

### 2.1 当前目录

```text
G:\novel_builder\
├── AGENTS.md
├── .env
├── src/                          # 空目录
└── docs/
    ├── 小说Agent项目完整需求文档_V2.md
    ├── 小说Agent设计文档_DeepAgent.md
    ├── 小说Agent_CLI设计文档_V1.md
    └── 输入文件案例/
        ├── 忆之罪.md
        ├── 回声镇的最后一记钟声.txt
        └── The ClockWork Prison.pdf
```

这是本次新增计划文档之前的目录快照。`rg --files` 与隐藏文件检查未发现源码、测试、依赖清单、锁文件、README、`.env.example` 或 `.gitignore`。`git status` 返回“not a git repository”，当前目录尚未初始化 Git。

### 2.2 十项接手结论

| 检查项 | 结论 |
|---|---|
| 1. 仓库结构 | 三份规范、AGENTS.md、三种格式的样例、`.env`、空 `src/` |
| 2. 技术栈、运行和包管理 | 项目尚未选定，没有可运行入口或包管理配置；宿主可调用 Python 3.13.2、uv 0.11.21 |
| 3. 已实现能力 | 尚无产品代码；已有需求与设计材料、样例和模型配置 |
| 4. 未实现能力 | Project、Session、Artifact、Commit、Agent、CLI、Workflow、恢复、测试均未实现 |
| 5. 当前 Phase | Phase 1 准备阶段，不能称为 Phase 1 实现完成 |
| 6. 测试体系 | 无 `tests/`、测试配置、测试脚本或 CI；尚无项目 deterministic/integration 测试可运行 |
| 7. `.env` 现状 | 两个聊天模型和一个 Embedding 模型均有 ID、Base URL、非空 key 字段；Grok 另有 reasoning effort；尚无配置加载代码和显式默认模型配置 |
| 8. 真实模型连通性 | 本轮对两个聊天模型完成独立短请求，均获得有效响应；尚未验证 Project 集成、工具调用和流式行为 |
| 9. 下一步最小交付 | 技术栈确认后，先交付 Project create/open、初始 Session metadata、目录验证、章节格式和路径安全测试 |
| 10. 阶段验证 | 分离 unit、integration、real_llm；Phase 1 最终必须走通 Artifact revision → exact revision Commit → reopen → 派生视图，并通过真实 Project 文本模型 smoke |

宿主 Python 中可发现 `langgraph`、`langchain_openai`、`dotenv`、`pytest`、`pydantic`、`rich`；未发现 `deepagents`、`prompt_toolkit`。这些是宿主环境事实，不是项目已安装或已锁定依赖的证据。`py` 启动器未登记 Python 安装，后续应使用 uv 管理项目解释器和隔离环境。

### 2.3 配置与本轮连通性证据

仅检查变量名、是否非空及非敏感模型字段，未输出密钥，也未修改 `.env`。

| 模型用途 | 环境变量 | 当前非敏感配置 | 本轮结果 |
|---|---|---|---|
| DeepSeek 聊天 | `DEEPSEEK_MODEL_ID`、`DEEPSEEK_MODEL_API_KEY`、`DEEPSEEK_MODEL_BASE_URL` | `deepseek-flash`；`https://api.deepseek.com/` | HTTP 200；非空文本；finish_reason=`stop`；约 1.28 秒 |
| Grok 聊天 | `GROK_MODEL_ID`、`GROK_MODEL_API_KEY`、`GROK_MODEL_BASE_URL`、`GROK_MODEL_REASONING_EFFORT` | `grok-4.7`；`https://api.x.ai/v1`；`medium` | HTTP 200；非空文本；finish_reason=`stop`；约 6.38 秒 |
| Embedding | `EMBEDDING_MODEL_ID`、`EMBEDDING_MODEL_API_KEY`、`EMBEDDING_MODEL_BASE_URL` | `qwen3.7-text-embedding-flash`；已配置 endpoint | 未调用；不是 Phase 1 必要条件 |

连通性探针通过现有 Python 的 dotenv 读取配置，向各自配置的 `/chat/completions` 地址发送一条短虚构小说片段及一句概括请求。两个返回的模型标识与请求一致。探针没有创建源码文件、安装依赖、发送样例小说或修改正式内容。

**证据边界：这证明当前凭证、模型和网络可用于短文本调用，不证明框架集成、工具调用、长上下文或流式功能可用，也不等于 Phase 1 real-model gate 通过。** 正式 gate 必须使用实现后的 Project API 读取测试章节，并通过项目的 ModelGateway 调用。

当前没有凭证缺失阻塞；主要待办是技术方案确认和实现。未来若 key 失效、网络或模型不可用，报告具体 gate 被阻塞，不能用 mock 或 skipped 替代通过。

## 3. Gap analysis 与需求归属

所有能力当前均为未实现，以下按交付阶段分配，避免只列功能而遗漏关键语义。

| 能力组 | 必须覆盖的需求 | 交付阶段 |
|---|---|---|
| Project 正式存储 | create/open/load、metadata、章节三段结构、sources/references、章节顺序、MANUSCRIPT 派生视图 | Phase 1 |
| 确定性操作 | read/search、章节及 Section 修改、文本锚点、增加/插入/删除/合并/拆分/移动/改标题、字符统计 | Phase 1，拆成多个小交付 |
| Session 基础 | Project 归属、多 Session 元数据、Mode/Auto/模型选择、稳定引用 | Phase 1；对话和执行恢复在 Phase 2–4 |
| Artifact/Commit | 类型与范围、不可变 Revision、依赖快照、精确版本提交、批准与写入分离、冲突和故障恢复 | Phase 1 |
| Freeform Main Agent | 意图、生成与修订、直接编辑、自然语言 Commit、转换与查询、任务依赖基础 | Phase 2A |
| CLI | Project/Session 管理、附件、Markdown/streaming、事件、模型切换、Artifact/Commit、取消与帮助 | Phase 2B |
| 长篇上下文 | 按任务取上下文、证据定位、预算与压缩、只读 Context/Review Subagent | Phase 2 建基础；Phase 3 创作上下文与 Review；Phase 4 综合验证 |
| 创作 Workflow | 六种子图、Request/Result、Approval/Decision/Auto、反馈、退出、持久恢复 | Phase 3 |
| 特殊创作规则 | 多章先全部概述再逐章大纲再正文；导入按标题拆章、无标题整篇一章、每 10 章一批大纲；字数目标 ±10% | Phase 3 |
| 端到端协作 | 等待时聊天、正确 Gate 恢复、Session reopen、结果回传、模型切换、冲突后决策 | Phase 4 |

依据：需求 V2 §8–20、§22–25；Agent 设计 §9–12、§16–28；CLI §4–23、§28、§41–46。

Embedding 配置不意味着必须立即上向量库。先以章节索引、确定性搜索和按需上下文满足可解释检索，再根据长篇评测决定是否增加 Embedding 检索。

## 4. 已确认技术方案

### 4.1 最小技术栈

| 部分 | 建议 | 选择理由与引入时间 |
|---|---|---|
| 语言 | Python 3.13，项目 `.venv` | 与现有可用解释器一致；M0 验证依赖兼容性后锁定，不依赖宿主全局包 |
| 包管理 | uv + `pyproject.toml` + `uv.lock` | 明确运行入口、依赖分组和可复现安装 |
| 数据协议 | Pydantic，标准库 Enum/typing/pathlib | 验证持久化 schema 和服务输入，隔离业务与框架状态 |
| Project 存储 | UTF-8 Markdown + JSON 元数据 | 正文可直接检查，沿用需求规定目录，避免数据库与正文双重真相 |
| 事务支撑 | 本地写锁、staging、持久事务日志、单文件替换 | 支持多文件 Commit 的失败恢复和幂等，Phase 1 即验证 |
| 模型适配 | python-dotenv + 薄 ModelGateway；初期使用兼容接口适配 | 复用现有 DeepSeek/Grok 配置；Phase 1 只做调用，Phase 2 接工具和流式 |
| Main Agent | Deep Agents Python SDK | Phase 2 引入，承接规划、上下文和按需 Subagent |
| Workflow | LangGraph 外层运行图 + 业务 Subgraph | Phase 3 引入，明确节点、Gate、持久化和恢复边界 |
| Checkpoint | 本地 SQLite checkpointer，位于 Project 内部目录 | Phase 2 支持 Agent Session 执行持久化，Phase 3 支持 Workflow；不替代正式 Markdown |
| CLI | prompt_toolkit + Rich + argparse 启动入口 | Phase 2 引入交互输入、Ctrl+C、Markdown/streaming；不增加 Web 服务 |
| 测试与静态检查 | pytest、必要时 pytest-asyncio、Ruff | 分离无网络测试与真实模型测试，按阶段增加覆盖 |
| PDF 导入 | pypdf 文字提取 | Phase 3 才引入；无文字则停止转换，不做 OCR |

精确依赖版本在 M0 兼容性验证后写入锁文件，不在计划阶段假设最新版本一定兼容。Deep Agents 内置通用文件工具必须受限；正式 Project 的写入只能经本产品 CommitService。禁用会绕过服务的任意文件写入、shell 执行和跨 Session 长期记忆。

框架能力核对依据：Deep Agents 基于 LangGraph，提供工具、上下文及委派能力，见 [Deep Agents 官方说明](https://docs.langchain.com/oss/python/deepagents/overview)；图状态持久化与人工中断分别见 [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 和 [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。这些能力用于承载协议，不能代替本产品的 Artifact/Commit 业务规则。

### 4.2 代码库分层与依赖方向

```text
CLI（终端输入 / 渲染 / 控制命令）
  ↓ Application Facade
运行协调层（Session / Run / Task / Event / Cancel）
  ├── Main DeepAgent（意图 / Freeform / 路由）
  └── LangGraph Workflow Subgraphs（阶段 / Gate / Resume）
          ↓ 工具适配器与应用服务接口
ProjectService / ArtifactService / CommitService / SessionService
          ↓
领域协议（ID / Ref / Change / Snapshot / Result / Error）
          ↑ 由端口连接
基础设施（Markdown / JSON / Lock / Journal / Checkpoint / Provider）
```

依赖约束：

1. `domain` 不导入 CLI、Deep Agents、LangGraph、模型 SDK 或文件存储实现。
2. `services` 使用领域协议和存储端口，不执行模型推理，不渲染终端。
3. `infrastructure` 实现端口；组装入口注入实现，避免服务层依赖具体文件类。
4. `agent`、`workflows` 调用同一组服务；受控写操作最终进入 CommitService。
5. `cli` 只调用应用接口，不判断续写/重写、小说事实冲突或候选内容合理性。
6. Context/Review Subagent 仅获得读取与辅助 Artifact 输出能力，不获得正式写权限。
7. `runtime` 区分事件流与内容流；State 引用大内容，不能把整章正文塞进持久控制状态。

### 4.3 目标代码目录

以下是分期目标，不是本轮要一次性创建的脚手架。

```text
novel_builder/
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example
├── .gitignore
├── README.md
├── docs/
│   ├── 三份规范与本计划
│   ├── decisions/                   # 章节格式、编号、事务等短设计决策
│   └── validation/                  # 脱敏阶段验收记录
├── src/novel_agent/
│   ├── domain/                     # Phase 1：models、refs、changes、errors
│   ├── ports/                      # Phase 1：store、lock、provider 接口
│   ├── services/                   # Phase 1：project、chapter、session、artifact、commit
│   ├── infrastructure/
│   │   ├── filesystem/             # Phase 1：路径校验、parser、存储、事务日志
│   │   ├── models/                 # Phase 1：配置、registry、gateway
│   │   └── checkpoints/            # Phase 2–3：本地执行状态持久化
│   ├── application/                # Phase 2：Facade 与依赖组装
│   ├── runtime/                    # Phase 2：run、task、event、cancel
│   ├── agent/                      # Phase 2：Main Agent、工具权限、context、subagents
│   ├── cli/                        # Phase 2：输入、命令、附件解析、渲染
│   ├── workflows/                  # Phase 3：六类 Workflow、gates、request/result
│   └── __main__.py                 # Phase 2：产品启动入口
└── tests/
    ├── unit/
    ├── integration/
    ├── real_llm/
    └── fixtures/                   # 小型合成数据，无凭证
```

Phase 1 通过服务 API 和测试运行，不先做完整交互 CLI。后续建议入口为 `uv run novel-agent`，目前不存在该命令。

### 4.4 Project 数据目录及状态归属

```text
novel-project/
├── PROJECT.md
├── MANUSCRIPT.md                   # 可重建，禁止当作正式章节来源
├── chapters/0001.md                # 一个文件：概述 + 大纲 + 正文
├── sources/
├── references/
└── .novel-agent/
    ├── project.json               # schema、project_id、章节 ID/路径/顺序
    ├── sessions/<session_id>.json # Session metadata 与执行状态引用
    ├── drafts/<artifact_id>/      # revision 内容、metadata、依赖指纹
    ├── commits/<commit_id>/       # 意图、状态、幂等结果、事务日志
    ├── checkpoints.sqlite         # Phase 2–3：运行控制状态的权威存储
    ├── cache/                     # 可丢弃索引，不保存另一套正式事实
    └── tmp/                       # 事务 staging；不是用户历史版本库
```

`state.json` 在需求中属于推荐结构，不强制同时保存另一份完整执行状态。Phase 1 的 Session JSON 保存元数据；引入 checkpoint 后，active task/workflow、Pending Gate 等以 checkpoint 为准，metadata 仅保留稳定关联或可重建索引。若保留 `state.json`，仅作索引，不复制图状态。

## 5. Phase 1 的最小实现设计

### 5.1 首个交付（已完成）

首先实施的是 **M0 + M1.1：可创建、可重新打开、可验证的 Project 与初始 Session**，现已完成。

交付包含环境与测试骨架、Project 目录、schema、初始 Session metadata、路径验证、章节序列化规则与读取测试。使用小型合成章节验证 parser；公开正式修改 API 在 Commit 通道完成后开放。此时不做 Agent、完整 CLI、Workflow，也不宣称 Phase 1 已完成。

### 5.2 Phase 1 完整服务合同

| 服务 | 最小接口语义 | 关键约束 |
|---|---|---|
| ProjectService | create/open/load/validate/read/search | create 接收 Project 名和初始 Session 名；错误结构不静默修复 |
| ChapterService | parse/read section、prepare changes、count/rebuild | 编辑和结构操作生成 Change；不提供绕过 Commit 的公共裸写接口 |
| SessionService | create/list/load/update metadata | 一个 Session 只属于一个 Project；新 Session Auto 关闭，不复制旧对话 |
| ArtifactService | create/revise/get revision/list/discard | 内容 revision 不可变；每次修订新建版本；返回稳定引用 |
| CommitService | prepare/validate/apply/recover/get result | 精确 revision、freshness、批量一致性、幂等结果 |
| ModelGateway | load config/list/select/invoke | 初期只做配置和短调用；无小说业务判断 |

核心引用建议为 `ArtifactRef(artifact_id, revision, content_hash)`、`ChapterRef(chapter_id)`、`ProjectSnapshot(read_set, write_set_baselines, order_hash)`。引用、目标和协议均需 schema_version。

### 5.3 内容与版本

- 章节采用固定、可无损读写的概述/大纲/正文边界；正文中的 Markdown 标题不能被误识别为结构分隔。M0 写明 grammar、转义和损坏处理规则。
- 章节使用稳定 `chapter_id`；显示顺序、编号、文件路径单独建模，不能用序号替代永久身份。
- 建议首版插入或移动时只更新顺序映射，不自动重命名现有文件。此为需求 §27 未定规则的提案，M0 定稿；相关语义在 CLI 显示当前编号。
- Artifact 的内容和依赖在 revision 内固定；状态可以经明确状态转换更新。用户看到 r1 后产生 r2，提交 r1 仍必须使用 r1 的引用，不擅自替换成 latest。
- 为 CLI 设计 revision 明确选择方式，例如 `/artifact show <id> --revision N`、`/commit <id>@N`；基础 `/commit <id>` 必须绑定当前已展示版本，歧义返回待决策，不静默选择未展示 revision。
- Commit 只安装指定内容，不调用生成模型，不改写正文措辞或自动规范化用户已经确认的内容；结构包装与正文内容分别验证。
- 分析类 Artifact 默认不可作为正文提交；若有明确转换目标，先生成相应可提交 Artifact。
- 结构变化不级联改写其他章节的情节。字符计数是确定性工具；计数是否包含空白和换行在 M0 明确，正文之外的标题/元数据不参与。

### 5.4 Freshness 与受控写入

Artifact 生成前记录相关正式章节、Project metadata/顺序、必要参考资料及依赖 Artifact revision 的内容指纹。新建目标记录“应不存在”的基线；不只比较 mtime，也不只依赖应用内部版本号，以识别手动改文件。

Commit 在 Project 写锁内重新读取并校验 read set、目标基线和冲突 Change；与任务无关且未被读取的文件变化不必导致全 Project 冲突。过期、目标缺失/意外出现、锚点多解均返回结构化 `DecisionRequired`，不自动覆盖。

Phase 1 只提供错误与待决策协议及测试，不实现自然语言决策或 Workflow Gate。Phase 2/3 将该协议映射到用户控制流程。

应用锁串行化本产品的写操作，不能保证锁住用户外部编辑器；在应用前再次检查哈希并在恢复时校验磁盘状态。若发生无法安全解释的外部并发修改，停止受影响 Commit 并返回冲突，不声称支持多用户事务隔离。

### 5.5 多文件 Commit 与恢复

采用本地可恢复事务：

1. 固定 commit_id、精确 Artifact 引用、直接编辑 Change 及基础快照。
2. 验证目标、权限、版本、重叠修改和章节结构。
3. 在 Project 内 staging 生成全部目标文件及派生 MANUSCRIPT，持久化操作清单和恢复信息。
4. 在写锁保护下执行文件替换/删除并记录进度；所有应用服务读取也遵守事务边界。
5. 正式文件一致后更新 Artifact 状态及 Commit Result，最后标记 COMPLETED。
6. 重开 Project 时先恢复未完成事务，再向上层开放读写；相同 commit_id 重试返回原结果，不重复插章或重复追加。

单个文件替换不等于多文件原子事务。目标是应用内不暴露混合结果、故障后可恢复完整一致状态；外部进程瞬时读取多个文件的强原子快照不在本地文件方案保证范围内。恢复要校验旧/新摘要；遇到外部修改无法安全恢复时封锁受影响写操作并报告错误。临时恢复备份不构成用户可浏览的历史版本或自动回滚历史 Commit 功能。

已 COMPLETED 的 Commit 不因 Task/Workflow Cancel 而回滚。MANUSCRIPT 构建失败不能报告成功；Artifact 状态与 Commit Result 的中断恢复也必须覆盖。

### 5.6 路径与配置

路径服务负责规范化和 containment 校验，覆盖 Windows 大小写、盘符、`..`、UNC、符号链接/junction、路径前缀混淆及不存在目标的父目录。禁止附件读取内部凭证/配置；开放的附件类型仅 `.txt/.md`。CLI 以后复用此服务，在模型调用前拒绝不存在、空文件或不可读文件。

模型配置先兼容现有变量，不要求用户重填 key。建议增加可选 `NOVEL_DEFAULT_MODEL=deepseek`；`deepseek`/`grok` 为配置别名，实际模型 ID 仍来自 `.env`。`.env.example` 只保存字段和空凭证。不同 provider 的 reasoning、streaming、tool 参数能力由 adapter 处理，不能把一种模型的参数无条件传给另一种模型。

最先加入 `.gitignore`，排除 `.env`、环境目录、凭证和本地运行产物；保留 `.env.example`。日志禁止输出 Authorization、完整环境变量或 SDK 请求头。默认不向外部追踪服务上传小说内容。

## 6. 里程碑与退出条件

采用验收条件驱动的里程碑，不在依赖尚未锁定时承诺工期。

| 里程碑 | 范围 | 可检查交付 / 退出条件 |
|---|---|---|
| M0：技术方案与格式定稿 | 确认技术栈、隔离环境、包/测试配置、章节格式、ID/编号、freshness、事务设计 | 锁定依赖；短 ADR 可审查；`.env` 已忽略；不实现 Agent |
| M1.1：Project 生命周期 | create/open/load/validate、初始 Session、path safety、chapter parser | 临时目录创建及跨进程重开测试通过；无模型依赖 |
| M1.2：Artifact 与单目标 Commit | revision、scope、依赖指纹、精确版本、单章/Section Change、MANUSCRIPT | 展示旧版本后提交仍完全一致；过期写入被拒；未 Commit 不改变正式章 |
| M1.3：完整确定性 Project 操作 | 多 Change、结构操作、搜索/统计、锁、事务恢复、幂等 | 操作矩阵与故障注入通过；重启后无重复写入或不可解释的半完成状态 |
| M1.4：Phase 1 gate | 模型配置/调用、Project API 文本 smoke、文档 | unit + integration + 至少一个真实配置模型 Project smoke 全通过；建议两个聊天模型均复测 |
| M2A：Freeform Agent | Main DeepAgent、受控工具、Artifact/Commit、任务上下文、只读 Subagent 基础 | Agent 行为测试及真实 Freeform 用例通过 |
| M2B：CLI | Project/Session、附件、命令、Markdown/streaming、事件、取消 | CLI 接口测试和真实模型端到端用例通过；与 M2A 在稳定接口上并行推进 |
| M2：Phase 2 gate | 两条轨道合并验证 | 下节全部 Phase 2 场景通过后进入 Workflow |
| M3.1：单章子图 | Request/Result、Context、两次 Approval、Review、Commit、Decision、Exit/Resume | 真实单章、反馈、Auto、Decision、进程恢复通过 |
| M3.2：其余五种子图 | Continue/Multi/Backfill/Rewrite/Import | 续写/补写上下文证据正确；多章和导入阶段顺序符合需求；全部子图 deterministic + integration 通过 |
| M3：Phase 3 gate | Workflow 综合 | 必需真实场景全通过，尤其 Auto 下 Decision 不推进 |
| M4：完整联调 | Agent 路由、CLI Gates、等待聊天、Session 重开、模型切换、结构化 Result | 11 项真实 E2E 全通过，形成可复现验收记录 |

M2A/M2B 并行是后续开发轨道安排，本次不启动编码或委派实现。阶段之间严格串联：Gate 未通过时留在当前阶段修复或明确阻塞。

## 7. Phase 1 测试计划

### 7.1 Deterministic unit tests

| 测试组 | 关键断言 |
|---|---|
| Project lifecycle | 重复创建不覆盖；缺失/损坏 metadata 明确失败；schema 版本不兼容有明确错误 |
| 章节格式 | 中文、空 Section、正文内部标题、换行与 Unicode 正确；parse/serialize 保留正文；重复边界不静默猜测 |
| Session metadata | Project 归属正确；重开配置不丢；新 Session Auto 关闭且无旧 Session 状态 |
| Artifact | 类型/scope 校验；revision 单调；旧内容不可覆盖；状态转换有效；discard 不修改正式章 |
| 精确版本 Commit | r1 展示、创建 r2 后仍能明确提交 r1；hash 不符拒绝；Commit 不调用 LLM |
| Freshness | 手工编辑且保留时间戳仍被发现；新增目标冲突；顺序及依赖改变被发现；不相关修改不误阻塞 |
| Change validation | 锚点无匹配/多匹配、目标不存在、重叠 Patch、冲突批量 Change 均不产生部分写入 |
| 结构操作 | 新增/插入/删除/合并/拆分/移动/改名；稳定 ID 不错指；不自动改写其他章 |
| MANUSCRIPT | 正文和顺序变更后准确重建；只聚合正文；不含摘要、大纲、状态事件或 Working Artifact |
| Path safety | traversal、绝对外部路径、UNC、盘符、大小写、junction/symlink、前缀混淆、含空格文件名 |
| 工具与配置 | 字符统计边界；缺 key/非法 URL/未知别名提前失败；模型参数隔离；日志脱敏 |

Windows 链接相关测试如受系统权限影响，应明确记录未执行项并在具备权限环境补测，不能将 skip 视为路径安全通过。

### 7.2 Integration tests

1. 在临时目录创建 Project 和初始 Session，加载章节，创建 Artifact r1/r2，Commit 指定版本，关闭并重开；验证内容、状态、顺序和 MANUSCRIPT。
2. 单次 Commit 同时修改概述、大纲、正文或多个章节；确保合并后的章节结构完整，返回准确 changed_files 和 Artifact refs。
3. 用户外部编辑目标文件后尝试 Commit；验证正式文件不被覆盖，并得到结构化冲突结果。
4. 在 staging、首次替换、后续替换、MANUSCRIPT、Artifact 状态写入及完成标记位置注入失败；新进程恢复，检查一致性和幂等性。
5. 对相同 commit_id 重试及模拟 checkpoint 恢复后的重复提交；不得重复创建章节或修改已成功结果。
6. 两个写请求竞争同一 Project，验证锁和 freshness；验证恢复过程中服务不暴露混合状态。
7. sources/references 保持独立；读附件、创建/批准/丢弃 Artifact 均不产生正式小说修改。

所有测试使用临时 Project；现有样例若用于导入回归，仅复制到临时目录，不能原地转换。Phase 1 无网络测试应在禁止模型调用的情况下通过。

### 7.3 Real-model gate：与本轮探针分开

正式测试路径：`tests/real_llm/test_phase1_connectivity.py`（计划创建）。

步骤：

1. 显式启用真实模型测试，读取 `.env` 并验证所选模型的 ID、endpoint、key；不打印凭证。
2. 使用 ProjectService 创建临时 Project，走受控 Change/Commit 写入一段小型合成正文。
3. 用 Project read API 读取实际已 Commit 正文，通过项目 ModelGateway 向配置模型发出一个短概括请求。
4. 验证响应结构有效、最终内容非空，记录配置别名、请求/返回模型、耗时及可用 usage。
5. 验证请求确实包含从 Project API 读取的文本；调用前后正式 Project 文件摘要保持一致。
6. 不断言具体文学句子，不以模型声称“已读取/已提交”为成功依据。
7. 记录通过、失败或阻塞；缺 key、超时、provider 错误不能转成假通过。

建议命令（实现后才可运行）：

```powershell
uv sync --frozen --group dev
uv run pytest tests/unit tests/integration
$env:RUN_REAL_LLM_TESTS = "1"
uv run pytest tests/real_llm/test_phase1_connectivity.py -m real_llm -s
```

默认 deterministic 命令不收集真实调用。显式 gate 运行时，缺少必需凭证应失败并解释，不用自动 skip 形成“全绿”。首次建议使用 `deepseek-flash`，再复测 `grok-4.7`；Embedding 不影响 Phase 1 完成。

## 8. 后续阶段验证矩阵

### 8.1 Phase 2

Deterministic：Agent 工具权限、自然语言意图的结构化结果验证、Session 隔离、Artifact revision、附件解析、命令来源、事件/内容分离、取消与配置切换。Integration：服务 + Runtime + CLI 输入输出驱动，不以模型最终自述代替磁盘和调用轨迹断言。

必须执行真实模型场景：

1. 普通 Freeform 对话：有响应，无正式写入。
2. Project-aware query：读取正确章节，有证据引用。
3. 工具调用：轨迹存在，参数和结果可验证。
4. Artifact 生成：类型、target、内容存在；正式 Project 不变。
5. Artifact revision：版本递增、旧版可读、依赖正确。
6. 自然语言 Commit：提交用户指定的已展示 revision。
7. CLI 显式 Commit：相同服务语义，输出实际 changed_files。
8. 单附件和多附件：调用前校验、内容传入、路径不逃逸。
9. Markdown 流式输出：真实流式事件、逐步可见，最终结构可渲染。
10. Runtime event：最终回答前出现，且不污染 Artifact。
11. 模型切换：当前已配置两个聊天模型，必须验证后续调用使用新模型，既有 Artifact 不变。
12. 安全回归：附件或模型输出中的 `/exit`、`/commit` 不能被 CLI 当成用户控制命令。

Phase 2 的 `/mode workflow`、`/auto` 先保存 Session 设置；固定 Workflow 尚未交付时，对相应创作执行明确提示能力未就绪，不伪装成已走工作流。

### 8.2 Phase 3

Deterministic：六种图的阶段迁移、Approval/Decision 区分、明确 gate_id/workflow_id、拒绝与反馈、Auto 开关、Exit、依赖 Task 阻塞、幂等 Commit。Integration：持久 checkpoint、进程重启、Commit 后恢复不重复副作用。

必须执行真实模型场景：单章人工确认；拒绝/反馈后的新 revision；Auto；Auto 下 Decision 仍暂停；Exit；独立进程中断后恢复；续写读取前章与人物资料；补写读取后续约束。另对 Multi、Rewrite、Import 做真实代表性用例，避免只测试公共单章子图。

多章检查“全部概述批准 → 逐章大纲批准 → 逐章正文”的顺序；导入检查 `.txt/.md/.pdf` 文字提取、无标题整篇一章、空 PDF 不转换、全部概述确认和每 10 章大纲批次。用户指定字数时由工具检查 ±10%，Review 结果与正文分别存储。

### 8.3 Phase 4

先跑 deterministic/integration 路由与控制回归，再完成以下 11 项真实 E2E：

| 编号 | 场景 | 主要验收证据 |
|---|---|---|
| E01 | Freeform：大纲 → 修改 → 正文 → Commit | revision 链、精确内容、正式文件变化 |
| E02 | Workflow 续写 + 人工 Approval | 读取前章、两次正确 Gate、Commit Result |
| E03 | Workflow + Auto | 批准来源为 Auto，无普通 Approval 等待 |
| E04 | Auto 下 Decision | 保持 WAITING_DECISION，明确选择前不继续 |
| E05 | 等待 → 普通聊天 → 再确认 | 原 workflow_id/gate_id 不变，普通聊天不确认或拒绝 |
| E06 | Workflow Exit | Session 继续，已提交保留，未提交不写入 |
| E07 | 退出应用 → 重开 Session → Resume | 恢复同一状态和精确 Artifact 引用，不重复 Commit |
| E08 | 附件 → Agent → Artifact → Commit | 校验与依赖证据，提交前不写正式内容 |
| E09 | 缺失章补写 | 读取后续约束，补入正确目标和顺序 |
| E10 | 暂停时切换模型 | 后续节点用新模型，已生成内容 hash 不变 |
| E11 | Commit 前 freshness 冲突 | 无覆盖，明确 Decision，决策后基于新校验执行 |

## 9. 待定规则与范围控制

技术栈已在 M0 确认并完成隔离环境兼容性检查。以下尚未在需求中完全定义的规则按里程碑形成短 ADR，不隐含视为既定需求：

| 规则 | 定稿时间 | 当前建议 |
|---|---|---|
| 章节结构标记、标题、编号及字数细节 | M0 | 稳定 chapter_id；顺序独立；格式可无损往返；字符口径显式测试 |
| Artifact 生命周期/清理 | M1.2 | 不自动清理仍被 Task/Gate/Commit 引用的 revision；先支持显式 discard |
| freshness 粒度和批量恢复策略 | M1.2–M1.3 | 依赖文件摘要 + 目标基线；带幂等 key 的持久事务日志 |
| 附件大小/数量与空文件行为 | M2 | 配置限额并在调用前验证；超限提示，不静默截断 |
| Approval 自然语言、Decision 表达 | M3–M4 | 明确绑定 Gate；歧义询问；普通聊天保留等待状态 |
| 复杂任务部分失败 | M2 基础、M4 验收 | 依赖任务阻塞，无依赖任务可继续；默认顺序执行 |
| 导入标题识别 | M3.2 | 明确模式并测试；无标题整篇一章；不做语义自动拆章 |

当前不引入 Web/桌面/移动 UI、HTTP 服务、OCR、云数据库、跨 Session 长期记忆、自动历史回滚或完整版本控制系统。向量数据库、复杂检索框架和额外 Workflow 按实际评测再决定。

## 10. 本轮交付与下一步

初次勘察完成了四份约定/规范阅读、模型配置脱敏检查及两个聊天模型真实连通性探针。此后用户确认实施 M0 + M1.1：技术栈、项目目录、章节格式、Project create/open/load/validate、初始 Session、Project-local 路径安全及确定性测试已落地。详见 [验证记录](validation/M0-M1.1.md)。

M1.2–M1.4 已完成：Artifact revision 与受控 Commit、确定性章节结构/文本操作、多文件故障恢复、Project API + ModelGateway 的真实模型 smoke。

M2A–M2 已完成 Freeform Main DeepAgent、只读分析 Subagent、Project/Artifact/Commit 工具、Session 控制状态、CLI 命令/附件/Markdown 流式输出和模型切换；确定性与真实模型 gate 见 [Phase 2 验证记录](validation/Phase2-M2A-M2.md)。Phase 3 六类 LangGraph Workflow 与 Phase 4 Main Agent/CLI 联调也已实现；对应验收见 [Phase 3](validation/Phase3-M3.md) 与 [Phase 4](validation/Phase4-M4.md)。
