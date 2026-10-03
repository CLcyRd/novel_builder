# ADR 0001：Phase 1 Project 格式与边界

状态：M0 已定稿，2026-09-26。依据：需求 V2 §2、§6、§18–22；Agent 设计 §9–12、§26–28；CLI 设计 §3–7。后续修改格式须增加 schema 迁移，不能悄然改变已存 Project。

## Project 与 Session

Project 根目录是 `PROJECT.md`、`MANUSCRIPT.md`、`chapters/`、`sources/`、`references/` 和 `.novel-agent/`。`.novel-agent/project.json` 记录 `schema_version=1`、稳定 UUID、名称、初始 Session ID、章节条目及顺序。`.novel-agent/sessions/<uuid>.json` 记录 Session 元数据。创建 Project 时必须同时创建初始 Session。新 Session 的默认执行模式为 `FREEFORM`、Auto Approval 为 `false`；M1.1 尚未提供创建额外 Session 的写接口。

每章一个正式文件。`chapter_id` 是稳定 UUID；章节顺序由 `chapter_order` 指定，序号及文件名是显示/存储位置，不等于身份。M1.1 只读取已有注册章节；新增、插入、改名等正式写入等 Commit 层实现后开放。

`MANUSCRIPT.md` 是派生视图，创建空 Project 时为空；后续由 Commit 按顺序从章节正文重建。`PROJECT.md` 是可读全局信息；相关写入也走受控变更。普通附件与参考资料的事实优先级低于正式正文。

## 章节文本语法

正式章节为 UTF-8、LF 换行，具有标题行和顺序固定的三段：

```markdown
# 第一章 雨夜

<!-- novel-agent:summary:start -->
一段概述
<!-- novel-agent:summary:end -->

<!-- novel-agent:outline:start -->
场景一
<!-- novel-agent:outline:end -->

<!-- novel-agent:manuscript:start -->
雨落了下来。
<!-- novel-agent:manuscript:end -->
```

段落内容以标记行之间的原始字符保存，内部 Markdown 标题属于内容。标题不能为空、不能换行。段落内容不能含保留标记行；真实需要显示同样文字时先作为正文普通文本改写或转义，后续格式版本可定义专门转义。解析器要求完整且唯一的边界、规定顺序和 LF；不猜测损坏文件的意图。序列化/解析往返保持每段内容的字符不变，包括末尾换行。规范化仅作用于结构包装，不能偷偷改写已经展示的 Artifact 正文。

## 路径与状态

Project-local 输入必须是相对路径；拒绝 `..`、绝对路径、外部 symlink/junction 目标，以及 `.novel-agent` 内部路径。聊天附件另限制 `.txt/.md`，由 CLI 在调用 Agent 前校验。M1.1 提供可复用的读取路径服务，但不提供任意正式写接口。

Project 元数据属于正式结构索引，Session 元数据属于控制面。大段正文不进入 Session 元数据。引入 LangGraph checkpoint 后，运行中的 Task/Workflow/Pending Gate 以 checkpoint 为权威状态，不并行维护另一份可冲突的完整状态。

## Commit 决策边界与实施记录

Artifact revision 不可变，Commit 绑定精确 revision。Freshness 比较依赖文件与目标内容摘要，包括目标原先不存在的情况；冲突产生结构化待决策结果。多文件变更先 staging，再用持久日志和幂等 Commit ID 恢复。插入/移动章节保持稳定 chapter_id，顺序与编号分离，新章节文件名单调分配，不因移动重命名。完整决策与故障边界见 [ADR 0002](0002-commit-and-recovery.md)。
