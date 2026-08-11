"""Opt-in live capability test for OpenCode Go Responses runtime.

Skipped unless RUN_LIVE_ZEN_TEST=1. Requires GAL_LLM_PROVIDER=opencode_go
and OPENCODE_GO_API_KEY (or OPENAI_API_KEY alias). Does not call Chat Completions.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agents.models.openai_responses import OpenAIResponsesModel

from src.story.runtime.config import OpenCodeGoSettings
from src.story.runtime.contracts import ChoicePlan, ScenePlan
from src.story.runtime.model import build_model_bundle
from src.story.runtime.planner import SdkPlanner
from src.story.runtime.service import RuntimeService
from src.story.runtime.validator import validate_scene_plan
from src.story.runtime.writer import SdkWriter
from src.story.script_pack import compile_script_pack
from src.story.state import initial_session_state
from src.story.storage import StoryEventStore

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_deepseek_responses_runs_one_v2_choice_roundtrip(tmp_path):
    if os.getenv("RUN_LIVE_ZEN_TEST") != "1":
        pytest.skip("set RUN_LIVE_ZEN_TEST=1 to run provider tests")
    settings = OpenCodeGoSettings.from_env()
    assert settings.api == "responses"
    bundle = build_model_bundle(settings)
    assert isinstance(bundle.model, OpenAIResponsesModel)
    sdk_planner = SdkPlanner(bundle.model)
    writer = SdkWriter(bundle.model)
    pack = compile_script_pack(Path("script_packs/cafe_mystery"))
    store = StoryEventStore(tmp_path / "live.db")
    state = initial_session_state(pack, "live-capability", 17)
    store.create_session(state)

    planner_probe = await sdk_planner.plan_scene(pack, state)
    validate_scene_plan(pack, state, planner_probe)

    actions = sorted(pack.action_ids & set(pack.source.protagonist.capabilities))[:2]
    deterministic_plan = ScenePlan(
        scene_id="live_decision_01",
        summary="The protagonist considers two safe ways to continue the cafe investigation.",
        location_id=state.world.location_id,
        present_character_ids=state.world.present_character_ids,
        terminal="decision",
        decision_id="live_decision_01",
        choices=tuple(
            ChoicePlan(
                option_id=f"live_{action_id}",
                action_id=action_id,
                intent=f"Use {action_id} to continue the investigation",
            )
            for action_id in actions
        ),
    )

    class FixedScenePlanner:
        async def plan_scene(self, pack, state):
            return deterministic_plan

        async def resolve_action(self, pack, state, choice):
            return await sdk_planner.resolve_action(pack, state, choice)

    runtime = RuntimeService(store, FixedScenePlanner(), writer)
    scene = await runtime.advance(
        pack, state.session_id, state.revision, idempotency_key="live-advance-1"
    )
    assert scene.blocks
    assert len(scene.choices) == 2

    selected = scene.choices[0]
    result = await runtime.select_choice(
        pack,
        state.session_id,
        selected.id,
        expected_revision=scene.revision,
        idempotency_key="live-capability-choice",
    )
    replayed = store.load_session(state.session_id)
    assert result.revision == replayed.revision
    assert replayed.pending_decision is None
