"""Tests for the streaming scene generator."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.story.runtime.stream_writer import (
    STREAMING_WRITER_INSTRUCTIONS,
    StreamingSceneGenerator,
)


def _make_streaming_response(deltas: list[str]):
    """Build a mock async iterator that yields delta events."""

    class DeltaEvent:
        type = "response.output_text.delta"

        def __init__(self, delta: str) -> None:
            self.delta = delta

    events = [DeltaEvent(d) for d in deltas]

    class FakeStream:
        def __init__(self) -> None:
            self._events = list(events)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._events:
                raise StopAsyncIteration
            return self._events.pop(0)

    return FakeStream()


def _minimal_pack_and_state():
    """Build a minimal pack and state for testing."""
    from src.story.script_pack.compiler import compile_source
    from src.story.state import initial_session_state
    from tests.story_factories import minimal_script_pack_dict

    raw = minimal_script_pack_dict()
    pack = compile_source(raw)
    state = initial_session_state(pack, "test-session", session_seed=42)
    return pack, state


@pytest.mark.asyncio
async def test_generate_scene_yields_blocks_then_complete():
    full_output = json.dumps({
        "blocks": [
            {"kind": "narration", "text": "The cafe hums quietly."},
            {"kind": "dialogue", "character_id": "alice", "text": "Hello there."},
        ],
        "terminal": "decision",
        "decision_id": "d_1",
        "choices": [
            {"option_id": "opt_1", "action_id": "ask", "label": "Ask", "intent": "ask"},
        ],
    })
    # Split into small chunks to simulate streaming
    chunk_size = 8
    deltas = [full_output[i : i + chunk_size] for i in range(0, len(full_output), chunk_size)]

    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(return_value=_make_streaming_response(deltas))

    generator = StreamingSceneGenerator(mock_client, "test-model")
    pack, state = _minimal_pack_and_state()

    events = []
    async for event_type, data in generator.generate_scene(pack, state):
        events.append((event_type, data))

    # Should have 2 block events + 1 complete event
    block_events = [e for e in events if e[0] == "block"]
    complete_events = [e for e in events if e[0] == "complete"]
    assert len(block_events) == 2
    assert len(complete_events) == 1
    assert block_events[0][1]["text"] == "The cafe hums quietly."
    assert block_events[1][1]["character_id"] == "alice"
    assert complete_events[0][1]["terminal"] == "decision"


@pytest.mark.asyncio
async def test_generate_scene_raises_on_unparseable_output():
    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(
        return_value=_make_streaming_response(["not json at all"])
    )

    generator = StreamingSceneGenerator(mock_client, "test-model")
    pack, state = _minimal_pack_and_state()

    from src.story.runtime.contracts import ModelContractError

    with pytest.raises(ModelContractError):
        async for _ in generator.generate_scene(pack, state):
            pass
