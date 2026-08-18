"""Tests for the turn orchestrator — the sole entry point for a player turn."""

import asyncio
from pathlib import Path

import pytest

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.contracts import (
    NarrativeBlock,
    RuntimeGenerationUnavailable,
    RuntimeRevisionConflict,
    SceneDraft,
    ScenePlan,
    SegmentDraft,
    SegmentPlan,
    WrittenChoice,
)
from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.pack_cache import CachedOpening, CachedPregen, PackCache
from src.story.runtime.simulator import (
    choice_selection_event,
    simulate_consequence,
    simulate_segment,
)
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.unified_segment import UnifiedSegmentOutput
from src.story.runtime.validator import (
    validate_segment_draft,
    validate_segment_plan,
)
from src.story.script_pack.compiler import compile_source
from src.story.state import (
    ActionResolved,
    EventEnvelope,
    FactCommitted,
    FactRevealed,
    initial_session_state,
)
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    _pacing_floor,
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


def test_turn_streams_progress_stages_before_segment_events(tmp_path: Path):
    pack, _store, orch = _build_orchestrator(tmp_path)
    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-progress", None))

    stages = [data["stage"] for t, data in events if t == "progress"]
    assert stages == ["generating", "validating", "committing"]
    for _t, data in events:
        if "elapsed_ms" in data:
            assert data["elapsed_ms"] >= 0
    # every progress event precedes the committed segment stream
    first_segment = next(i for i, (t, _data) in enumerate(events) if t == "segment_started")
    last_progress = max(i for i, (t, _data) in enumerate(events) if t == "progress")
    assert last_progress < first_segment


def test_choice_turn_streams_planning_stage(tmp_path: Path):
    pack, _store, orch = _build_orchestrator(tmp_path)
    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
    ready = next(data for t, data in opening if t == "segment_ready")
    choice_id = ready["choices"][0]["id"]
    revision = ready["revision"]

    events = _collect_events(
        orch.execute_turn(pack, "s1", revision, f"cmd-choice-{choice_id}", choice_id)
    )
    stages = [data["stage"] for t, data in events if t == "progress"]
    assert stages == ["planning", "generating", "validating", "committing"]


def test_slow_generation_emits_heartbeats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("src.story.runtime.turn_orchestrator._HEARTBEAT_INTERVAL_SECONDS", 0.01)

    class SlowDirector(FakeDirector):
        async def plan_segment(self, pack, state, pacing):
            await asyncio.sleep(0.05)
            return await super().plan_segment(pack, state, pacing)

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "turn_heartbeat.db")
    state = initial_session_state(pack, "s1", session_seed=42)
    store.create_session(state)
    orch = TurnOrchestrator(
        store=store,
        director=SlowDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )

    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-slow", None))
    heartbeats = [data for t, data in events if t == "heartbeat"]
    assert heartbeats, "a slow model call must emit heartbeats"
    assert all("elapsed_ms" in data for data in heartbeats)
    # the turn still completes normally despite the slow call
    assert any(t == "segment_ready" for t, _data in events)


async def _valid_unified_output(pack, state, pacing):
    """Produce a validator-clean UnifiedSegmentOutput via the fakes."""
    director = FakeDirector()
    writer = FakeSegmentWriter()
    plan = await director.plan_segment(pack, state, pacing)
    plan = validate_segment_plan(pack, state, plan, pacing)
    draft = await writer.write_segment(pack, state, plan)
    draft = validate_segment_draft(plan, draft)
    return UnifiedSegmentOutput(segment_plan=plan, segment_draft=draft)


def test_judge_rejection_regenerates_once_with_notes(tmp_path: Path):
    """A judge-rejected proposal gets one regeneration carrying the blocking
    findings; the turn commits only when the revision passes."""
    from src.story.runtime.semantic_judge import JudgeFinding, JudgeFindings

    pack = compile_source(budget_test_pack_dict())

    class RecordingUnifiedAgent:
        def __init__(self) -> None:
            self.notes: list[tuple[str, ...]] = []

        async def generate(self, pack, state, pacing, *, rejection_notes=(), pending_choice=None):
            self.notes.append(rejection_notes)
            return await _valid_unified_output(pack, state, pacing)

    class RejectOnceJudge:
        def __init__(self) -> None:
            self.calls = 0

        async def judge_segment(self, pack, state, plan, draft, pending_choice=None):
            self.calls += 1
            if self.calls == 1:
                return JudgeFindings(
                    findings=(
                        JudgeFinding(
                            kind="choice_reversal",
                            severity="blocking",
                            detail="the segment ignores the player's committed stance",
                        ),
                    )
                )
            return JudgeFindings()

    agent = RecordingUnifiedAgent()
    judge = RejectOnceJudge()
    store = StoryEventStore(tmp_path / "turn_retry.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        unified_agent=agent,
        semantic_judge=judge,
    )

    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-retry", None))

    assert any(t == "segment_ready" for t, _data in events)
    stages = [data["stage"] for t, data in events if t == "progress"]
    assert "regenerating" in stages
    assert judge.calls == 2
    assert len(agent.notes) == 2
    assert agent.notes[0] == ()
    assert agent.notes[1] and "choice_reversal" in agent.notes[1][0]


