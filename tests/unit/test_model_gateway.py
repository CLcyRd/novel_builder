import pytest

from novel_agent.infrastructure.models.gateway import ModelConfigurationError, ModelGateway


def test_gateway_loads_only_complete_configured_models(tmp_path):
    config = tmp_path / ".env"
    config.write_text(
        "NOVEL_DEFAULT_MODEL=deepseek\n"
        "DEEPSEEK_MODEL_ID=test-model\n"
        "DEEPSEEK_MODEL_API_KEY=example-only\n"
        "DEEPSEEK_MODEL_BASE_URL=https://example.invalid/v1\n",
        encoding="utf-8",
    )
    gateway = ModelGateway.from_env(config)
    assert gateway.available_models() == ("deepseek",)
    assert gateway.default_model == "deepseek"
    with pytest.raises(ModelConfigurationError, match="Unknown"):
        gateway.invoke("example", "summarize", model="grok")


def test_gateway_fails_early_for_missing_key_or_model(tmp_path):
    config = tmp_path / ".env"
    config.write_text(
        "DEEPSEEK_MODEL_ID=test-model\nDEEPSEEK_MODEL_BASE_URL=https://example.invalid/v1\n",
        encoding="utf-8",
    )
    with pytest.raises(ModelConfigurationError, match="Incomplete"):
        ModelGateway.from_env(config)
    with pytest.raises(ModelConfigurationError, match="missing"):
        ModelGateway.from_env(tmp_path / "absent.env")
