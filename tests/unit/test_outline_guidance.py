import asyncio

from langchain_core.messages import AIMessage

from novel_agent.agent import freeform
from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
    build_session_service,
)
from novel_agent.domain.outline_structure import OUTLINE_STRUCTURE_GUIDANCE
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.workflow import generator

REQUIRED_HEADINGS = (
    "## 背景",
    "## 情节结构",
    "## 出场人物",
    "## 场景规划",
    "## 叙事策略",
    "## 辅助资料",
)


def test_workflow_outline_prompt_uses_shared_structure_without_enforcing_output(monkeypatch):
    prompts = []

    class FakeModel:
        def invoke(self, prompt):
            prompts.append(prompt)
            return AIMessage(content="模型返回的大纲原文")

    monkeypatch.setattr(generator, "build_chat_model", lambda *_: FakeModel())
    produced = generator.LLMChapterGenerator(None).generate(
        "outline", "创作第一章", "Project 设定", feedback="增加悬念"
    )

    assert produced == "模型返回的大纲原文"
    assert all(heading in prompts[0] for heading in REQUIRED_HEADINGS)
    assert [prompts[0].index(heading) for heading in REQUIRED_HEADINGS] == sorted(
        prompts[0].index(heading) for heading in REQUIRED_HEADINGS
    )
    assert "## 前情提要" in prompts[0]
    assert "首章或没有可靠前情时省略" in prompts[0]
    assert "增加悬念" in prompts[0]


def test_freeform_receives_same_outline_guidance(monkeypatch, tmp_path):
    captured = []

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):
            yield "updates", {"agent": {"messages": [AIMessage(content="回答")]}}

    monkeypatch.setattr(freeform, "build_chat_model", lambda *_: object())

    def create_agent(**kwargs):
        captured.append(kwargs)
        return FakeAgent()

    monkeypatch.setattr(freeform, "create_deep_agent", create_agent)
    projects = build_project_service()
    project = projects.create_project(tmp_path, "Outline", "Main")
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake-model", "https://example.invalid/v1", "none")},
        "fake",
    )
    agent = freeform.FreeformAgent(
        projects,
        build_session_service(),
        build_artifact_service(),
        build_commit_service(),
        models,
    )

    result = asyncio.run(agent.run(project, project.sessions[0], "你好"))
    assert result.response == "回答"
    prompt = captured[0]["system_prompt"]
    assert OUTLINE_STRUCTURE_GUIDANCE in prompt
    assert "除非用户明确指定其他格式" in prompt
    assert "你是唯一长期面向用户的小说 Main Agent" in prompt
    assert "正文 > 大纲 > 概述 > 参考资料" in prompt
    assert "提交用户已经看到的确切修订版本，不得重新生成内容" in prompt
    subagent = captured[0]["subagents"][0]
    assert "只读分析" in subagent["description"]
    assert "不得修改 Project 或 Artifact" in subagent["system_prompt"]
    descriptions = {item.name: item.description for item in captured[0]["tools"]}
    assert "保存未 Commit 的草稿" in descriptions["create_working_artifact"]
    assert "现有的基础版本号" in descriptions["revise_working_artifact"]
    assert "用户明确要求 Commit" in descriptions["commit_working_artifact"]