def test_second_judge_rejection_fails_closed(tmp_path: Path):
    """Two rejected proposals commit nothing and fail closed."""
    from src.story.runtime.semantic_judge import JudgeFinding, JudgeFindings

    pack = compile_source(budget_test_pack_dict())

    class AlwaysRejectJudge:
        async def judge_segment(self, pack, state, plan, draft, pending_choice=None):
            return JudgeFindings(
                findings=(
                    JudgeFinding(
                        kind="canon_contradiction",
                        severity="blocking",
                        detail="contradicts a committed fact",
                    ),
                )
            )

    class AlwaysGenerateAgent:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, pack, state, pacing, *, rejection_notes=(), pending_choice=None):
            self.calls += 1
            return await _valid_unified_output(pack, state, pacing)

    agent = AlwaysGenerateAgent()
    store = StoryEventStore(tmp_path / "turn_failclosed.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        unified_agent=agent,
        semantic_judge=AlwaysRejectJudge(),
    )

    with pytest.raises(RuntimeGenerationUnavailable) as exc_info:
        _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-fail", None))

    assert "semantic judge rejected segment" in str(exc_info.value)
    assert agent.calls == 2
    assert store.load_events("s1") == ()


def test_preapproved_cached_opening_skips_runtime_judge(tmp_path: Path):
    """A cache stamped judge_preapproved is not re-judged at runtime."""
    from src.story.runtime.pacing import compute_pacing_envelope
    from src.story.runtime.pack_cache import CachedOpening, PackCache
    from src.story.runtime.simulator import simulate_segment

    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "cache_builder", session_seed=1)
    pacing = compute_pacing_envelope(state, pack)
    # build a valid plan/draft through the fakes
    plan = validate_segment_plan(
        pack,
        state,
        asyncio.run(FakeDirector().plan_segment(pack, state, pacing)),
        pacing,
    )
    draft = validate_segment_draft(
        plan, asyncio.run(FakeSegmentWriter().write_segment(pack, state, plan))
    )
    cache = PackCache(tmp_path / "pack_cache")
    cache.save_opening(
        pack.pack_hash,
        CachedOpening(
            segment_plan=plan,
            segment_draft=draft,
            seg_events=simulate_segment(pack, state, plan, draft),
            pacing=pacing,
            judge_preapproved=True,
        ),
    )

    class FailIfCalledJudge:
        async def judge_segment(self, *args, **kwargs):
            raise AssertionError("runtime must not re-judge a pre-approved opening")

    store = StoryEventStore(tmp_path / "turn_preapproved.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=3))
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        pack_cache=cache,
        semantic_judge=FailIfCalledJudge(),
    )

    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-pre", None))
    assert any(t == "segment_ready" for t, _data in events)


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
        async def write_segment(self, pack, state, plan, *, pending_choice=None):
            if plan.terminal != "decision":
                return await super().write_segment(pack, state, plan, pending_choice=pending_choice)
            floor = _pacing_floor(state, pack)
            return SegmentDraft(
                segment_id=plan.segment_id,
                scene_drafts=(
                    SceneDraft(
                        scene_id=plan.scenes[-1].scene_id,
                        blocks=tuple(
                            NarrativeBlock(
                                kind="narration",
                                text=(
                                    "The cafe hums quietly."
                                    if i == 0
                                    else f"Quiet beat {i} passes."
                                ),
                            )
                            for i in range(max(1, floor))
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


def test_failed_consequence_generation_preserves_committed_choice(tmp_path: Path):
    class FailingPlanner(FakePlanner):
        async def resolve_action(self, pack, state, choice, rejection_notes=()):
            raise RuntimeError("model failed")

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "fail_test.db")
    state = initial_session_state(pack, "s1", session_seed=1)
    store.create_session(state)
    opening_orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    opening = _collect_events(opening_orchestrator.execute_turn(pack, "s1", 0, "cmd-opening", None))
    ready = next(data for event_type, data in opening if event_type == "segment_ready")
    offered = ready["choices"][0]
    decision_revision = ready["revision"]

    failing_orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FailingPlanner(),
    )

    with pytest.raises(RuntimeGenerationUnavailable):
        _collect_events(
            failing_orchestrator.execute_turn(
                pack,
                "s1",
                decision_revision,
                "cmd-select",
                offered["id"],
            )
        )

    loaded = store.load_session("s1")
    assert loaded.revision == decision_revision + 1
    assert loaded.pending_decision is None
    assert loaded.pending_consequence is not None
    assert loaded.pending_consequence.option_id == offered["id"]
    assert loaded.pending_consequence.action_id == offered["action_id"]
    assert loaded.pending_consequence.intent == offered["intent"]
    assert loaded.pending_consequence.outcome is None


def test_rejected_resolution_regenerates_once_with_notes(tmp_path: Path):
    """A validator-rejected consequence resolution gets one regeneration
    carrying the rejection reasons; the turn commits only when the revised
    resolution passes the deterministic validator."""
    from src.story.runtime.validator import ProposalRejected

    pack = compile_source(budget_test_pack_dict())

    class RejectOncePlanner(FakePlanner):
        def __init__(self) -> None:
            self.notes: list[tuple[str, ...]] = []
            self.calls = 0

        async def resolve_action(self, pack, state, choice, rejection_notes=()):
            self.calls += 1
            self.notes.append(rejection_notes)
            if self.calls == 1:
                raise ProposalRejected(["cannot evidence uncommitted fact: alice_hidden_motive"])
            return await super().resolve_action(pack, state, choice)

    planner = RejectOncePlanner()
    store = StoryEventStore(tmp_path / "resolution_retry.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=planner,
    )

    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-opening", None))
    ready = next(data for t, data in opening if t == "segment_ready")
    offered = ready["choices"][0]

    events = _collect_events(
        orch.execute_turn(pack, "s1", ready["revision"], "cmd-select", offered["id"])
    )

    assert any(t == "segment_ready" for t, _data in events)
    stages = [data["stage"] for t, data in events if t == "progress"]
    assert "regenerating" in stages
    assert planner.notes[0] == ()
    assert planner.notes[1] and "uncommitted fact" in planner.notes[1][0]


def test_pending_consequence_resumes_without_resubmitting_choice(tmp_path: Path):
    class FailsOncePlanner(FakePlanner):
        def __init__(self):
            self.failed = False

        async def resolve_action(self, pack, state, choice, rejection_notes=()):
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient failure")
            return await super().resolve_action(pack, state, choice)

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "resume_test.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=1))
    planner = FailsOncePlanner()
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=planner,
    )
    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "opening", None))
    ready = next(data for event_type, data in opening if event_type == "segment_ready")

    with pytest.raises(RuntimeGenerationUnavailable):
        _collect_events(
            orch.execute_turn(
                pack,
                "s1",
                ready["revision"],
                "select-once",
                ready["choices"][0]["id"],
            )
        )

    pending_state = store.load_session("s1")
    assert pending_state.pending_consequence is not None
    choice_event_id = pending_state.pending_consequence.choice_event_id
    pending_revision = pending_state.revision

    recovered = _collect_events(
        orch.execute_turn(pack, "s1", pending_revision, "resume-request", None)
    )
    recovered_ready = next(data for event_type, data in recovered if event_type == "segment_ready")
    assert recovered_ready["revision"] > pending_revision

    selected_events = [
        envelope
        for envelope in store.load_events("s1")
        if envelope.event.type == "player_action_selected"
    ]
    resolved_events = [
        envelope for envelope in store.load_events("s1") if envelope.event.type == "action_resolved"
    ]
    assert len(selected_events) == 1
    assert len(resolved_events) == 1
    assert resolved_events[0].event.source_choice_event_id == choice_event_id

    replay = _collect_events(
        orch.execute_turn(pack, "s1", pending_revision, "another-device", None)
    )
    replay_ready = next(data for event_type, data in replay if event_type == "segment_ready")
    assert replay_ready == recovered_ready
    assert (
        len(
            [
                envelope
                for envelope in store.load_events("s1")
                if envelope.event.type == "action_resolved"
            ]
        )
        == 1
    )


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


