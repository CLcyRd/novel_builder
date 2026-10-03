"""LangChain adapter for the same environment-defined chat models as Phase 1."""

from langchain_openai import ChatOpenAI

from novel_agent.infrastructure.models.gateway import ModelGateway


def build_chat_model(gateway: ModelGateway, alias: str | None = None) -> ChatOpenAI:
    definition = gateway.definition(alias)
    return ChatOpenAI(
        model=definition.model_id,
        base_url=definition.base_url,
        api_key=definition.api_key,
        timeout=60,
        max_retries=1,
        streaming=True,
    )
