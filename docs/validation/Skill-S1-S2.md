# Skill S1–S2 验证记录

只读 Skill 已接入 Project 本地目录、内置库、Session CLI 设置、Freeform DeepAgent 与六类 Workflow 的共用生成边界。`create_deep_agent` 使用虚拟 `/skills/` 文件映射并拒绝 Skill 路径写入；真实 Project root 没有挂到 Deep Agents 文件系统。Workflow Request 只保存 Skill 名称、来源和内容摘要，内容快照在 `.novel-agent/skill-snapshots/`，重开 Session 后继续使用同一快照。Artifact revision 保存 Skill 引用，Commit 仍应用用户看到的精确内容。导入 Workflow 的原始正文不经模型改写。

本次使用仓库 `.env` 配置的 `deepseek-flash` 执行四项真实模型用例：CLI 启用并选用 Project Skill → Freeform 生成 Outline Artifact（未 Commit）→ Workflow 到达 PLAN Approval → 修改源 Skill → 重新创建 CLI 并恢复 → MANUSCRIPT Artifact 仍引用原 Skill 摘要；用户明确的本次风格要求覆盖 Skill 建议；多章流程的后续章节大纲保留 Skill 引用、导入正文与源文件完全一致；续写从正式前章生成带 Skill 引用的候选大纲。四项均通过，未断言具体文学措辞。

验证命令与结果：

```powershell
uv run --frozen --extra agent --extra cli pytest -q
# 85 passed
uv run --frozen --extra agent --extra cli ruff check src tests
# All checks passed!
$env:RUN_REAL_LLM_TESTS = "1"
uv run --frozen --extra agent --extra cli pytest tests/real_llm/test_skill_e2e.py -q -s
# 四项用例分次运行并通过；model=deepseek-flash
uv build --offline
# wheel 与 sdist 构建成功；wheel 包含内置 SKILL.md
```

确定性测试还覆盖 Project Skill 默认禁用、Session 启用设置、Skill 快照在源文件修改后保持不变、非法 frontmatter/链接拒绝，以及导入时 Skill 不改变源正文。脚本执行、远程安装和 Skill 自动编写仍不在当前范围。