def test_committed_completion_citations_resolve_against_committed_history(
    tmp_path: Path,
):
    """The committed CompletionEvaluated must cite the actual committed
    envelope ids — never simulation-only ids — so the Causal Trace and any
    later audit resolve against committed history."""

    from tests.story_factories import minimal_pack_v2_dict

    pack = compile_source(minimal_pack_v2_dict())
    store = StoryEventStore(tmp_path / "citation_test.db")
    state = initial_session_state(pack, "s1", session_seed=1)
    state = state.model_copy(
        update={"world": state.world.model_copy(update={"scene_count": state.world.max_scenes})}
    )
    store.create_session(state)
    early_history = (
        EventEnvelope(
            event_id="early-evidence",
            session_id="s1",
            sequence=1,
            event=ActionResolved(action_id="observe", outcome="success"),
        ),
        EventEnvelope(
            event_id="early-commit",
            session_id="s1",
            sequence=2,
            event=FactCommitted(
                fact_id="who_took_notebook",
                value="alice",
                evidence_event_ids=("early-evidence",),
            ),
        ),
        EventEnvelope(
            event_id="early-reveal",
            session_id="s1",
            sequence=3,
            event=FactRevealed(fact_id="who_took_notebook"),
        ),
    )
    store.append_envelopes("s1", 0, early_history)
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
    )

    _collect_events(orch.execute_turn(pack, "s1", 3, "cmd-ending", None))

    committed = store.load_events("s1")
    committed_ids = {envelope.event_id for envelope in committed}
    completion_event = next(
        envelope.event for envelope in committed if envelope.event.type == "completion_evaluated"
    )
    cited = {
        event_id
        for assessment in completion_event.assessments
        for event_id in assessment.cited_event_ids
    }
    assert cited
    assert cited.issubset(committed_ids)


