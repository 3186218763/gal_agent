from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from agents import Runner
from agents.exceptions import ModelBehaviorError
from agents.models.interface import Model

from src.story.runtime.contracts import (
    ChoicePlan,
    DirectorOutput,
    ModelContractError,
    PacingEnvelope,
    ScenePlan,
    SegmentPlan,
)
from src.story.runtime.director import DIRECTOR_INSTRUCTIONS, SdkDirector
from src.story.script_pack import compile_source
from src.story.state import StoryPhase, initial_session_state
from tests.story_factories import minimal_script_pack_dict


class SharedFakeModel(Model):
    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network model calls are not allowed in offline tests")

    async def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network model calls are not allowed in offline tests")
        if False:  # pragma: no cover
            yield None


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
    )


@pytest.fixture
def shared_model():
    return SharedFakeModel()


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
                    ChoicePlan(option_id="opt_observe", action_id="observe", intent="watch carefully"),
                ),
            ),
        ),
        terminal="decision",
    )


def valid_director_output() -> DirectorOutput:
    return DirectorOutput(segment_plan=valid_segment_plan())


def test_director_output_supports_strict_json_schema():
    from agents.agent_output import AgentOutputSchema

    assert AgentOutputSchema(DirectorOutput).is_strict_json_schema() is True


def test_director_instructions_forbid_prose():
    assert "prose" in DIRECTOR_INSTRUCTIONS.lower() or "narration" in DIRECTOR_INSTRUCTIONS.lower()
    assert "structured" in DIRECTOR_INSTRUCTIONS.lower() or "plan" in DIRECTOR_INSTRUCTIONS.lower()


@pytest.mark.asyncio
async def test_director_returns_segment_plan(monkeypatch, shared_model, pack, state, pacing):
    async def fake_run(agent, input):
        payload = json.loads(input)
        assert payload["operation"] == "plan_segment"
        assert "context" in payload
        assert "pacing" in payload["context"]
        return SimpleNamespace(final_output=valid_director_output())

    monkeypatch.setattr(Runner, "run", fake_run)
    director = SdkDirector(shared_model)
    plan = await director.plan_segment(pack, state, pacing)
    assert plan.segment_id == "seg_01"
    assert len(plan.scenes) == 2
    assert plan.terminal == "decision"
    assert director.agent.model is shared_model


@pytest.mark.asyncio
async def test_director_contract_error_retries_once(monkeypatch, shared_model, pack, state, pacing):
    calls = 0

    async def fake_run(agent, input):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelBehaviorError("invalid output")
        return SimpleNamespace(final_output=valid_director_output())

    monkeypatch.setattr(Runner, "run", fake_run)
    director = SdkDirector(shared_model)
    plan = await director.plan_segment(pack, state, pacing)
    assert plan.segment_id == "seg_01"
    assert calls == 2


@pytest.mark.asyncio
async def test_director_second_failure_raises_contract_error(
    monkeypatch, shared_model, pack, state, pacing
):
    calls = 0

    async def fake_run(agent, input):
        nonlocal calls
        calls += 1
        raise ModelBehaviorError("still broken")

    monkeypatch.setattr(Runner, "run", fake_run)
    director = SdkDirector(shared_model)
    with pytest.raises(ModelContractError, match="structured output failed after repair"):
        await director.plan_segment(pack, state, pacing)
    assert calls == 2


@pytest.mark.asyncio
async def test_director_ending_proposal(monkeypatch, shared_model, pack, state, pacing):
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

    async def fake_run(agent, input):
        return SimpleNamespace(final_output=DirectorOutput(segment_plan=ending_plan))

    monkeypatch.setattr(Runner, "run", fake_run)
    director = SdkDirector(shared_model)
    plan = await director.plan_segment(pack, state, pacing)
    assert plan.terminal == "ending"
    assert plan.ending_proposal is not None
    assert plan.ending_proposal.title == "Farewell"
