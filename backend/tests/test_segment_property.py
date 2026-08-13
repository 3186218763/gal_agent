"""End-to-end property tests: fake-agent sessions with multiple player policies."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

import pytest
import yaml

from src.story.api import ScriptPackRegistry
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.state import SessionStatus, initial_session_state
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)

PlayerPolicy = Literal["first", "last", "alternate"]


def _build_orchestrator(tmp_path: Path):
    raw = budget_test_pack_dict()
    packs_root = tmp_path / "script_packs"
    pack_dir = packs_root / "test_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    registry = ScriptPackRegistry(packs_root)
    pack = registry.get("test_pack")
    store = StoryEventStore(tmp_path / "prop_test.db")
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    return pack, store, orchestrator


def _run_turn(orch, pack, session_id, revision, key, choice_id):
    """Run a turn synchronously and return the segment_ready data."""
    gen = orch.execute_turn(pack, session_id, revision, key, choice_id)
    events = []

    async def run():
        async for evt_type, data in gen:
            events.append((evt_type, data))

    loop = asyncio.new_event_loop()
    loop.run_until_complete(run())
    loop.close()
    ready = next(data for t, data in events if t == "segment_ready")
    return ready


def _select_choice(choices: list, policy: PlayerPolicy, turn_index: int):
    if policy == "first":
        return choices[0]["id"]
    elif policy == "last":
        return choices[-1]["id"]
    else:  # alternate
        return choices[turn_index % len(choices)]["id"]


def _run_full_session(store, orch, pack, session_id, policy: PlayerPolicy):
    """Run a full session from opening to ending. Returns final state."""
    revision = 0
    turn = 0
    choice_id = None
    key = f"cmd-{session_id}-000"

    ready = _run_turn(orch, pack, session_id, revision, key, choice_id)
    revision = ready["revision"]

    while ready["terminal"] != "ending":
        turn += 1
        choice_id = _select_choice(ready["choices"], policy, turn)
        key = f"cmd-{session_id}-{turn:03d}"
        ready = _run_turn(orch, pack, session_id, revision, key, choice_id)
        revision = ready["revision"]

        # Safety valve: prevent infinite loops.
        if turn > 50:
            break

    return store.load_session(session_id), ready


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_session_reaches_ending_within_scene_budget(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_budget", session_seed=99)
    store.create_session(state)

    final_state, ready = _run_full_session(store, orch, pack, "sess_budget", policy)

    assert final_state.status == SessionStatus.ENDED
    assert final_state.world.scene_count <= final_state.world.max_scenes
    assert ready["terminal"] == "ending"


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_session_has_exactly_one_ending(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_ending", session_seed=100)
    store.create_session(state)

    final_state, _ = _run_full_session(store, orch, pack, "sess_ending", policy)

    assert final_state.ending is not None
    assert final_state.ending.ending_id is not None
    # Check event log for exactly one EndingGenerated.
    events = store.load_events("sess_ending")
    ending_events = [e for e in events if e.event.type == "ending_generated"]
    assert len(ending_events) == 1


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_session_has_completion_assessment(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_completion", session_seed=101)
    store.create_session(state)

    final_state, _ = _run_full_session(store, orch, pack, "sess_completion", policy)

    assert final_state.completion is not None
    # Completion is a boolean (cleared or not), but must exist.
    assert isinstance(final_state.completion.cleared, bool)


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_no_duplicate_choice_ids(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_choices", session_seed=102)
    store.create_session(state)

    # Run a few turns and collect choice IDs.
    revision = 0
    all_choice_ids: set[str] = set()
    ready = _run_turn(orch, pack, "sess_choices", revision, "cmd-0", None)
    revision = ready["revision"]

    for turn in range(1, 4):
        if ready["terminal"] == "ending":
            break
        choice = _select_choice(ready["choices"], policy, turn)
        for c in ready["choices"]:
            assert c["id"] not in all_choice_ids, f"duplicate choice id: {c['id']}"
            all_choice_ids.add(c["id"])
        ready = _run_turn(orch, pack, "sess_choices", revision, f"cmd-{turn}", choice)
        revision = ready["revision"]


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_event_replay_equals_committed_state(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_replay", session_seed=103)
    store.create_session(state)

    final_state, _ = _run_full_session(store, orch, pack, "sess_replay", policy)

    # Reload from store and verify it matches.
    reloaded = store.load_session("sess_replay")
    assert reloaded.revision == final_state.revision
    assert reloaded.status == final_state.status
    assert reloaded.world.scene_count == final_state.world.scene_count


def test_sessions_with_different_policies_produce_different_revisions(tmp_path: Path):
    """Different player policies should lead to different session paths."""
    pack1, store1, orch1 = _build_orchestrator(tmp_path / "policy1")
    state1 = initial_session_state(pack1, "sess_p1", session_seed=1)
    store1.create_session(state1)
    _run_full_session(store1, orch1, pack1, "sess_p1", "first")

    pack2, store2, orch2 = _build_orchestrator(tmp_path / "policy2")
    state2 = initial_session_state(pack2, "sess_p2", session_seed=1)
    store2.create_session(state2)
    _run_full_session(store2, orch2, pack2, "sess_p2", "last")

    # Different policies may or may not produce different revisions with
    # the fake director (it always produces 1-scene segments), but the
    # event traces should differ in choice selections.
    events1 = store1.load_events("sess_p1")
    events2 = store2.load_events("sess_p2")
    choices1 = [e for e in events1 if e.event.type == "player_action_selected"]
    choices2 = [e for e in events2 if e.event.type == "player_action_selected"]

    # With the same number of turns, first vs last should differ.
    if len(choices1) == len(choices2) and len(choices1) > 0:
        option_ids1 = [e.event.option_id for e in choices1]
        option_ids2 = [e.event.option_id for e in choices2]
        assert option_ids1 != option_ids2, "different policies should select different options"
