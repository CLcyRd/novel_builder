# 小说 Agent

Phase 1 Project 层、Phase 2 Freeform Main Agent/CLI、Phase 3 六类 Workflow 与 Phase 4 Main Agent/CLI 联调已实现。产品目标和阶段划分见 [开发计划](docs/小说Agent开发计划_V1.md)；[Phase 3](docs/validation/Phase3-M3.md) 和 [Phase 4](docs/validation/Phase4-M4.md) 验证记录列出真实模型 gate。Project 格式见 [ADR 0001](docs/decisions/0001-project-format.md)，Artifact/Commit 与恢复见 [ADR 0002](docs/decisions/0002-commit-and-recovery.md)，章节文件编号见 [ADR 0003](docs/decisions/0003-chapter-file-numbering.md)。需要系统安装 Python 3.13。

首次使用请阅读 [CLI 使用说明](docs/小说Agent_CLI使用说明.md)，其中包含安装配置、命令速查、Freeform、Workflow 审批与恢复、附件和常见问题。

章节列表、搜索和上下文构建使用操作内读取快照，每章只解析一次，并保留全项目校验；快照不跨操作缓存，无需迁移现有 Project。读取边界与性能说明见 [ADR 0005](docs/decisions/0005-project-read-snapshot.md)。

事务恢复只扫描未完成事务，正式 Commit 完成后保留轻量幂等凭据并回收 staged 副本。旧项目首次打开时自动迁移事务布局；迁移后须使用新版程序。`/project maintenance` 重试遗留清理并显示事务占用与回收统计，不调用模型，也不删除 Artifact 历史版本。详见 [ADR 0004](docs/decisions/0004-transaction-retention.md)。

只读 Skill 已接入 Freeform、Workflow 和 CLI；需求边界见 [Skill 需求与落地方案](docs/小说Agent_Skill需求与落地方案_V1.md)，操作方式见 [CLI 使用说明](docs/小说Agent_CLI使用说明.md)。

应用提示词统一维护在 [prompts/prompt.py](src/novel_agent/prompts/prompt.py)，包括 Main Agent、只读分析、任务规划、Skill 选择及概述/大纲/正文/审阅阶段。该层只组织提示和瞬时上下文，不读取 Project 或写入正式内容。Workflow 上下文标明来源、状态与 Artifact 修订；关键材料超过配置预算或模型报告必要条件缺失时进入 Decision，用户补充条件后继续，Auto 不代选。实现与验证见 [提示词验证记录](docs/validation/Prompts.md)。

单章节概述、大纲独立确认及旧 checkpoint 恢复的实现与测试见 [分阶段确认验证记录](docs/validation/Split-chapter-stages.md)。

