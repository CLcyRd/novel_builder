"""Model generation boundary for single-chapter workflow stages."""

from __future__ import annotations

from typing import Protocol

from novel_agent.infrastructure.models.gateway import ModelGateway, ModelInvocationError
from novel_agent.infrastructure.models.langchain import build_chat_model
from novel_agent.prompts.prompt import build_prompt, generation_output


class ChapterGenerator(Protocol):
    def generate(
        self,
        stage: str,
        instruction: str,
        context: str,
        *,
        feedback: str = "",
        model: str | None = None,
    ) -> str: ...


class LLMChapterGenerator:
    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def generate(
        self,
        stage: str,
        instruction: str,
        context: str,
        *,
        feedback: str = "",
        model: str | None = None,
    ) -> str:
        prompt = build_prompt(stage, instruction, context, feedback)
        response = build_chat_model(self.gateway, model).invoke(prompt)
        content = response.content
        if not isinstance(content, str) or not content.strip():
            raise ModelInvocationError(f"{stage} returned no content")
        return generation_output(content)
