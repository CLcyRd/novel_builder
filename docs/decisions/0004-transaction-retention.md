# ADR 0004：事务恢复、完成凭据与恢复副本回收

状态：已实施，2026-10-02。本决策替代 [ADR 0002](0002-commit-and-recovery.md) 的事务目录布局与完成后保留策略；正式内容、Artifact revision、freshness 和向前恢复语义保持不变。

## 问题与目标

旧布局把所有事务放在 `.novel-agent/commits/<id>/`。每次 Project 或 Artifact 读取都遍历历史 manifest；每次 Artifact 修订也产生一组永久保留的 staged 副本。完成的事务不需要这些副本，但正式 Commit 的 ID、指纹和结果必须继续可查，以避免 Workflow checkpoint 重放造成重复提交。

## 布局版本 2

```text
.novel-agent/commits/
├── layout.json                 # schema_version=2；MIGRATING / READY
├── staging/.staging-*/          # 未发布，尚未修改目标
├── pending/<commit_id>/         # 已发布，恢复或收尾所需
│   ├── manifest.json
│   └── staged/*.bin
├── receipts/<id前两位>/<id>.json # 正式 Commit 的永久轻量凭据
└── garbage/<commit_id>/         # 已完成，只需物理清理
```

普通恢复只遍历 `pending/`。READY 布局只检查固定目录与布局标记，不枚举历史凭据、garbage、staging 或旧布局根目录。按 ID 查询直接读取该 ID 的 pending manifest / receipt；两者同时存在时，状态、类型、指纹和结果必须一致，冲突在应用任何目标之前停止恢复。

manifest 显式区分 `COMMIT` 与 `ARTIFACT_REVISION`。正式 Commit receipt 永久保留 schema、ID、类型、指纹、COMPLETED 状态、原始结果及完成时间，不保留正文副本或文件 entries。相同 ID、相同指纹仍返回原结果；不同指纹仍报错。

Artifact 修订事务由仓库内部生成一次性 UUID，没有对外暴露该事务 ID 的重试接口。完成后不保留 receipt。真正的 Artifact revision、status 与 index 仍保存在 `drafts/`，不属于本次回收范围。

## 完成与故障边界

所有操作持有现有 Project 进程/文件写锁：

1. 完整写入 staging，再原子移动到 pending，发布 PREPARED 事务。
2. 校验所有目标与 staged 摘要，按原协议向前应用。
3. 持久保存 COMPLETED manifest。
4. 正式 Commit 原子保存 receipt；内部 Artifact 事务跳过此步。
5. 原子移动到 garbage，再删除恢复副本。

PREPARED/APPLYING 事务继续校验 before/after 摘要并恢复。COMPLETED 只补凭据和清理，不再校验或覆盖后来修改的目标。receipt 写入失败时保留 COMPLETED pending；移走或清理失败时保留 pending / garbage，并发出 `TransactionCleanupWarning`。这些 I/O 清理失败不改变已成功 Commit 的结果。损坏的记录、冲突凭据和不安全路径仍报告 `RecoveryConflict`。

每个写入文件 flush/fsync；POSIX 还 fsync 原子替换、移动及删除涉及的目录。Windows 的 Python 文件接口不提供目录 fsync，因此这里验证进程硬退出后的恢复，不声明具备跨文件系统的断电原子性。

递归删除前校验整个树的最终路径，删除范围仅为 commits 的 staging / garbage 直接子项，拒绝 symlink、junction 和越界路径。未完成或冲突的 pending 不按年龄删除。

## 旧布局迁移

第一次访问在 Project 锁内写 MIGRATING 标记。校验并移动旧 UUID 目录到 pending，移动遗留 `.staging-*` 到 staging，全部搬迁后才写 READY。中断后继续迁移剩余项；随后正常恢复 pending，已完成旧历史仅转换凭据，不重放正式文件。旧内部 Artifact 事务根据原 `result.kind` 识别。

Project metadata 的 schema_version 仍为 1；事务布局单独版本化。未知布局版本或迁移状态被拒绝。升级后的事务布局不能由旧版恢复器处理，旧版会拒绝其目录结构；不要用旧版程序写入已迁移 Project。没有新增降级工具。

## 显式维护

ProjectService 提供 `maintain_transactions(project)`；CLI 提供 `/project maintenance`，不调用模型且不要求选择 Session。命令先恢复/收尾 pending，再清理 garbage、遗留 staging 和 receipt 写入中断留下的临时文件，报告待恢复/收尾数、正式凭据数、待清理数、暂存项数、文件字节占用、净回收字节及维护警告。

目录与字节统计仅在显式维护时遍历历史；普通读取没有完整历史统计开销。字节统计是文件内容大小之和，不是文件系统分配空间。净回收字节不包含命令之前已经自动删除的副本。

正式 receipt 不设置过期时间，因此少量幂等元数据仍随 Commit 增长。需要改变这一点时，必须另行决定幂等契约的有效期。

## 验证

确定性验证覆盖凭据清理后的永久幂等性、内部修订历史保留、旧完成/未完成及 Artifact 事务迁移、迁移中断、凭据冲突、清理 I/O 失败及重试、链接路径保护、CLI 统计和未知布局拒绝。子进程硬退出覆盖发布前后、应用中、COMPLETED 保存后、receipt 保存后、移动到 garbage 后以及删除中。大量历史凭据的访问拦截测试要求普通操作既不枚举也不读取历史 receipt。

2026-10-02 验收：unit / integration 共 219 项通过，其中本次事务存储专项 27 项；真实模型连通验收 2 项通过（deepseek-flash、grok-4.7）；Ruff 与差异空白检查通过。迁移及故障测试均使用临时 Project，未对现有小说 Project 执行迁移。
