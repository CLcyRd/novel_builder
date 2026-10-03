"""Environment-driven, business-free chat model connectivity adapter."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


class ModelConfigurationError(ValueError):
    """The selected provider lacks a usable model or credential."""


class ModelInvocationError(RuntimeError):
    """The configured provider failed to return a valid chat response."""


@dataclass(frozen=True)
class ModelDefinition:
    alias: str
    model_id: str
    base_url: str
    api_key: str
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    provider: str
    requested_model: str
    returned_model: str
    content: str
    usage: dict | None


class ModelGateway:
    def __init__(self, definitions: dict[str, ModelDefinition], default_model: str) -> None:
        if default_model not in definitions:
            raise ModelConfigurationError("Default model is not configured")
        self._definitions = definitions
        self.default_model = default_model

    @classmethod
    def from_env(cls, path: Path) -> ModelGateway:
        if not path.is_file():
            raise ModelConfigurationError(".env file is missing")
        config = dotenv_values(path)
        definitions = {}
        for alias in ("deepseek", "grok"):
            prefix = alias.upper() + "_MODEL_"
            model_id = (config.get(prefix + "ID") or "").strip()
            base_url = (config.get(prefix + "BASE_URL") or "").strip()
            api_key = (config.get(prefix + "API_KEY") or "").strip()
            if not any((model_id, base_url, api_key)):
                continue
            if not all((model_id, base_url, api_key)):
                raise ModelConfigurationError(f"Incomplete {alias} model configuration")
            if not base_url.startswith("https://"):
                raise ModelConfigurationError(f"{alias} base URL must use HTTPS")
            definitions[alias] = ModelDefinition(
                alias=alias,
                model_id=model_id,
                base_url=base_url,
                api_key=api_key,
                reasoning_effort=(config.get(prefix + "REASONING_EFFORT") or None),
            )
        if not definitions:
            raise ModelConfigurationError("No chat model is configured")
        default = (config.get("NOVEL_DEFAULT_MODEL") or next(iter(definitions))).strip().lower()
        return cls(definitions, default)

    def available_models(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def definition(self, alias: str | None = None) -> ModelDefinition:
        selected = alias or self.default_model
        try:
            return self._definitions[selected]
        except KeyError as exc:
            raise ModelConfigurationError(f"Unknown configured model: {selected}") from exc

    def invoke(
        self, excerpt: str, instruction: str, *, model: str | None = None, timeout: int = 45
    ) -> ModelResponse:
        alias = model or self.default_model
        if alias not in self._definitions:
            raise ModelConfigurationError(f"Unknown configured model: {alias}")
        definition = self._definitions[alias]
        body = {
            "model": definition.model_id,
            "messages": [
                {"role": "system", "content": "Summarize only the supplied Project excerpt."},
                {"role": "user", "content": f"Project excerpt:\n{excerpt}\n\nTask: {instruction}"},
            ],
            "max_tokens": 1024,
            "stream": False,
        }
        if definition.reasoning_effort:
            body["reasoning_effort"] = definition.reasoning_effort
        request = urllib.request.Request(
            definition.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + definition.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ModelInvocationError(f"{alias} returned HTTP {exc.code}") from exc
        except (OSError, ValueError) as exc:
            raise ModelInvocationError(f"{alias} request failed: {type(exc).__name__}") from exc
        choices = payload.get("choices") or []
        message = choices[0].get("message") if choices else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ModelInvocationError(f"{alias} returned no assistant content")
        return ModelResponse(
            provider=alias,
            requested_model=definition.model_id,
            returned_model=payload.get("model", ""),
            content=content,
            usage=payload.get("usage"),
        )
