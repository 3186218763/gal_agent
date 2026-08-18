"""Opt-in live capability test for OpenCode Go Responses runtime.

Skipped unless RUN_LIVE_ZEN_TEST=1. Requires GAL_LLM_PROVIDER=opencode_go
and OPENCODE_GO_API_KEY (or OPENAI_API_KEY alias). Does not call Chat Completions.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.config import LLMSettings
from src.story.runtime.contracts import ChoicePlan, ScenePlan
from src.story.runtime.guard import Guard
from src.story.runtime.model import LLMClient
from src.story.runtime.planner import LLMPlanner
from src.story.runtime.segment_contracts import SegmentPlan
from src.story.runtime.segment_writer import LLMSegmentWriter
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.validator import validate_scene_plan
from src.story.script_pack import compile_script_pack
from src.story.state import initial_session_state
from src.story.storage import StoryEventStore

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_deepseek_responses_runs_one_v2_choice_roundtrip(tmp_path):
    if os.getenv("RUN_LIVE_ZEN_TEST") != "1":
        pytest.skip("set RUN_LIVE_ZEN_TEST=1 to run provider tests")
    settings = LLMSettings.from_env()
    assert settings.api == "responses"
    client = LLMClient(settings)
    assert client.api == "responses"
    planner = LLMPlanner(client)
    pack = compile_script_pack(Path("script_packs/yokai_after_school"))
    store = StoryEventStore(tmp_path / "live.db")
    state = initial_session_state(pack, "live-capability", 17)
    store.create_session(state)

    planner_probe = await planner.plan_scene(pack, state)
    validate_scene_plan(pack, state, planner_probe)

    actions = sorted(pack.action_ids & set(pack.source.protagonist.capabilities))[:2]
    deterministic_scene = ScenePlan(
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

    class FixedSegmentDirector:
        async def plan_segment(self, pack, state, pacing):
            return SegmentPlan(
                segment_id="live_segment_01",
                scenes=(deterministic_scene,),
                terminal="decision",
            )

    orchestrator = TurnOrchestrator(
        store,
        FixedSegmentDirector(),
        LLMSegmentWriter(client),
        Guard(),
        CompletionJudge(),
        planner=planner,
    )

    events: list[tuple[str, dict]] = []
    async for event_type, data in orchestrator.execute_turn(
        pack, state.session_id, state.revision, "live-opening-1", None
    ):
        events.append((event_type, data))
    assert [t for t, _ in events if t == "error"] == []
    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "decision"
    assert len(ready["choices"]) == 2
    assert any(t == "block" for t, _ in events)

    selected = ready["choices"][0]
    followup: list[tuple[str, dict]] = []
    async for event_type, data in orchestrator.execute_turn(
        pack, state.session_id, ready["revision"], "live-capability-choice", selected["id"]
    ):
        followup.append((event_type, data))
    assert [t for t, _ in followup if t == "error"] == []
    ready2 = next(data for t, data in followup if t == "segment_ready")
    assert ready2["revision"] > ready["revision"]

    replayed = store.load_session(state.session_id)
    assert replayed.revision == ready2["revision"]
    selected_events = [
        e for e in store.load_events(state.session_id) if e.event.type == "player_action_selected"
    ]
    resolved_events = [
        e for e in store.load_events(state.session_id) if e.event.type == "action_resolved"
    ]
    assert len(selected_events) == 1
    assert len(resolved_events) == 1
    assert resolved_events[0].event.source_choice_event_id == selected_events[0].event_id
