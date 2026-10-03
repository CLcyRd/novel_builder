# 单章节概述与大纲分阶段确认

验证日期：2026-10-01。

## 实现

新单章节 Workflow（创建、续写、补写、重写）使用：

```text
Prepare → Summary → SUMMARY Approval
→ Outline → OUTLINE Approval
→ Manuscript → Review → MANUSCRIPT Approval
→ Commit → Result
```

概述确认前不生成大纲，大纲确认前不生成正文。各阶段有独立 Gate ID、修订轮次和 ApprovalRecord；`n` / 修改意见仅修订当前阶段。生成节点还检查上游 Artifact 的 APPROVED 状态。正文确认前正式 Project 不变，Commit 使用已确认的精确 revision 并检查 freshness。

仍使用同一个章节 draft：概述阶段其他区域为空；概述和大纲确认保留文件；正文确认或成功 Commit 后删除。`DraftService.export(replace_refs=True)` 在新单章节流程中绑定当前有效区域，防止失效下游从旧记录再次合并回来；历史 Artifact 不删除。Freeform、多章和导入保留原有绑定方式和阶段安排。

修改已批准的上游材料会进入 Decision。明确选择后返回相应阶段重新确认；REGENERATE 清除下游当前绑定，KEEP 保留文本并更新其依赖引用，仍逐阶段确认。Auto 不替用户选择 Decision，明确选择后仍可自动通过后续 Approval。并发编辑会保留文件并暂停，审阅重试不重新生成正文，跨阶段返回不丢失用户修改意见。

新单章节 checkpoint 的 State 保存 `workflow_version=2`。旧 checkpoint 缺失版本时编译版本 1，保留原 `plan_gate` / `approval_plan` 等节点、PLAN Gate 和合并审批行为，以恢复旧的暂停任务；不迁移 Gate、不重新生成用户已经看到的候选。版本分支共用生成、Project、Artifact 和 Commit 服务。

## 确定性验证

```powershell
.venv\Scripts\python -m pytest tests/unit tests/integration -q --tb=short
.venv\Scripts\ruff.exe check src tests
```

最终全量 **192 项通过**（71.89 秒），Ruff 通过。新增 `test_split_chapter_stages.py` 覆盖：

- 概述、大纲分别拒绝与修订，不误重做其他阶段。
- 上游手动 revision 先批准，再提供给真实生成接口的上下文组装。
- 每个阶段重开、精确 Commit、同一 draft 保留/删除与 stale Gate 拒绝。
- 在大纲/正文阶段修改上游，分别选择 KEEP / REGENERATE，历史保留与活动绑定清除。
- 生成大纲期间并发修改概述，返回概述确认并保留候选大纲。
- 重做正文受阻后，下游旧正文不会从 draft 记录恢复成当前版本。
- 上游重新确认期间保留用户修改意见。
- 无版本的旧 PLAN、MANUSCRIPT、生成受阻 checkpoint 按原图恢复完成。
- 新三个 Gate 分别 Exit，不写正式 Project。

既有测试更新为三个确认点，覆盖四类单章节、多章/导入、Skill 冻结、CLI、模型切换、Auto、Decision、freshness 与新进程恢复。

## 真实模型验证

配置从 `.env` 加载，模型为 `deepseek-flash`；Phase 4 的暂停后模型切换场景使用 `grok-4.7`。测试使用临时 Project，断言阶段、Artifact 依赖、审批记录、Commit 与文件，不断言具体生成的文学措辞。

```powershell
$env:RUN_REAL_LLM_TESTS = '1'
.venv\Scripts\python -m pytest tests/real_llm/test_m3_1_create_chapter.py tests/real_llm/test_editable_drafts.py::test_live_workflow_manual_edits_resume_and_exact_commit tests/real_llm/test_m3_2_workflows.py -m real_llm -q -s --tb=short
.venv\Scripts\python -m pytest tests/real_llm/test_phase4_e2e.py -m real_llm -q -s --tb=short
```

第一组 **10 项通过**（500.12 秒），第二组 **6 项通过**（499.61 秒）。验证了概述反馈、大纲反馈、人工确认、独立进程重开、Exit、Auto 下 freshness Decision、概述和大纲手动版本逐字 Commit，以及续写、补写、重写、多章、导入、附件、普通聊天与暂停后模型切换。

补充验证：

```powershell
.venv\Scripts\python -m pytest tests/real_llm/test_generation_decision.py tests/real_llm/test_builtin_writing_skills.py::test_live_builtin_viewpoint_skill_uses_stage_scope_and_survives_reopen -m real_llm -s -q --tb=short
```

**2 项通过**（144.17 秒），验证 Auto 下必要条件缺失仍进入 Decision、补充条件后恢复并提交，以及分阶段生成中的内置 Skill 作用阶段与重开版本保持。

本次共 **18 项真实模型测试通过**，无验证阻塞。Freeform 本次未改变其审批语义；其既有确定性测试及 Phase 4 真实 E2E 已回归。
