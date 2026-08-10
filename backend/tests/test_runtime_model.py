from __future__ import annotations

from pydantic import SecretStr

import src.story.runtime.model as model_module
from src.story.runtime.config import OpenCodeGoSettings
from src.story.runtime.model import build_model_bundle


def make_test_settings() -> OpenCodeGoSettings:
    return OpenCodeGoSettings(api_key=SecretStr("test-secret"))


def test_build_model_uses_responses_model(monkeypatch):
    captured = {}
    tracing = {"disabled": False}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeResponsesModel:
        def __init__(self, *, model, openai_client):
            self.model = model
            self.openai_client = openai_client

    def fake_set_tracing_disabled(value: bool) -> None:
        tracing["disabled"] = value

    monkeypatch.setattr(model_module, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(model_module, "OpenAIResponsesModel", FakeResponsesModel)
    monkeypatch.setattr(model_module, "set_tracing_disabled", fake_set_tracing_disabled)
    bundle = build_model_bundle(make_test_settings())
    assert bundle.model.model == "deepseek-v4-flash"
    assert captured["base_url"] == "https://opencode.ai/zen/go/v1"
    assert captured["max_retries"] == 1
    assert captured["timeout"] == 45
    assert captured["api_key"] == "test-secret"
    assert tracing["disabled"] is True
    assert bundle.model.openai_client is bundle.client


def test_build_model_does_not_use_chat_completions(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

    class FakeResponsesModel:
        def __init__(self, *, model, openai_client):
            self.model = model
            self.openai_client = openai_client

    monkeypatch.setattr(model_module, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(model_module, "OpenAIResponsesModel", FakeResponsesModel)
    monkeypatch.setattr(model_module, "set_tracing_disabled", lambda _v: None)
    bundle = build_model_bundle(make_test_settings())
    assert type(bundle.model).__name__ == "FakeResponsesModel"
