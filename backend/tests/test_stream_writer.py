"""Tests for the plan-consuming streaming segment writer adapter."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.story.runtime.contracts import (
    ChoicePlan,
    ScenePlan,
    SegmentPlan,
)
from src.story.runtime.stream_writer import (
    STREAMING_WRITER_INSTRUCTIONS,
    StreamingSceneGenerator,
)
from src.story.script_pack import compile_source
from src.story.state import initial_session_state
from tests.fakes import StubLLMClient
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


def _stream_client(chunks: list[str]) -> StubLLMClient:
    """A StubLLMClient whose stream_text replays the given text deltas."""
    client = StubLLMClient(replies=chunks)
    return client


@pytest.mark.asyncio
async def test_streaming_adapter_consumes_approved_plan(pack, state):
    """The streaming adapter should use the approved plan to build its prompt,
    not invent facts, choices, or terminal states."""
    plan = _approved_plan()

    # Build a valid JSON output matching the plan
    output_json = json.dumps(
        {
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
        }
    )

    generator = StreamingSceneGenerator(_stream_client([output_json]))
    events = []
    async for event_type, data in generator.generate_segment(pack, state, plan):
        events.append((event_type, data))

    assert any(et == "block" for et, _ in events)
    assert events[-1][0] == "complete"


@pytest.mark.asyncio
async def test_streaming_adapter_builds_prompt_from_plan(pack, state):
    """Verify the adapter prompt references the plan, not just state."""
    plan = _approved_plan()

    output_json = json.dumps(
        {
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
        }
    )

    client = _stream_client([output_json])
    generator = StreamingSceneGenerator(client)
    events = []
    async for event_type, data in generator.generate_segment(pack, state, plan):
        events.append((event_type, data))

    # The prompt was built from the plan and sent through stream_text
    request = client.requests[0]
    assert request["instructions"] == STREAMING_WRITER_INSTRUCTIONS
    prompt_str = json.dumps(request["payload"], ensure_ascii=False)
    assert request["payload"]["operation"] == "write_segment"
    assert "seg_01" in prompt_str


@pytest.mark.asyncio
async def test_streaming_segment_parses_json(pack, state):
    """Verify the streaming adapter parses JSON output correctly."""
    plan = _approved_plan()
    output_json = json.dumps(
        {
            "segment_draft": {
                "segment_id": "seg_01",
                "scene_drafts": [
                    {"scene_id": "scene_01", "blocks": [{"kind": "narration", "text": "test"}]},
                ],
                "choices": [],
            },
        }
    )

    generator = StreamingSceneGenerator(_stream_client([output_json]))
    events: list[tuple[str, Any]] = []
    async for event_type, data in generator.generate_segment(pack, state, plan):
        events.append((event_type, data))

    assert events[-1][0] == "complete"
    assert "segment_draft" in events[-1][1]


@pytest.mark.asyncio
async def test_streaming_segment_validates_output(pack, state):
    """Verify invalid JSON is rejected with ModelContractError."""
    plan = _approved_plan()
    invalid_json = "{invalid json"

    from src.story.runtime.contracts import ModelContractError

    generator = StreamingSceneGenerator(_stream_client([invalid_json]))
    with pytest.raises(ModelContractError, match="could not be parsed"):
        async for _ in generator.generate_segment(pack, state, plan):
            pass


@pytest.mark.asyncio
async def test_streaming_segment_rejects_wrong_shape_output(pack, state):
    """Valid JSON of the wrong shape (e.g. the legacy scene format) is
    rejected with ModelContractError, matching the unparseable branch."""
    plan = _approved_plan()
    wrong_shape_json = json.dumps(
        {
            "blocks": [{"kind": "narration", "text": "test"}],
            "terminal": "decision",
            "choices": [],
        }
    )

    from src.story.runtime.contracts import ModelContractError

    generator = StreamingSceneGenerator(_stream_client([wrong_shape_json]))
    with pytest.raises(ModelContractError, match="could not be validated"):
        async for _ in generator.generate_segment(pack, state, plan):
            pass
