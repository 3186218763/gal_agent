from __future__ import annotations

import pytest
from pydantic import SecretStr

from src.story.runtime.config import ConfigurationError, OpenCodeGoSettings


def test_opencode_go_settings_use_responses_defaults(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = OpenCodeGoSettings.from_env()
    assert settings.provider == "opencode_go"
    assert settings.base_url == "https://opencode.ai/zen/go/v1"
    assert settings.model == "deepseek-v4-flash"
    assert settings.api == "responses"
    assert "test-secret" not in repr(settings)
    assert settings.api_key.get_secret_value() == "test-secret"


def test_conflicting_key_aliases_fail(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "one")
    monkeypatch.setenv("OPENAI_API_KEY", "two")
    with pytest.raises(ConfigurationError, match="both set with different values"):
        OpenCodeGoSettings.from_env()


def test_non_opencode_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-secret")
    with pytest.raises(ConfigurationError, match="GAL_LLM_PROVIDER must be opencode_go"):
        OpenCodeGoSettings.from_env()


def test_matching_key_aliases_are_accepted(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "same-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "same-secret")
    settings = OpenCodeGoSettings.from_env()
    assert settings.api_key.get_secret_value() == "same-secret"


def test_openai_api_key_alias_alone_is_accepted(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "alias-secret")
    settings = OpenCodeGoSettings.from_env()
    assert settings.api_key.get_secret_value() == "alias-secret"


def test_missing_key_fails(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="OPENCODE_GO_API_KEY is required"):
        OpenCodeGoSettings.from_env()


def test_non_responses_api_is_rejected(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-secret")
    monkeypatch.setenv("GAL_LLM_API", "chat_completions")
    with pytest.raises(ConfigurationError, match="GAL_LLM_API must be responses"):
        OpenCodeGoSettings.from_env()


def test_http_base_url_rejected_except_localhost():
    with pytest.raises(ValueError, match="base_url must use HTTPS"):
        OpenCodeGoSettings(
            api_key=SecretStr("k"),
            base_url="http://example.com/v1",
        )
    settings = OpenCodeGoSettings(
        api_key=SecretStr("k"),
        base_url="http://localhost:8080/v1/",
    )
    assert settings.base_url == "http://localhost:8080/v1"
