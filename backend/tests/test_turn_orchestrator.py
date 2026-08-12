"""Tests for the turn orchestrator — the sole entry point for a player turn."""

import asyncio
from pathlib import Path

import pytest

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.contracts import (
    NarrativeBlock,
    RuntimeGenerationUnavailable,
    SceneDraft,
    ScenePlan,
    SegmentDraft,
    SegmentPlan,
    WrittenChoice,
)
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.script_pack.compiler import compile_source
from src.story.state import initial_session_state
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)


def _build_orchestrator(tmp_path: Path):
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "turn_test.db")
    state = initial_session_state(pack, "s1", session_seed=42)
    store.create_session(state)
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    return pack, store, orchestrator


def _collect_events(gen):
    """Run an async generator synchronously and collect events."""
    events = []
    loop = asyncio.new_event_loop()

    async def run():
        async for evt_type, data in gen:
            events.append((evt_type, data))

    loop.run_until_complete(run())
    loop.close()
    return events


def test_opening_turn_streams_segment_started_blocks_ready(tmp_path: Path):
    pack, _store, orch = _build_orchestrator(tmp_path)
    gen = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events = _collect_events(gen)

    types = [e[0] for e in events]
    assert "segment_started" in types
    assert "block" in types
    assert "segment_ready" in types

    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "decision"
    assert len(ready["choices"]) == 2


def test_segment_ready_choices_come_from_draft_when_plan_scene_has_none(
    tmp_path: Path,
):
    """A decision plan whose last scene carries no scene-level choices must
    still deliver the draft's 2-4 written choices in segment_ready."""

    class NoSceneChoicesDirector(FakeDirector):
        async def plan_segment(self, pack, state, pacing):
            segment_id = f"seg_{state.session_id}_draft_choices"
            # model_construct bypasses ScenePlan's "decision scenes require
            # 2-4 choices" constructor check — the validator layer explicitly
            # permits empty scene choices when the draft has 2-4 choices.
            return SegmentPlan.model_construct(
                segment_id=segment_id,
                scenes=(
                    ScenePlan.model_construct(
                        scene_id=f"scene_{segment_id}",
                        summary="A scene unfolds",
                        location_id=state.world.location_id,
                        present_character_ids=state.world.present_character_ids,
                        terminal="decision",
                        decision_id=f"dec_{segment_id}",
                        choices=(),
                    ),
                ),
                terminal="decision",
            )

    class DraftChoicesWriter(FakeSegmentWriter):
        async def write_segment(self, pack, state, plan):
            if plan.terminal != "decision":
                return await super().write_segment(pack, state, plan)
            return SegmentDraft(
                segment_id=plan.segment_id,
                scene_drafts=(
                    SceneDraft(
                        scene_id=plan.scenes[-1].scene_id,
                        blocks=(
                            NarrativeBlock(
                                kind="narration",
                                text="The cafe hums quietly.",
                            ),
                        ),
                    ),
                ),
                choices=(
                    WrittenChoice(
                        option_id="ask",
                        label="Ask about the notebook",
                        preview="Ask Alice about the notebook",
                    ),
                    WrittenChoice(
                        option_id="observe",
                        label="Watch quietly",
                        preview="Observe the room",
                    ),
                ),
            )

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "draft_choices_test.db")
    state = initial_session_state(pack, "s1", session_seed=42)
    store.create_session(state)
    orch = TurnOrchestrator(
        store=store,
        director=NoSceneChoicesDirector(),
        writer=DraftChoicesWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )

    gen = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events = _collect_events(gen)
    ready = next(data for t, data in events if t == "segment_ready")

    assert ready["terminal"] == "decision"
    assert [c["id"] for c in ready["choices"]] == ["ask", "observe"]
    assert [c["label"] for c in ready["choices"]] == [
        "Ask about the notebook",
        "Watch quietly",
    ]
    assert [c["preview"] for c in ready["choices"]] == [
        "Ask Alice about the notebook",
        "Observe the room",
    ]


def test_turn_increases_revision(tmp_path: Path):
    pack, _store, orch = _build_orchestrator(tmp_path)
    gen = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events = _collect_events(gen)
    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["revision"] > 0


def test_idempotent_replay_returns_same_segment(tmp_path: Path):
    pack, _store, orch = _build_orchestrator(tmp_path)
    gen1 = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events1 = _collect_events(gen1)
    gen2 = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events2 = _collect_events(gen2)

    ready1 = next(data for t, data in events1 if t == "segment_ready")
    ready2 = next(data for t, data in events2 if t == "segment_ready")
    assert ready1["revision"] == ready2["revision"]
    assert ready1["segment_id"] == ready2["segment_id"]


def test_failed_generation_releases_command(tmp_path: Path):
    class FailingDirector(FakeDirector):
        async def plan_segment(self, pack, state, pacing):
            raise RuntimeError("model failed")

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "fail_test.db")
    state = initial_session_state(pack, "s1", session_seed=1)
    store.create_session(state)
    orch = TurnOrchestrator(
        store=store,
        director=FailingDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    gen = orch.execute_turn(pack, "s1", 0, "cmd-fail", None)

    with pytest.raises(RuntimeGenerationUnavailable):
        _collect_events(gen)

    # Verify session revision is unchanged (command was released).
    loaded = store.load_session("s1")
    assert loaded.revision == 0


def test_choice_turn_resolves_and_advances(tmp_path: Path):
    pack, _store, orch = _build_orchestrator(tmp_path)

    # First turn: opening -> decision.
    gen1 = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events1 = _collect_events(gen1)
    ready1 = next(data for t, data in events1 if t == "segment_ready")
    choice_id = ready1["choices"][0]["id"]
    rev = ready1["revision"]

    # Second turn: resolve choice -> next decision.
    gen2 = orch.execute_turn(pack, "s1", rev, "cmd-01", choice_id)
    events2 = _collect_events(gen2)
    ready2 = next(data for t, data in events2 if t == "segment_ready")
    assert ready2["revision"] > rev


def test_ending_turn_has_ending_terminal(tmp_path: Path):
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "ending_test.db")
    state = initial_session_state(pack, "s1", session_seed=1)
    # Force to max scenes.
    state = state.model_copy(update={
        "world": state.world.model_copy(update={"scene_count": state.world.max_scenes})
    })
    store.create_session(state)
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
    )
    gen = orch.execute_turn(pack, "s1", 0, "cmd-ending", None)
    events = _collect_events(gen)
    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "ending"
    assert "ending" in ready
