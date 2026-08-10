from __future__ import annotations

from pathlib import Path

import pytest

from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    DecisionRequired,
    EndingDraft,
    RuntimeRevisionConflict,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.service import RuntimeService
from src.story.runtime.simulator import simulate_scene
from src.story.script_pack import compile_source
from src.story.state import NarrativeBlock, SessionStatus, initial_session_state
from src.story.storage import StoryEventStore
from tests.story_factories import minimal_script_pack_dict


class FakePlanner:
    async def plan_scene(self, pack, state):
        return valid_decision_plan()

    async def resolve_action(self, pack, state, choice):
        return ActionResolution(action_id=choice.action_id, outcome="success")


class FakeWriter:
    async def write_scene(self, pack, state, plan):
        return valid_scene_draft(plan)

    async def write_ending(self, pack, state, ending):
        return valid_ending_draft(ending)


def valid_decision_plan() -> ScenePlan:
    return ScenePlan(
        scene_id="scene_01",
        summary="Alice waits for the protagonist to choose.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="decision_01",
        choices=(
            ChoicePlan(option_id="ask", action_id="ask", intent="ask directly"),
            ChoicePlan(option_id="observe", action_id="observe", intent="watch carefully"),
        ),
    )


def valid_scene_draft(plan: ScenePlan) -> SceneDraft:
    return SceneDraft(
        scene_id=plan.scene_id,
        blocks=(NarrativeBlock(kind="narration", text="The cafe hums quietly."),),
        choices=tuple(
            WrittenChoice(option_id=item.option_id, label=item.intent[:80])
            for item in plan.choices
        ),
    )


def valid_ending_draft(ending) -> EndingDraft:
    return EndingDraft(
        ending_id=ending.id,
        title=ending.title,
        blocks=(NarrativeBlock(kind="narration", text=f"Ending: {ending.title}"),),
    )


def service_fixture(tmp_path: Path, planner=None, writer=None):
    pack = compile_source(minimal_script_pack_dict())
    store = StoryEventStore(tmp_path / "story.db")
    state = initial_session_state(pack, "session_01", session_seed=42)
    store.create_session(state)
    service = RuntimeService(
        store,
        planner if planner is not None else FakePlanner(),
        writer if writer is not None else FakeWriter(),
    )
    return service, pack, store


def decision_service_fixture(tmp_path: Path):
    service, pack, store = service_fixture(tmp_path, FakePlanner(), FakeWriter())
    state = store.load_session("session_01")
    plan = valid_decision_plan()
    draft = valid_scene_draft(plan)
    events = simulate_scene(pack, state, plan, draft)
    store.append(state.session_id, state.revision, events)
    return service, pack, store


def ending_service_fixture(tmp_path: Path):
    raw = minimal_script_pack_dict()
    for ending in raw["endings"]:
        if ending["type"] == "fallback":
            ending["id"] = "safe_exit"
    pack = compile_source(raw)
    store = StoryEventStore(tmp_path / "story.db")
    state = initial_session_state(pack, "session_01", session_seed=42)
    world = state.world.model_copy(update={"scene_count": state.world.max_scenes})
    state = state.model_copy(update={"world": world})
    store.create_session(state)
    service = RuntimeService(store, FakePlanner(), FakeWriter())
    return service, pack, store


@pytest.mark.asyncio
async def test_advance_commits_scene_and_persists_choices(tmp_path):
    service, pack, store = service_fixture(tmp_path, FakePlanner(), FakeWriter())
    result = await service.advance(pack, "session_01", expected_revision=0)
    assert result.scene_id == "scene_01"
    assert len(result.choices) == 2
    assert store.load_session("session_01").pending_decision is not None


@pytest.mark.asyncio
async def test_select_choice_rejects_stale_revision(tmp_path):
    service, pack, _ = decision_service_fixture(tmp_path)
    with pytest.raises(RuntimeRevisionConflict):
        await service.select_choice(
            pack,
            "session_01",
            "ask_alice",
            expected_revision=0,
            idempotency_key="stale-request",
        )


@pytest.mark.asyncio
async def test_advance_refuses_while_decision_is_pending(tmp_path):
    service, pack, store = decision_service_fixture(tmp_path)
    state = store.load_session("session_01")
    with pytest.raises(DecisionRequired):
        await service.advance(pack, state.session_id, state.revision)


@pytest.mark.asyncio
async def test_select_choice_commits_selection_before_resolution(tmp_path):
    service, pack, store = decision_service_fixture(tmp_path)
    state = store.load_session("session_01")
    await service.select_choice(
        pack,
        state.session_id,
        state.pending_decision.choices[0].id,
        state.revision,
        "request-01",
    )
    event_types = [item.event.type for item in store.load_events(state.session_id)]
    assert event_types[-2:] == ["player_action_selected", "action_resolved"]


@pytest.mark.asyncio
async def test_eligible_ending_commits_atomic_epilogue(tmp_path):
    service, pack, store = ending_service_fixture(tmp_path)
    state = store.load_session("session_01")
    result = await service.advance(pack, state.session_id, state.revision)
    assert result.ending_id == "safe_exit"
    assert store.load_session(state.session_id).status == SessionStatus.ENDED
    assert [item.event.type for item in store.load_events(state.session_id)][-3:] == [
        "ending_entered",
        "scene_committed",
        "session_ended",
    ]