def test_ending_ready_payload_carries_causal_traces(tmp_path: Path):
    """The ending segment_ready exposes the derived Causal Traces so player
    impact on the ending is auditable through the SSE payload."""

    from tests.story_factories import minimal_pack_v2_dict

    pack = compile_source(minimal_pack_v2_dict())
    store = StoryEventStore(tmp_path / "trace_payload_test.db")
    state = initial_session_state(pack, "s1", session_seed=1)
    state = state.model_copy(
        update={"world": state.world.model_copy(update={"scene_count": state.world.max_scenes})}
    )
    store.create_session(state)
    early_history = (
        EventEnvelope(
            event_id="early-evidence",
            session_id="s1",
            sequence=1,
            event=ActionResolved(action_id="observe", outcome="success"),
        ),
        EventEnvelope(
            event_id="early-commit",
            session_id="s1",
            sequence=2,
            event=FactCommitted(
                fact_id="who_took_notebook",
                value="alice",
                evidence_event_ids=("early-evidence",),
            ),
        ),
        EventEnvelope(
            event_id="early-reveal",
            session_id="s1",
            sequence=3,
            event=FactRevealed(fact_id="who_took_notebook"),
        ),
    )
    store.append_envelopes("s1", 0, early_history)
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
    )

    events = _collect_events(orch.execute_turn(pack, "s1", 3, "cmd-ending", None))
    ready = next(data for t, data in events if t == "segment_ready")

    # The completion payload lives on segment_ready next to `ending`
    # (the frontend protocol reads `cleared` at this level).
    assert "causal_traces" in ready
    traces = ready["causal_traces"]
    # The trace references real committed event ids only.
    committed_ids = {envelope.event_id for envelope in store.load_events("s1")}
    for trace in traces:
        referenced = {
            *trace["direct_consequence_event_ids"],
            *trace["development_event_ids"],
            *trace["ending_contribution_event_ids"],
        }
        assert referenced.issubset(committed_ids)


