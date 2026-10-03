from novel_agent.agent.freeform import _has_commit_intent


def test_explicit_commit_intent_does_not_confuse_negation():
    assert _has_commit_intent("把 Artifact a@2 正式提交到 Project")
    assert _has_commit_intent("commit this revision")
    assert not _has_commit_intent("生成大纲，不要提交")
    assert not _has_commit_intent("只读分析；不要创建 Artifact 或提交")
