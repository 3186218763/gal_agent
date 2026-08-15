from __future__ import annotations

import pytest

from src.story.runtime.contracts import (
    ChoicePlan,
    DirectorOutput,
    ModelContractError,
    PacingEnvelope,
    ScenePlan,
    SegmentPlan,
)
from src.story.runtime.director import DIRECTOR_INSTRUCTIONS, LLMDirector
from src.story.script_pack import compile_source
from src.story.state import StoryPhase, initial_session_state
from tests.fakes import StubLLMClient, json_reply
from tests.story_factories import minimal_script_pack_dict


@pytest.fixture
def pack():
    return compile_source(minimal_script_pack_dict())


@pytest.fixture
def state(pack):
    return initial_session_state(pack, "session_01", session_seed=42)


@pytest.fixture
def pacing():
    return PacingEnvelope(
        phase=StoryPhase.EXPLORATION,
        scene_count=5,
        min_scenes=8,
        max_scenes=20,
        reserved_resolution_scenes=3,
        remaining_budget=15,
        can_end=False,
        must_end=False,
        in_convergence=False,
        max_new_threads=2,
        quiet_scene_allowance=1,
        target_block_range=(8, 25),
    )


def valid_segment_plan() -> SegmentPlan:
    return SegmentPlan(
        segment_id="seg_01",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="Alice considers the situation.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="continue",
            ),
            ScenePlan(
                scene_id="scene_02",
                summary="The protagonist must choose.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id="dec_01",
                choices=(
                    ChoicePlan(option_id="opt_ask", action_id="ask", intent="ask directly"),
                    ChoicePlan(
                        option_id="opt_observe", action_id="observe", intent="watch carefully"
                    ),
                ),
            ),
        ),
        terminal="decision",
    )


def valid_director_output() -> DirectorOutput:
    return DirectorOutput(segment_plan=valid_segment_plan())


def test_director_output_supports_strict_json_schema():
    from src.story.runtime.model import build_output_schema

    schema = build_output_schema(DirectorOutput)
    assert schema["additionalProperties"] is False
    plan = schema["properties"]["segment_plan"]
    assert plan["properties"]["segment_id"]["type"] == "string"


def test_director_instructions_forbid_prose():
    assert "prose" in DIRECTOR_INSTRUCTIONS.lower() or "narration" in DIRECTOR_INSTRUCTIONS.lower()
    assert "structured" in DIRECTOR_INSTRUCTIONS.lower() or "plan" in DIRECTOR_INSTRUCTIONS.lower()


@pytest.mark.asyncio
async def test_director_returns_segment_plan(pack, state, pacing):
    client = StubLLMClient(replies=[json_reply(valid_director_output())])
    director = LLMDirector(client)
    plan = await director.plan_segment(pack, state, pacing)
    assert plan.segment_id == "seg_01"
    assert len(plan.scenes) == 2
    assert plan.terminal == "decision"

    request = client.requests[0]
    assert request["instructions"] == DIRECTOR_INSTRUCTIONS
    payload = request["payload"]
    assert payload["operation"] == "plan_segment"
    assert "pacing" in payload["context"]


@pytest.mark.asyncio
async def test_director_contract_error_retries_once(pack, state, pacing):
    client = StubLLMClient(
        replies=[
            "invalid output",
            json_reply(valid_director_output()),
        ]
    )
    director = LLMDirector(client)
    plan = await director.plan_segment(pack, state, pacing)
    assert plan.segment_id == "seg_01"
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_director_second_failure_raises_contract_error(pack, state, pacing):
    client = StubLLMClient(replies=["still broken", "still broken"])
    director = LLMDirector(client)
    with pytest.raises(ModelContractError, match="structured output failed after repair"):
        await director.plan_segment(pack, state, pacing)
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_director_ending_proposal(pack, state, pacing):
    from src.story.runtime.contracts import EndingProposal

    ending_plan = SegmentPlan(
        segment_id="seg_end",
        scenes=(
            ScenePlan(
                scene_id="scene_final",
                summary="The story concludes.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="ending",
            ),
        ),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Farewell",
            tone="bittersweet",
            terminal_state_summary="They part ways.",
        ),
    )
    client = StubLLMClient(replies=[json_reply(DirectorOutput(segment_plan=ending_plan))])
    director = LLMDirector(client)
    plan = await director.plan_segment(pack, state, pacing)
    assert plan.terminal == "ending"
    assert plan.ending_proposal is not None
    assert plan.ending_proposal.title == "Farewell"
