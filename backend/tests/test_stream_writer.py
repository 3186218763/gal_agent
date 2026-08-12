"""Tests for the plan-consuming streaming segment writer adapter."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import AsyncOpenAI

from src.story.runtime.contracts import (
    ChoicePlan,
    ScenePlan,
    SegmentPlan,
)
from src.story.runtime.stream_writer import StreamingSceneGenerator
from src.story.script_pack import compile_source
from src.story.state import initial_session_state
from tests.story_factories import minimal_script_pack_dict


@pytest.fixture
def pack():
    return compile_source(minimal_script_pack_dict())


@pytest.fixture
def state(pack):
    return initial_session_state(pack, "session_01", session_seed=42)


def _approved_plan() -> SegmentPlan:
    return SegmentPlan(
        segment_id="seg_01",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="A quiet moment.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="continue",
            ),
            ScenePlan(
                scene_id="scene_02",
                summary="Decision.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id="dec_01",
                choices=(
                    ChoicePlan(option_id="opt_a", action_id="ask", intent="ask"),
                    ChoicePlan(option_id="opt_b", action_id="observe", intent="watch"),
                ),
            ),
        ),
        terminal="decision",
    )


class FakeStreamEvent:
    def __init__(self, event_type: str, delta: str = "") -> None:
        self.type = event_type
        self.delta = delta


class FakeStream:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        self._idx = 0
        return self

    async def __anext__(self):
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._idx]
        self._idx += 1
        return FakeStreamEvent("response.output_text.delta", chunk)


@pytest.mark.asyncio
async def test_streaming_adapter_consumes_approved_plan(pack, state):
    """The streaming adapter should use the approved plan to build its prompt,
    not invent facts, choices, or terminal states."""
    plan = _approved_plan()

    # Build a valid JSON output matching the plan
    output_json = json.dumps({
        "segment_draft": {
            "segment_id": "seg_01",
            "scene_drafts": [
                {
                    "scene_id": "scene_01",
                    "blocks": [
                        {"kind": "narration", "text": "The cafe was quiet."},
                    ],
                },
                {
                    "scene_id": "scene_02",
                    "blocks": [
                        {"kind": "narration", "text": "Alice looked up."},
                        {"kind": "dialogue", "character_id": "alice", "text": "Well?"},
                    ],
                    "choices": [
                        {"option_id": "opt_a", "label": "Ask"},
                        {"option_id": "opt_b", "label": "Watch"},
                    ],
                },
            ],
            "choices": [
                {"option_id": "opt_a", "label": "Ask"},
                {"option_id": "opt_b", "label": "Watch"},
            ],
        },
    })

    mock_client = MagicMock(spec=AsyncOpenAI)
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(
        return_value=FakeStream([output_json])
    )

    generator = StreamingSceneGenerator(mock_client, "deepseek-v4-flash")
    events = []
    async for event_type, data in generator.generate_segment(pack, state, plan):
        events.append((event_type, data))

    assert any(et == "block" for et, _ in events)
    assert events[-1][0] == "complete"


@pytest.mark.asyncio
async def test_streaming_adapter_builds_prompt_from_plan(pack, state):
    """Verify the adapter prompt references the plan, not just state."""
    plan = _approved_plan()
    captured_kwargs: dict[str, Any] = {}

    output_json = json.dumps({
        "segment_draft": {
            "segment_id": "seg_01",
            "scene_drafts": [
                {
                    "scene_id": "scene_01",
                    "blocks": [{"kind": "narration", "text": "test"}],
                },
                {
                    "scene_id": "scene_02",
                    "blocks": [{"kind": "narration", "text": "test2"}],
                    "choices": [
                        {"option_id": "opt_a", "label": "A"},
                        {"option_id": "opt_b", "label": "B"},
                    ],
                },
            ],
            "choices": [
                {"option_id": "opt_a", "label": "A"},
                {"option_id": "opt_b", "label": "B"},
            ],
        },
    })

    async def fake_create(**kwargs):
        captured_kwargs.update(kwargs)
        return FakeStream([output_json])

    mock_client = MagicMock(spec=AsyncOpenAI)
    mock_client.responses = MagicMock()
    mock_client.responses.create = fake_create

    generator = StreamingSceneGenerator(mock_client, "deepseek-v4-flash")
    events = []
    async for event_type, data in generator.generate_segment(pack, state, plan):
        events.append((event_type, data))

    # The prompt should contain the plan
    prompt_str = captured_kwargs.get("input", "")
    assert "seg_01" in prompt_str


@pytest.mark.asyncio
async def test_streaming_segment_parses_json(pack, state):
    """Verify the streaming adapter parses JSON output correctly."""
    plan = _approved_plan()
    output_json = json.dumps({
        "segment_draft": {
            "segment_id": "seg_01",
            "scene_drafts": [
                {"scene_id": "scene_01", "blocks": [{"kind": "narration", "text": "test"}]},
            ],
            "choices": [],
        },
    })

    mock_client = MagicMock(spec=AsyncOpenAI)
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(return_value=FakeStream([output_json]))

    generator = StreamingSceneGenerator(mock_client, "deepseek-v4-flash")
    events = []
    async for event_type, data in generator.generate_segment(pack, state, plan):
        events.append((event_type, data))

    assert events[-1][0] == "complete"
    assert "segment_draft" in events[-1][1]


@pytest.mark.asyncio
async def test_streaming_segment_validates_output(pack, state):
    """Verify invalid JSON is rejected with ModelContractError."""
    plan = _approved_plan()
    invalid_json = "{invalid json"

    mock_client = MagicMock(spec=AsyncOpenAI)
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(return_value=FakeStream([invalid_json]))

    from src.story.runtime.contracts import ModelContractError

    generator = StreamingSceneGenerator(mock_client, "deepseek-v4-flash")
    with pytest.raises(ModelContractError, match="could not be parsed"):
        async for _ in generator.generate_segment(pack, state, plan):
            pass


@pytest.mark.asyncio
async def test_streaming_segment_rejects_wrong_shape_output(pack, state):
    """Valid JSON of the wrong shape (e.g. the legacy scene format) is
    rejected with ModelContractError, matching the unparseable branch."""
    plan = _approved_plan()
    wrong_shape_json = json.dumps({
        "blocks": [{"kind": "narration", "text": "test"}],
        "terminal": "decision",
        "choices": [],
    })

    mock_client = MagicMock(spec=AsyncOpenAI)
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(return_value=FakeStream([wrong_shape_json]))

    from src.story.runtime.contracts import ModelContractError

    generator = StreamingSceneGenerator(mock_client, "deepseek-v4-flash")
    with pytest.raises(ModelContractError, match="could not be validated"):
        async for _ in generator.generate_segment(pack, state, plan):
            pass