def test_yokai_after_school_completion_review_evaluates_real_derived_evidence(
    tmp_path: Path,
):
    """End-to-end with the real pack: a playthrough that builds trust with
    Hiyori and repeatedly takes an obligation-typed risk produces derived
    turning-point and cost evidence that the completion review actually
    evaluates — meaningful_bond and accepted_cost are satisfied from
    committed history, not prose."""

    from src.story.runtime.contracts import (
        ActionResolution,
        ChoicePlan,
        RelationshipDelta,
    )
    from src.story.script_pack import compile_script_pack

    PACK_DIR = Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school"
    pack = compile_script_pack(PACK_DIR)

    class TrustingPlanner(FakePlanner):
        async def resolve_action(self, pack, state, choice, rejection_notes=()):
            return ActionResolution(
                action_id=choice.action_id,
                outcome="success",
                relationship_deltas=(
                    RelationshipDelta(character_id="hiyori", axis="trust", delta=10),
                ),
            )

    class ObligationDirector(FakeDirector):
        """Attach obligation/risk Choice Meaning to the first offered choice."""

        async def plan_segment(self, pack, state, pacing):
            plan = await super().plan_segment(pack, state, pacing)
            if plan.terminal != "decision":
                return plan
            scenes = list(plan.scenes)
            last_scene = scenes[-1]
            choices = list(last_scene.choices)
            choices[0] = ChoicePlan(
                option_id=choices[0].option_id,
                action_id=choices[0].action_id,
                intent=choices[0].intent,
                accepted_risk="keep_private_wish",
                potential_obligation_kind="keep_private_wish",
            )
            scenes[-1] = last_scene.model_copy(update={"choices": tuple(choices)})
            return plan.model_copy(update={"scenes": tuple(scenes)})

    store = StoryEventStore(tmp_path / "yokai_completion.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=11))
    orch = TurnOrchestrator(
        store=store,
        director=ObligationDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=TrustingPlanner(),
    )

    def run_turn(revision, key, choice_id=None):
        return _collect_events(orch.execute_turn(pack, "s1", revision, key, choice_id))

    opening = next(data for t, data in run_turn(0, "cmd-open") if t == "segment_ready")
    revision = opening["revision"]
    ready = opening
    turn = 0
    while ready["terminal"] != "ending":
        # Always take the obligation-typed first choice.
        choice_id = ready["choices"][0]["id"]
        followup = run_turn(revision, f"cmd-{turn}", choice_id)
        ready = next(data for t, data in followup if t == "segment_ready")
        revision = ready["revision"]
        turn += 1

    assert ready["terminal"] == "ending"
    assessments = {item["requirement_id"]: item for item in ready["assessments"]}
    assert assessments["meaningful_bond"]["satisfied"] is True
    assert assessments["accepted_cost"]["satisfied"] is True

    # The turning point was derived at the ending and committed with the
    # actual envelope ids of the relationship events it cites.
    committed = store.load_events("s1")
    committed_ids = {envelope.event_id for envelope in committed}
    turning_points = [
        envelope.event
        for envelope in committed
        if envelope.event.type == "relationship_turning_point_reached"
    ]
    assert turning_points
    for turning_point in turning_points:
        assert set(turning_point.relationship_event_ids).issubset(committed_ids)
    relationship_events = [
        envelope.event
        for envelope in committed
        if envelope.event.type == "relationship_event_recorded"
    ]
    assert len(relationship_events) >= 2
    for event in relationship_events:
        assert event.scene_event_id in committed_ids
        assert event.source_choice_event_id in committed_ids

    # Derived costs cite the committed obligation they were derived from.
    costs = [envelope.event for envelope in committed if envelope.event.type == "cost_incurred"]
    assert costs
    for cost in costs:
        assert set(cost.effect_event_ids).issubset(committed_ids)


def test_ending_completion_judge_receives_persisted_and_terminal_history(tmp_path: Path):
    class RecordingJudge(CompletionJudge):
        def __init__(self):
            self.event_trace = ()

        def evaluate(self, requirements, final_state, event_trace):
            self.event_trace = event_trace
            return super().evaluate(requirements, final_state, event_trace)

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "ending_history_test.db")
    state = initial_session_state(pack, "s1", session_seed=1)
    state = state.model_copy(
        update={"world": state.world.model_copy(update={"scene_count": state.world.max_scenes})}
    )
    store.create_session(state)
    early_history = (
        EventEnvelope(
            event_id="early-evidence",
            session_id="s1",
            sequence=1,
            event=ActionResolved(action_id="observe", outcome="success"),
        ),
        EventEnvelope(
            event_id="early-commit",
            session_id="s1",
            sequence=2,
            event=FactCommitted(
                fact_id="who_took_notebook",
                value="alice",
                evidence_event_ids=("early-evidence",),
            ),
        ),
        EventEnvelope(
            event_id="early-reveal",
            session_id="s1",
            sequence=3,
            event=FactRevealed(fact_id="who_took_notebook"),
        ),
    )
    store.append_envelopes("s1", 0, early_history)
    judge = RecordingJudge()
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=judge,
    )

    _collect_events(orch.execute_turn(pack, "s1", 3, "cmd-ending", None))

    assert judge.event_trace[:3] == early_history
    assert len(judge.event_trace) > len(early_history)
    assert tuple(item.sequence for item in judge.event_trace) == tuple(
        range(1, len(judge.event_trace) + 1)
    )


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


