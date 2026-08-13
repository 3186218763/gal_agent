"""Tests for the turn orchestrator — the sole entry point for a player turn."""

import asyncio
from pathlib import Path

import pytest

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.contracts import (
    NarrativeBlock,
    RuntimeRevisionConflict,
    SceneDraft,
    ScenePlan,
    SegmentDraft,
    SegmentPlan,
    WrittenChoice,
)
from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.pack_cache import CachedOpening, CachedPregen, PackCache
from src.story.runtime.simulator import simulate_resolution, simulate_segment
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.validator import (
    validate_segment_draft,
    validate_segment_plan,
)
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


def test_failed_generation_uses_deterministic_fallback(tmp_path: Path):
    """When the Director fails, the deterministic fallback produces a valid
    segment so the player never hits a dead-end error screen."""

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
    events = _collect_events(gen)

    # Fallback should produce a valid segment with blocks and choices.
    types = [e[0] for e in events]
    assert "segment_started" in types
    assert "block" in types
    assert "segment_ready" in types

    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "decision"
    assert len(ready["choices"]) >= 2

    # Revision should advance — the fallback segment is committed normally.
    loaded = store.load_session("s1")
    assert loaded.revision > 0


def test_revision_conflict_releases_command(tmp_path: Path):
    """When a non-generation error (e.g. revision conflict) occurs, the
    command lease must be released so the session is not locked."""

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "conflict_test.db")
    state = initial_session_state(pack, "s1", session_seed=1)
    store.create_session(state)
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    # Pass a wrong expected_revision to trigger RuntimeRevisionConflict.
    gen = orch.execute_turn(pack, "s1", 99, "cmd-conflict", None)

    with pytest.raises(RuntimeRevisionConflict):
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
    state = state.model_copy(
        update={"world": state.world.model_copy(update={"scene_count": state.world.max_scenes})}
    )
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


# ---------------------------------------------------------------------------
# Cache integration tests (Tasks 6, 7, 9)
# ---------------------------------------------------------------------------


def _build_opening_cache(pack, tmp_path: Path) -> PackCache:
    """Manually build a PackCache with a pre-generated opening segment."""
    cache = PackCache(tmp_path / "pack_cache")
    state = initial_session_state(pack, "cache_builder", session_seed=1)

    director = FakeDirector()
    writer = FakeSegmentWriter()
    pacing = compute_pacing_envelope(state, pack)
    plan = asyncio.run(director.plan_segment(pack, state, pacing))
    plan = validate_segment_plan(pack, state, plan, pacing)
    draft = asyncio.run(writer.write_segment(pack, state, plan))
    draft = validate_segment_draft(plan, draft)
    seg_events = simulate_segment(pack, state, plan, draft)

    cache.save_opening(
        pack.pack_hash,
        CachedOpening(
            segment_plan=plan,
            segment_draft=draft,
            seg_events=seg_events,
            pacing=pacing,
        ),
    )
    return cache


def test_cached_opening_skips_generation(tmp_path: Path):
    """When PackCache has an opening, the orchestrator uses it directly
    and never calls director/writer/unified_agent."""

    class FailingDirector(FakeDirector):
        async def plan_segment(self, pack, state, pacing):
            raise AssertionError("Director should not be called on cache hit")

    pack = compile_source(budget_test_pack_dict())
    cache = _build_opening_cache(pack, tmp_path)

    store = StoryEventStore(tmp_path / "cache_opening.db")
    state = initial_session_state(pack, "s1", session_seed=42)
    store.create_session(state)

    orch = TurnOrchestrator(
        store=store,
        director=FailingDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        pack_cache=cache,
    )

    gen = orch.execute_turn(pack, "s1", 0, "cmd-cache-00", None)
    events = _collect_events(gen)

    types = [e[0] for e in events]
    assert "segment_started" in types
    assert "block" in types
    assert "segment_ready" in types

    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "decision"
    assert len(ready["choices"]) >= 2

    # Verify events were committed.
    loaded = store.load_session("s1")
    assert loaded.revision > 0


def test_cached_opening_idempotent_replay(tmp_path: Path):
    """A cached opening turn replays correctly on second call."""

    class FailingDirector(FakeDirector):
        async def plan_segment(self, pack, state, pacing):
            raise AssertionError("Director should not be called")

    pack = compile_source(budget_test_pack_dict())
    cache = _build_opening_cache(pack, tmp_path)

    store = StoryEventStore(tmp_path / "cache_replay.db")
    state = initial_session_state(pack, "s1", session_seed=42)
    store.create_session(state)

    orch = TurnOrchestrator(
        store=store,
        director=FailingDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        pack_cache=cache,
    )

    gen1 = orch.execute_turn(pack, "s1", 0, "cmd-replay", None)
    events1 = _collect_events(gen1)
    ready1 = next(data for t, data in events1 if t == "segment_ready")

    gen2 = orch.execute_turn(pack, "s1", 0, "cmd-replay", None)
    events2 = _collect_events(gen2)
    ready2 = next(data for t, data in events2 if t == "segment_ready")

    assert ready1["revision"] == ready2["revision"]
    assert ready1["segment_id"] == ready2["segment_id"]


