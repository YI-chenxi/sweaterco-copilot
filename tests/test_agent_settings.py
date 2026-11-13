"""Tests for environment-backed LLM configuration."""

import pytest

from app.agent.llm_client import LLMClientError, create_llm_client
from app.agent.settings import LLMSettings


def test_llm_settings_load_openai_configuration_from_environment(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "12.5")

    settings = LLMSettings.from_env(load_dotenv_file=False)

    assert settings.provider == "openai"
    assert settings.model == "test-model"
    assert settings.api_key == "test-key"
    assert settings.timeout_seconds == 12.5


def test_llm_settings_reject_unknown_provider():
    with pytest.raises(ValueError, match="MODEL_PROVIDER"):
        LLMSettings(provider="unsupported")


def test_openai_provider_requires_api_key():
    settings = LLMSettings(provider="openai", api_key=None)

    with pytest.raises(LLMClientError, match="OPENAI_API_KEY"):
        create_llm_client(settings)


def test_mock_provider_does_not_require_api_key():
    client = create_llm_client(LLMSettings(provider="mock"))

    assert client.__class__.__name__ == "MockLLMClient"