def test_legacy_choice_cache_cannot_bypass_authoritative_flow(tmp_path: Path):
    """Legacy choice cache entries are ignored until their keys and validation
    contract can identify the exact committed choice and session revision."""

    class RecordingPlanner(FakePlanner):
        called = False

        async def resolve_action(self, pack, state, choice, rejection_notes=()):
            self.called = True
            return await super().resolve_action(pack, state, choice)

    class RecordingDirector(FakeDirector):
        called = False

        async def plan_segment(self, pack, state, pacing):
            self.called = True
            return await super().plan_segment(pack, state, pacing)

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
    selection = choice_selection_event(post_state, choice, "pregen-key")

    from src.story.state import EventEnvelope, apply_events

    choice_envelope = EventEnvelope(
        event_id="pregen-choice",
        session_id="s1",
        sequence=post_state.revision + 1,
        event=selection,
    )
    selected_state = apply_events(post_state, (choice_envelope,))
    consequence_events = simulate_consequence(pack, selected_state, resolution)
    pre_events = (selection, *consequence_events)
    consequence_envelopes = tuple(
        EventEnvelope(
            session_id="s1",
            sequence=selected_state.revision + i,
            event=e,
        )
        for i, e in enumerate(consequence_events, start=1)
    )
    hypo_state = apply_events(selected_state, consequence_envelopes)
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

    planner = RecordingPlanner()
    director = RecordingDirector()
    choice_orch = TurnOrchestrator(
        store=store,
        director=director,
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=planner,
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
    assert planner.called
    assert director.called


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


def test_pregen_manager_no_longer_exists_as_a_consumption_path(tmp_path: Path):
    """The legacy PreGenerationManager was removed with its implicit-success
    path.  No orchestrator surface accepts it anymore: choice cache entries
    (see ``test_legacy_choice_cache_cannot_bypass_authoritative_flow``) are
    never consumed, so consequences can only be committed by the
    authoritative command flow."""

    import importlib
    import inspect

    assert "pregen_manager" not in inspect.signature(TurnOrchestrator.__init__).parameters
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.story.runtime.pregeneration")


def test_validator_rejected_plan_regenerates_with_notes(tmp_path: Path):
    """A plan rejected by the deterministic validator regenerates once with
    the validator reasons as rejection notes, instead of failing the command
    cold (autoplay-level retries start without notes)."""

    class PoisonedFirstAgent:
        """First proposal commits an unavailable candidate value."""

        def __init__(self) -> None:
            self.notes: list[tuple[str, ...]] = []
            self.poisoned = True

        async def generate(self, pack, state, pacing, *, rejection_notes=(), pending_choice=None):
            from src.story.runtime.contracts import FactCommitPlan

            self.notes.append(rejection_notes)
            output = await _valid_unified_output(pack, state, pacing)
            if self.poisoned:
                self.poisoned = False
                plan = output.segment_plan.model_copy(
                    update={
                        "scenes": (
                            output.segment_plan.scenes[0].model_copy(
                                update={
                                    "fact_commits": (
                                        FactCommitPlan(
                                            fact_id="who_took_notebook",
                                            value="nobody",
                                            reason="explicit_revelation",
                                        ),
                                    )
                                }
                            ),
                        )
                    }
                )
                return output.model_copy(
                    update={"segment_plan": plan},
                )
            return output

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "plan_regen.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        unified_agent=PoisonedFirstAgent(),
    )

    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-plan-regen", None))

    assert any(t == "segment_ready" for t, _data in events)
    stages = [data["stage"] for t, data in events if t == "progress"]
    assert "regenerating" in stages
    assert agent_notes_carry_validator_reason(orch)
    record = store.load_turn_diagnostics("s1")[0]
    assert record["regenerations"] == 1
    assert record["validator_violations"]


def agent_notes_carry_validator_reason(orch) -> bool:
    return any(
        "validator/proposal" in note
        for note in orch.unified_agent.notes[1]
    )
