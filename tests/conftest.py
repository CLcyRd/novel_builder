"""Deterministic tests explicitly abstain from network-backed automatic Skill selection."""

import pytest


@pytest.fixture(autouse=True)
def isolate_default_skill_selector(request, monkeypatch):
    if "real_llm" in request.node.keywords:
        return
    from novel_agent.services.workflow_skill import LLMSkillSelector, SkillChoices

    original = LLMSkillSelector.select

    def select(self, *args, model=None, **kwargs):
        if "example.invalid" in self.gateway.definition(model).base_url:
            return SkillChoices()
        return original(self, *args, model=model, **kwargs)

    monkeypatch.setattr(LLMSkillSelector, "select", select)
