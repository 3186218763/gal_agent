from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

import src.story.runtime.model as model_module
from src.story.runtime.config import LLMSettings
from src.story.runtime.contracts import ModelContractError
from src.story.runtime.model import (
    LLMClient,
    build_output_schema,
    parse_model_json,
)


def opencode_settings() -> LLMSettings:
    return LLMSettings(
        provider="opencode_go",
        api_key=SecretStr("test-secret"),
        base_url="https://opencode.ai/zen/go/v1",
        model="deepseek-v4-flash",
        api="responses",
        timeout_seconds=45,
        max_retries=1,
    )


def everygpt_settings() -> LLMSettings:
    return LLMSettings(
        provider="everygpt",
        api_key=SecretStr("test-secret"),
        base_url="https://api.everygpt.site/v1",
        model="gemini-3.7-flash",
        api="chat_completions",
        timeout_seconds=30,
        max_retries=2,
    )


class FakeChatCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


class FakeChunk:
    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]


class FakeEmptyChunk:
    def __init__(self) -> None:
        self.choices = []


class FakeStream:
    """Async-iterable of chat-completions chunks yielding text deltas."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        self._idx = 0
        return self

    async def __anext__(self):
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk


def install_fake_openai(monkeypatch, *, chat_create=None, responses_create=None) -> dict:
    """Patch model_module.AsyncOpenAI; return the captured constructor kwargs."""
    captured: dict[str, Any] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=chat_create or _forbid("chat.create"))
            )
            self.responses = SimpleNamespace(create=responses_create or _forbid("responses.create"))

    monkeypatch.setattr(model_module, "AsyncOpenAI", FakeAsyncOpenAI)
    return captured


def _forbid(name: str):
    async def forbidden(**kwargs: Any) -> Any:
        raise AssertionError(f"unexpected {name} call in this test")

    return forbidden


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_client_construction_passes_provider_config(monkeypatch):
    captured = install_fake_openai(monkeypatch)
    client = LLMClient(everygpt_settings())
    assert captured["api_key"] == "test-secret"
    assert captured["base_url"] == "https://api.everygpt.site/v1"
    assert captured["timeout"] == 30
    assert captured["max_retries"] == 2
    assert client.api == "chat_completions"
    assert client.model == "gemini-3.7-flash"


def test_client_construction_responses_flavor(monkeypatch):
    captured = install_fake_openai(monkeypatch)
    client = LLMClient(opencode_settings())
    assert captured["base_url"] == "https://opencode.ai/zen/go/v1"
    assert client.api == "responses"
    assert client.model == "deepseek-v4-flash"


# ---------------------------------------------------------------------------
# _ask: one round trip per API flavor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_ask_omits_response_format(monkeypatch):
    """response_format is deliberately omitted: this project's providers
    treat json_schema as guidance and some corrupt nested arrays."""
    captured_kwargs: dict[str, Any] = {}

    async def fake_create(**kwargs):
        captured_kwargs.update(kwargs)
        return FakeChatCompletion('{"ok": true}')

    install_fake_openai(monkeypatch, chat_create=fake_create)
    client = LLMClient(everygpt_settings())
    reply = await client._ask("be strict", "the request", {"type": "object"})

    assert reply == '{"ok": true}'
    assert captured_kwargs["model"] == "gemini-3.7-flash"
    assert captured_kwargs["messages"][0]["role"] == "system"
    assert captured_kwargs["messages"][0]["content"] == "be strict"
    assert captured_kwargs["messages"][1]["role"] == "user"
    assert captured_kwargs["messages"][1]["content"] == "the request"
    assert "response_format" not in captured_kwargs


@pytest.mark.asyncio
async def test_responses_ask_sends_text_format_contract(monkeypatch):
    captured_kwargs: dict[str, Any] = {}

    async def fake_create(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(output_text='{"ok": true}')

    install_fake_openai(monkeypatch, responses_create=fake_create)
    client = LLMClient(opencode_settings())
    reply = await client._ask("be strict", "the request", {"type": "object", "properties": {}})

    assert reply == '{"ok": true}'
    assert captured_kwargs["model"] == "deepseek-v4-flash"
    text = captured_kwargs["text"]
    assert text["format"]["type"] == "json_schema"
    assert text["format"]["strict"] is True
    assert text["format"]["schema"] == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# complete_structured: validate, repair once, fail closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_structured_accepts_fenced_reply(monkeypatch):
    from pydantic import BaseModel

    class Toy(BaseModel):
        kind: str

    async def fake_create(**kwargs):
        return FakeChatCompletion('```json\n{"kind": "scene"}\n```')

    install_fake_openai(monkeypatch, chat_create=fake_create)
    client = LLMClient(everygpt_settings())
    result = await client.complete_structured(
        instructions="sys",
        payload={"operation": "toy"},
        output_type=Toy,
    )
    assert result.kind == "scene"


@pytest.mark.asyncio
async def test_complete_structured_repairs_once(monkeypatch):
    from pydantic import BaseModel

    class Toy(BaseModel):
        kind: str

    replies = iter(["not json", '{"kind": "scene"}'])
    calls: list[str] = []

    async def fake_create(**kwargs):
        payload = kwargs["messages"][1]["content"]
        calls.append(payload)
        return FakeChatCompletion(next(replies))

    install_fake_openai(monkeypatch, chat_create=fake_create)
    client = LLMClient(everygpt_settings())
    result = await client.complete_structured(
        instructions="sys",
        payload={"operation": "toy"},
        output_type=Toy,
    )
    assert result.kind == "scene"
    assert len(calls) == 2
    first = json.loads(calls[0])
    repair = json.loads(calls[1])
    assert first["operation"] == "toy"
    assert first["required_output_schema"]["properties"]["kind"]["type"] == "string"
    assert repair["operation"] == "repair_contract"
    assert repair["validation_error"]
    assert repair["required_output_schema"] == first["required_output_schema"]


@pytest.mark.asyncio
async def test_complete_structured_fails_closed_after_repair(monkeypatch):
    from pydantic import BaseModel

    class Toy(BaseModel):
        kind: str

    async def fake_create(**kwargs):
        return FakeChatCompletion("still not json")

    install_fake_openai(monkeypatch, chat_create=fake_create)
    client = LLMClient(everygpt_settings())
    with pytest.raises(ModelContractError, match="structured output failed after repair"):
        await client.complete_structured(
            instructions="sys",
            payload={"operation": "toy"},
            output_type=Toy,
        )


# ---------------------------------------------------------------------------
# stream_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_text_chat_yields_content_deltas(monkeypatch):
    async def fake_create(**kwargs):
        assert kwargs["stream"] is True
        assert kwargs["messages"][0]["content"] == "sys"
        assert kwargs["messages"][1]["content"] == "usr"
        return FakeStream([FakeChunk("hello "), FakeEmptyChunk(), FakeChunk("world")])

    install_fake_openai(monkeypatch, chat_create=fake_create)
    client = LLMClient(everygpt_settings())
    deltas = [delta async for delta in client.stream_text(system="sys", user="usr")]
    assert deltas == ["hello ", "world"]


@pytest.mark.asyncio
async def test_stream_text_responses_yields_delta_events(monkeypatch):
    async def fake_create(**kwargs):
        assert kwargs["input"] == "usr"
        assert kwargs["instructions"] == "sys"
        return FakeStream(
            [
                SimpleNamespace(type="response.output_text.delta", delta="hel"),
                SimpleNamespace(type="response.output_text.delta", delta="lo"),
                SimpleNamespace(type="response.completed", delta=""),
            ]
        )

    install_fake_openai(monkeypatch, responses_create=fake_create)
    client = LLMClient(opencode_settings())
    deltas = [delta async for delta in client.stream_text(system="sys", user="usr")]
    assert deltas == ["hel", "lo"]


# ---------------------------------------------------------------------------
# Schema building and JSON parsing
# ---------------------------------------------------------------------------


def test_build_output_schema_is_strict_and_inlined():
    from src.story.runtime.contracts import PlannerOutput

    schema = build_output_schema(PlannerOutput)
    dumped = json.dumps(schema)
    assert "$defs" not in schema
    assert "$ref" not in dumped
    assert schema["properties"]["kind"]["enum"] == ["scene", "resolution"]
    # every object schema forbids additional properties
    assert schema["additionalProperties"] is False
    scene = schema["properties"]["scene"]
    if "anyOf" in scene:
        assert all("type" in branch for branch in scene["anyOf"])


def test_parse_model_json_strips_code_fences():
    assert parse_model_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_model_json('```\n{"a": 1}\n```') == {"a": 1}
    assert parse_model_json('  {"a": 1}  ') == {"a": 1}


def test_parse_model_json_rejects_empty_output():
    with pytest.raises(ValueError, match="empty output"):
        parse_model_json("   \n")


def test_strip_code_fences():
    from src.story.runtime.model import _strip_code_fences

    assert _strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_code_fences('  {"a": 1}  ') == '{"a": 1}'
    assert _strip_code_fences("") == ""
