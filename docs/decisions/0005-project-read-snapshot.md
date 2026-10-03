# ADR 0005：操作内 Project 读取快照

状态：已实施，2026-10-02。

## 问题

原 `open_unlocked()` 校验并解析全部章节后丢弃解析结果。每次 `read_chapter()` 再调用它，并再次解析目标章节。`list_chapters()` 先打开 Project，再对 N 章逐一读取，共解析 N² + 2N 次。搜索章节、项目说明及 R 个参考文件，共解析 N² + 3N + RN 次。

10 章临时 Project 的旧实现计数为：列表 120 次、无参考文件的搜索 130 次、有两个参考文件的搜索 150 次。

## 读取边界

`ProjectReadSnapshot` 保存一次完整校验产生的 Project、PROJECT.md 原文、UUID 对应的 ChapterDocument，以及可选的参考文件/附件文本。章节和文件映射只读，ChapterDocument 保持不可变。

`FileProjectRepository.read_snapshot()` 在现有 Project 进程及文件锁内依次恢复 pending、加载并完整校验 Project、检查传入 Project 身份、读取本次所需资料。返回后释放锁。列表、搜索和上下文拼装在锁外复用该结果；模型调用不持锁。

`read_snapshot_unlocked()` 供已经持锁且已恢复 pending 的调用方使用。`open_unlocked()` 包装它并继续返回 Project，兼容原有接口。

完整校验继续拒绝缺失或损坏的登记章节、未登记章节、无效 Session/metadata、不安全的目录及路径。单章读取仍执行全项目校验，但直接返回已解析目标，不再进行第二次解析。未知章节 ID 仍产生 ChapterNotFound。

## 批量内容和调用方

- `list_chapters()` 按最新快照的 chapter_order 返回章节。
- `search()` 一次读取正式内容及 references / sources 下的 .txt/.md，保持章节段落顺序、文件排序、路径、行号和换行处理方式。
- `read_snapshot()` 的 reference_directories 只允许 references / sources；reference_keywords 可筛选文件名，attachments 显式指定其他 Project-local 输入。每个资料仍通过原路径/附件校验后读取，同一路径在一次快照内只读一次。
- Freeform 项目概览、章节来源输出及按标题定位复用一次读取结果，不在章节循环中打开 Project。
- 单章/多章 Workflow 上下文复用一次快照；邻章选择、完整正式证据、人物参考及附件的来源和优先级保持不变。未选中的资料不成为新的必需输入。
- Commit apply 在锁内重新加载快照，将章节映射复制为渲染所需的可修改字典。prepare 与 apply 之间不复用快照，原摘要 freshness 校验、Artifact revision 校验和幂等凭据保留。

## 性能与边界

列表、搜索、Freeform 读取工具和单次 Workflow 正式上下文构建中，每个正式章节解析一次。以每章文本规模近似相等计，章节解析从平方级降至线性；更准确地说，开销与全书文本总量成正比。参考文件数量不再增加章节解析次数。

单章读取仍为线性；不是随机访问优化。一次快照的内存开销与全书及所选资料文本总量成正比。快照仅为局部变量，不缓存跨命令内容，不加入 Session、Artifact 或 Workflow checkpoint。下一次读取重新恢复并校验磁盘，观察外部编辑和完成的 Commit。

Project 锁串行化应用内操作；不承诺对不遵守该锁的外部编辑器提供文件系统原子快照。现有 Commit 摘要冲突检查继续处理外部修改。

磁盘格式、Project schema、CLI 命令与既有 Workflow checkpoint 不变，无需迁移。

## 验证

新增确定性验收以 0、1、10、100 章验证解析次数；覆盖参考文件数量、排序与行号、快照与下一次读取的差异、Commit 后刷新及 stale 拒绝、未完成事务恢复、完整结构校验、Project 身份、路径越界、正式证据和 Freeform 工具来源。真实模型验收覆盖概览/搜索/章节只读查询与引用，以及带正式前章和人物资料的续写流程。

2026-10-02 验收结果：unit / integration 共 240 项通过，其中读取快照专项 21 项；deepseek-flash 的只读工具查询与续写验收各 1 项通过；Ruff 与差异空白检查通过。所有场景使用临时 Project，未修改现有小说内容。

只读模型测试首轮使用“不生成或提交”的表述，触发现有 Freeform 请求识别中的创作完成检查；改用“只读查询”后通过。本次未改变该意图识别规则。
