# 可编辑章节 draft 验证记录

验证日期：2026-10-01。

Freeform 与六类 Workflow 共用 `DraftService`。可编辑文件在 Project 的 `working/<session>/<task>/<chapter>.draft.md`，同一文件包含概述、大纲、正文。正式章节仍只能通过 Commit 写入，内部 Artifact revision 不会因为 draft 删除而丢失。操作方式见 [CLI 使用说明](../小说Agent_CLI使用说明.md#阅读与手动修改-draft)。

## 确定性验证

```powershell
.venv\Scripts\python -m pytest tests/unit tests/integration -q
.venv\Scripts\python -m pytest tests/integration/test_editable_drafts.py -q
.venv\Scripts\python -m ruff check src tests
```

全量回归通过 139 项；此后新增的 3 项仅涉及测试文件，draft 专项共 18 项通过，静态检查通过。确定性测试合计覆盖 142 项。

专项覆盖：

- 手动大纲作为正文生成上下文、正文逐字 Commit、历史版本不变。
- 大纲确认保留 draft；正文确认删除 draft；Freeform Approval 不写正式 Project。
- 直接 `/commit current` 同步文件；精确历史版本不悄悄替换为手动稿。
- `n` 前保存手动 revision，再更新同一个文件；后续调用携带最新 Artifact 引用。
- 多章的手动正文用于下一章上下文；导入正文允许明确编辑，源文件不变。
- 修改已批准依据进入三选项 Decision；Auto 不代选；退出保留文件。
- 生成/审核期间的并发编辑保留，不进入已经确认的 Commit。
- 缺失文件、损坏区域标记、Project 外路径拒绝处理。
- UTF-8 BOM/CRLF 支持，内容缩进与空行保留。
- Workflow 和 Freeform 重开恢复，重复同步不创建重复 revision。

确定性测试使用虚构 provider 地址时，测试 fixture 禁用默认网络 Skill 选择；真实模型测试不应用此替身，生产 Skill 行为不变。

## 真实模型验证

```powershell
$env:RUN_REAL_LLM_TESTS = '1'
.venv\Scripts\python -m pytest tests/real_llm/test_editable_drafts.py tests/real_llm/test_phase4_e2e.py -m real_llm -q -s
```

8 项通过（307.11 秒），配置从 `.env` 加载：默认模型 `deepseek-flash`，暂停后切换模型场景使用 `grok-4.7`。

Freeform 最新版本引用提示调整后，单独复验相应真实模型用例：1 项通过（22.66 秒）。

新增测试验证 Freeform 和 Workflow 的手动编辑、确认、恢复、精确 Commit 与 draft 删除。既有 Phase 4 E2E 覆盖 Freeform 修订/提交、续写与人物上下文、等待时普通聊天、Session 重开、模型切换、Auto、freshness Decision、Workflow Exit、附件及缺失章节补写。

真实测试断言 Artifact/dependency、Gate、文件内容和状态，不断言具体生成的文学文本。所有测试使用临时 Project，未修改开发中的小说 Project。

## 使用边界

读取编辑器已保存的文件，不读取未保存缓冲区。保留章节标题、区域标题及边界标记；尚未生成区域保持空白。新文件默认大小上限为 2 MB，配置位于 `config/setting.py`。普通聊天仍流式输出，章节候选默认展示文件路径；`/artifact show` 继续提供完整版本查看。
