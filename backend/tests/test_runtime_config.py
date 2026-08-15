from __future__ import annotations

import pytest
from pydantic import SecretStr

from src.story.runtime.config import ConfigurationError, LLMSettings


def test_opencode_go_settings_use_responses_defaults(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = LLMSettings.from_env()
    assert settings.provider == "opencode_go"
    assert settings.base_url == "https://opencode.ai/zen/go/v1"
    assert settings.model == "deepseek-v4-flash"
    assert settings.api == "responses"
    assert "test-secret" not in repr(settings)
    assert settings.api_key.get_secret_value() == "test-secret"


def test_everygpt_settings_use_chat_completions_defaults(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "everygpt")
    monkeypatch.setenv("EVERYGPT_API_KEY", "test-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = LLMSettings.from_env()
    assert settings.provider == "everygpt"
    assert settings.base_url == "https://api.everygpt.site/v1"
    assert settings.model == "gemini-3.7-flash"
    assert settings.api == "chat_completions"
    assert "test-secret" not in repr(settings)
    assert settings.api_key.get_secret_value() == "test-secret"


def test_everygpt_env_overrides_are_applied(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "everygpt")
    monkeypatch.setenv("EVERYGPT_API_KEY", "test-secret")
    monkeypatch.setenv("EVERYGPT_BASE_URL", "https://api.everygpt.site/v2")
    monkeypatch.setenv("GAL_LLM_MODEL", "gemini-3.7-pro")
    settings = LLMSettings.from_env()
    assert settings.base_url == "https://api.everygpt.site/v2"
    assert settings.model == "gemini-3.7-pro"


def test_conflicting_key_aliases_fail(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "one")
    monkeypatch.setenv("OPENAI_API_KEY", "two")
    with pytest.raises(ConfigurationError, match="both set with different values"):
        LLMSettings.from_env()


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    with pytest.raises(ConfigurationError, match="GAL_LLM_PROVIDER must be one of"):
        LLMSettings.from_env()


def test_matching_key_aliases_are_accepted(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "everygpt")
    monkeypatch.setenv("EVERYGPT_API_KEY", "same-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "same-secret")
    settings = LLMSettings.from_env()
    assert settings.api_key.get_secret_value() == "same-secret"


def test_openai_api_key_alias_alone_is_accepted(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "everygpt")
    monkeypatch.delenv("EVERYGPT_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "alias-secret")
    settings = LLMSettings.from_env()
    assert settings.api_key.get_secret_value() == "alias-secret"


def test_missing_key_fails(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "everygpt")
    monkeypatch.delenv("EVERYGPT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="EVERYGPT_API_KEY is required"):
        LLMSettings.from_env()


def test_api_flavor_must_match_provider(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-secret")
    monkeypatch.setenv("GAL_LLM_API", "chat_completions")
    with pytest.raises(ConfigurationError, match="GAL_LLM_API must be responses"):
        LLMSettings.from_env()

    monkeypatch.setenv("GAL_LLM_PROVIDER", "everygpt")
    monkeypatch.setenv("EVERYGPT_API_KEY", "test-secret")
    monkeypatch.setenv("GAL_LLM_API", "responses")
    with pytest.raises(ConfigurationError, match="GAL_LLM_API must be chat_completions"):
        LLMSettings.from_env()


def test_http_base_url_rejected_except_localhost():
    with pytest.raises(ValueError, match="base_url must use HTTPS"):
        LLMSettings(
            provider="everygpt",
            api_key=SecretStr("k"),
            base_url="http://example.com/v1",
            model="gemini-3.7-flash",
            api="chat_completions",
        )
    settings = LLMSettings(
        provider="everygpt",
        api_key=SecretStr("k"),
        base_url="http://localhost:8080/v1/",
        model="gemini-3.7-flash",
        api="chat_completions",
    )
    assert settings.base_url == "http://localhost:8080/v1"
