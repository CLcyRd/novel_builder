# Phase 2：M2A、M2B、M2 验证记录

日期：2026-09-26。范围仅限 Freeform Main Agent 与本地 CLI；固定创作 Workflow 属于 Phase 3。

## 实现边界

- `agent/freeform.py` 使用 Deep Agents 构建唯一面向用户的 Main Agent；内建任务拆解与显式只读 `project_analyst` Subagent。SDK 虚拟文件系统不映射 Project root。正式文件只经 `CommitService` 写入。
- Project read、Artifact create/revise、精确 revision Commit 是独立工具；Agent 工具调用轨迹和 RuntimeEvent 与回答文本分离。操作结果经结构化核对，未生成 revision/Commit 不报告完成。
- `runtime/state.py` 在 Project 内 SQLite 存储 Session 的短对话提示、已展示 Artifact refs、当前 ref 和未提交新章标题。正文保存在 Artifact，不放入 Session state。重开 Session 恢复这些引用；不同 Session 隔离。
- `cli/shell.py` 负责命令、附件解析和预检；`__main__.py` 使用 prompt_toolkit 输入与 Rich Markdown 流式渲染。模型输出及附件内容不经过命令解析器。
- `ModelGateway` 的 `.env` 配置复用于 LangChain ChatOpenAI 适配；模型别名按 Session 保存，后续调用读取新选择。缺配置时启动失败，不使用假模型。
- Project 锁增加进程内可重入串行化，避免 Windows 并行工具调用争用同一文件锁。

## 可复现命令

```powershell
uv sync --frozen --group dev --extra agent --extra cli
uv lock --check
uv run --frozen --extra agent --extra cli ruff check src tests
uv run --frozen --extra agent --extra cli pytest tests/unit tests/integration -q
$env:RUN_REAL_LLM_TESTS = "1"
uv run --frozen --extra agent --extra cli pytest tests/real_llm -m real_llm -q -s
uv run --frozen --extra agent --extra cli python -m novel_agent --workspace .\projects --env .\.env
```

## 结果

确定性 unit/integration：52 passed。Ruff 和 `uv lock --check` 通过。真实模型 gate：6 passed，包括 Phase 1 连通性 2 项和 Phase 2 场景 4 项；所用配置模型为 `deepseek-flash` 与 `grok-4.7`。测试只断言非空响应、工具轨迹、Artifact ref/target/revision、文件变动、未提交时正式文件不变、事件与流式片段、附件路径、多模型切换，不断言具体文学措辞。

Phase 2 场景包括普通聊天、正式章节查询、只读 Subagent/拆解、Artifact 生成与修订、自然语言 Commit、CLI `/commit`、单附件与多附件、Markdown 流式片段、RuntimeEvent、DeepSeek→Grok 切换、附件内 `/exit`/`/commit` 无命令效果。真实测试使用临时 Project；人工终端探针确认 Project 打开、状态展示、普通对话、Rich 输出和退出。Ctrl+C 的取消传播用确定性异步任务取消测试覆盖；当前 PTY 探针未能产生可靠的 Windows SIGINT，不将其记作人工通过。

Phase 2 中 `/mode workflow`、`/auto` 仅保存 Session 设置；Workflow 模式的创作运行明确拒绝并提示 Phase 3。Approval/Decision Gate、Workflow resume 与 11 项 Phase 4 联调尚未交付。