def test_pack_cache_hit_skips_planner_and_agent(tmp_path: Path):
    """When PackCache has a pregen for a choice_id, the orchestrator uses it
    directly — planner.resolve_action and director.plan_segment are never called."""

    class FailingPlanner(FakePlanner):
        async def resolve_action(self, pack, state, choice):
            raise AssertionError("Planner should not be called on cache hit")

    class FailingDirector(FakeDirector):
        async def plan_segment(self, pack, state, pacing):
            raise AssertionError("Director should not be called on cache hit")

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "choice_cache.db")
    state = initial_session_state(pack, "s1", session_seed=42)
    store.create_session(state)

    # Run opening turn normally to get the first decision.
    opening_orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    gen0 = opening_orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events0 = _collect_events(gen0)
    ready0 = next(data for t, data in events0 if t == "segment_ready")
    choice_id = ready0["choices"][0]["id"]
    rev = ready0["revision"]

    # Build a pregen for that choice.
    post_state = store.load_session("s1")
    choice = post_state.pending_decision.choices[0]

    # Resolve to get pre_events.
    resolution = asyncio.run(FakePlanner().resolve_action(pack, post_state, choice))
    pre_events = simulate_resolution(post_state, choice, resolution, "pregen-key")

    from src.story.state import EventEnvelope, apply_events

    pre_envelopes = tuple(
        EventEnvelope(session_id="s1", sequence=post_state.revision + i, event=e)
        for i, e in enumerate(pre_events, start=1)
    )
    hypo_state = apply_events(post_state, pre_envelopes)
    pacing = compute_pacing_envelope(hypo_state, pack)

    director = FakeDirector()
    writer = FakeSegmentWriter()
    plan = asyncio.run(director.plan_segment(pack, hypo_state, pacing))
    plan = validate_segment_plan(pack, hypo_state, plan, pacing)
    draft = asyncio.run(writer.write_segment(pack, hypo_state, plan))
    draft = validate_segment_draft(plan, draft)
    seg_events = simulate_segment(pack, hypo_state, plan, draft)

    cache = PackCache(tmp_path / "choice_pack_cache")
    cache.save_pregen(
        pack.pack_hash,
        choice_id,
        CachedPregen(
            choice_id=choice_id,
            pre_events=pre_events,
            seg_events=seg_events,
            segment_plan=plan,
            segment_draft=draft,
            pacing=pacing,
        ),
    )

    # Now run choice turn with cache — should skip planner and director.
    choice_orch = TurnOrchestrator(
        store=store,
        director=FailingDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FailingPlanner(),
        pack_cache=cache,
    )

    gen1 = choice_orch.execute_turn(pack, "s1", rev, "cmd-01", choice_id)
    events1 = _collect_events(gen1)

    types = [e[0] for e in events1]
    assert "segment_started" in types
    assert "block" in types
    assert "segment_ready" in types

    ready1 = next(data for t, data in events1 if t == "segment_ready")
    assert ready1["revision"] > rev


def test_cache_miss_falls_through_to_normal_generation(tmp_path: Path):
    """When no cache entry exists, orchestrator generates normally."""
    pack = compile_source(budget_test_pack_dict())
    cache = PackCache(tmp_path / "empty_cache")

    store = StoryEventStore(tmp_path / "miss_test.db")
    state = initial_session_state(pack, "s1", session_seed=42)
    store.create_session(state)

    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        pack_cache=cache,
    )

    gen = orch.execute_turn(pack, "s1", 0, "cmd-miss", None)
    events = _collect_events(gen)

    types = [e[0] for e in events]
    assert "segment_started" in types
    assert "block" in types
    assert "segment_ready" in types

    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "decision"


def test_pregeneration_triggered_after_decision_commit(tmp_path: Path):
    """After committing a decision segment, pregeneration_manager is called."""

    class RecordingPregenManager:
        def __init__(self):
            self.calls = []

        async def pregenerate_choices(self, session_id, state, choices, pack):
            self.calls.append(
                {
                    "session_id": session_id,
                    "choices": list(choices),
                }
            )

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "pregen_trigger.db")
    state = initial_session_state(pack, "s1", session_seed=42)
    store.create_session(state)

    mock_pregen = RecordingPregenManager()

    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        pregen_manager=mock_pregen,
    )

    gen = orch.execute_turn(pack, "s1", 0, "cmd-pregen", None)
    _collect_events(gen)

    # The background task fires asyncio.create_task which may not have
    # completed yet.  Run the event loop briefly to let it schedule.
    async def _drain():
        await asyncio.sleep(0.1)

    asyncio.run(_drain())

    assert len(mock_pregen.calls) == 1
    assert mock_pregen.calls[0]["session_id"] == "s1"
    assert len(mock_pregen.calls[0]["choices"]) >= 2