章节候选默认保存为一个可编辑的 `working/<session>/<task>/<chapter>.draft.md`，包含概述、大纲和正文。Freeform 与 Workflow 都可以打开文件局部修改，保存后输入 `y`，或用 `n` / 修改意见更新同一个文件。新单章节 Workflow 按“概述确认 → 大纲确认 → 正文确认 → Commit”推进，`n` 只重做当前阶段。概述和大纲确认保留 draft，正文确认或成功 Commit 后删除 draft；Artifact 历史版本保留。Freeform 的 `y` 不会正式写入 Project，仍须 Commit。修改已确认依据时会显示三选项 Decision，Auto 不代选；单章节流程明确选择后会重新确认上游与受影响阶段。旧的未标记版本 Workflow 保留 PLAN 合并确认以支持重开恢复。详见 [draft 使用方法](docs/小说Agent_CLI使用说明.md#阅读与手动修改-draft)。

```powershell
uv sync --frozen --group dev --extra agent --extra cli
uv run --frozen --extra agent --extra cli pytest tests/unit tests/integration
uv run --frozen --extra agent --extra cli ruff check src tests
$env:RUN_REAL_LLM_TESTS = "1"
uv run --frozen --extra agent --extra cli pytest tests/real_llm -m real_llm -s
```

启动交互式 CLI：

```powershell
uv run --frozen --extra agent --extra cli python -m novel_agent --workspace .\projects --env .\.env
```

在 CLI 中输入 `/project create 小说名` 建立 Project 和初始 Session；输入普通文字开始 Freeform 对话。`/help` 列出命令；`@references/资料.md` 或 `@"references/含空格资料.md"` 附加当前 Project 内的文件。`/artifact show <id>@<revision>` 查看精确版本，`/commit current` 同步并提交当前工作稿，`/commit <id>@<revision>` 提交指定版本。`/model list` 查看已配置模型，`/model grok` 切换后续调用。输入 `/mode workflow` 后，创作请求由 Main Agent 路由至 Workflow；等待审批时输入 `y` 批准、`n` 重做、`e` 退出，或直接给出修改意见。freshness Decision 使用 `1` 重新生成、`2` 退出；draft 冲突使用 `1` 重新生成、`2` 保留手动稿、`3` 退出。`/workflow status|exit` 查看或退出当前流程。等待期间仍可普通聊天，`/auto on` 仅推进 Approval Gate。

Project API 示例：

```python
from pathlib import Path
from novel_agent.application import build_project_service

service = build_project_service()
project = service.create_project(Path("projects"), "深海回声", "初始会话")
opened = service.open_project(project.root)
print(opened.metadata.name, opened.sessions[0].name)
```

真实模型测试从 `.env` 读取配置；复制 `.env.example` 配置。真实密钥不应提交到版本库。默认 `pytest` 不收集 `tests/real_llm/`，必须显式启用并执行上述真实模型命令。Session 对话提示和 Artifact 引用保存在 Project 内 `.novel-agent/checkpoints.sqlite`；正式小说内容仍以章节文件为准。Main Agent 的 Deep Agents 虚拟文件系统与正式 Project 文件隔离，正式写入只能通过 CommitService。

单章 Workflow 服务 API：

```python
from pathlib import Path
from novel_agent.application import build_create_chapter_workflow
from novel_agent.infrastructure.models.gateway import ModelGateway
from novel_agent.workflow.generator import LLMChapterGenerator
from novel_agent.workflow.models import GateResponse, WorkflowRequest

gateway = ModelGateway.from_env(Path(".env"))
workflow = build_create_chapter_workflow(LLMChapterGenerator(gateway))
request = WorkflowRequest(
    project_id=opened.project_id,
    session_id=opened.sessions[0].session_id,
    title="第一章",
    instruction="创作第一章",
)
snapshot = workflow.start(opened, request)
# 向用户展示 snapshot.gate.artifact_refs 对应的 Artifact 内容后，接收明确选择。
snapshot = workflow.resume(
    opened,
    request.workflow_id,
    GateResponse(gate_id=snapshot.gate.gate_id, action="APPROVE"),
)
```

Workflow checkpoint 保存在 `.novel-agent/workflows.sqlite`，Session 中仅保存活动 Workflow ID/类型、已完成 Task ID 和最近 Result。等待 Gate 时可重新创建服务实例并调用 `load_active` 恢复；Approval/Decision 只接受当前 Gate ID，Auto Approval 不跨越 Decision Gate。

M3.2 新增 `build_continue_chapter_workflow`、`build_backfill_chapter_workflow`、`build_rewrite_chapter_workflow`、`build_multi_chapter_workflow`、`build_import_novel_workflow`。输入均为 `WorkflowRequest`；补写通过 `position` 指定插入位置，重写通过 `chapter_id` 指向已有章节，多章通过 `chapters=(ChapterTarget(...), ...)` 指定目标。导入通过 `source_path="sources/novel.txt"` 指定当前 Project 内的 `.txt/.md/.pdf` 原始文件；PDF 只提取文字，不做 OCR。

核心职责边界见 [ADR 0006](docs/decisions/0006-core-responsibility-boundaries.md)：Freeform 编排与草稿/工具/模型执行拆分，Workflow 图装配与节点拆分，Commit 内容解析/内存变更/写入规划拆分；现有 CLI 命令、服务入口及 checkpoint 协议保持兼容。
