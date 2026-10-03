# M3.2 五类 Workflow 验证记录

新增 Continue、Backfill、Rewrite、Multi Chapter 和 Import Novel 五类 LangGraph 子图。前三类复用单章两次 Approval 的阶段结构，但各自校验目标、装配正式上下文并生成不同 Commit：续写追加新章节且读取前章与人物资料；补写读取插入点前后章节并按指定位置插入；重写保留原章节 ID 和顺序，以三个新 Section Artifact 替换旧内容。

Multi Chapter 先生成所有章节概述并一次审批，再逐章生成及审批大纲，最后逐章生成及审批正文与只读 Review，全部通过后一次 Commit。Import Novel 从 Project `sources/` 中的 `.txt/.md/.pdf` 提取原有正文，以确定性标题规则拆章；无标题整篇一章，空 PDF 或无文字 PDF 停止转换，不做 OCR。导入时原始正文保存在工作 Artifact，先审批全部概述，再每 10 章一批审批大纲，最后一次 Commit。两种批量图都只在状态中保存目标、阶段和 Artifact 引用，不存储大段正文。用户指定字数目标时，Review Artifact 包含确定性的 ±10% 字数检查。

验证命令：

```powershell
uv run --frozen --extra agent --extra cli pytest -q
# 68 passed
uv run --frozen --extra agent --extra cli ruff check src tests
# All checks passed
$env:RUN_REAL_LLM_TESTS = "1"
uv run --frozen --extra agent --extra cli pytest tests/real_llm/test_m3_2_workflows.py -q -s
# 5 passed；model=deepseek-flash
```

确定性集成测试覆盖五类图的阶段顺序、正式上下文、目标 Chapter ID/位置、Approval 前无正式写入、集体概述修订反馈传递到每章、导入 11 章的 10+1 大纲批次、无标题整篇一章、PDF 文字提取和空 PDF 拒绝，以及 Auto Approval 下源文件变化进入 Decision Gate、重新读取源文件后提交。真实模型代表性测试分别验证续写收到前章与人物资料、补写收到后章约束、重写保留章节 ID、多章先全部概述再大纲、导入正文与原始文件完全一致。

M3.2 不包含 Main Agent 的 Workflow 路由或 CLI Gate 交互；这些属于 M4。M3 Phase 3 总 gate 仍需按开发计划合并复核六类图的全量恢复与异常场景。
