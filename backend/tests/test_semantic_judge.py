"""Tests for the independent Semantic Judge (ADR 0007).

The judge reports structured findings, cannot write prose or mutate state,
and fails closed: a blocking finding or a judge failure means the proposed
segment is never committed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.contracts import RuntimeGenerationUnavailable
from src.story.runtime.semantic_judge import (
    BLOCKING_KINDS,
    JudgeFinding,
    JudgeFindings,
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
    FakeSemanticJudge,
    budget_test_pack_dict,
)


def _blocking_findings() -> JudgeFindings:
    return JudgeFindings(
        findings=(
            JudgeFinding(
                kind="choice_reversal",
                severity="blocking",
                detail="the segment ignores the player's committed stance",
            ),
        )
    )


def _build_orchestrator(tmp_path: Path, judge):
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "judge_test.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=7))
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        semantic_judge=judge,
    )
    return pack, store, orch


def _collect_events(gen):
    events = []
    loop = asyncio.new_event_loop()

    async def run():
        async for evt_type, data in gen:
            events.append((evt_type, data))

    loop.run_until_complete(run())
    loop.close()
    return events


def test_blocking_kinds_are_the_high_risk_vocabulary():
    assert BLOCKING_KINDS == {
        "canon_contradiction",
        "knowledge_leakage",
        "choice_reversal",
        "boundary_violation",
        "missing_ending_integrity",
        "detail_contradiction",
        "repetition",
    }


def test_judge_is_called_for_every_segment(tmp_path: Path):
    judge = FakeSemanticJudge()
    pack, _store, orch = _build_orchestrator(tmp_path, judge)
    _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-00", None))
    assert len(judge.calls) == 1
    assert judge.calls[0]["pending_choice"] is None


def test_blocking_finding_fails_closed_without_committing(tmp_path: Path):
    judge = FakeSemanticJudge(_blocking_findings())
    pack, store, orch = _build_orchestrator(tmp_path, judge)
    with pytest.raises(RuntimeGenerationUnavailable):
        _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-00", None))
    # Nothing was committed and the command lease was released.
    assert store.load_session("s1").revision == 0
    assert store.load_events("s1") == ()
    # A later pass does not inherit the failure (lease was released).
    judge._findings = JudgeFindings()
    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-01", None))
    assert any(t == "segment_ready" for t, _ in events)


def test_blocking_finding_preserves_pending_consequence(tmp_path: Path):
    """A rejected consequence must not erase the committed Choice Meaning."""
    judge = FakeSemanticJudge()
    pack, store, orch = _build_orchestrator(tmp_path, judge)
    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
    ready = next(data for t, data in opening if t == "segment_ready")
    choice = ready["choices"][0]

    judge._findings = _blocking_findings()
    with pytest.raises(RuntimeGenerationUnavailable):
        _collect_events(
            orch.execute_turn(pack, "s1", ready["revision"], "cmd-select", choice["id"])
        )

    state = store.load_session("s1")
    assert state.revision == ready["revision"] + 1
    assert state.pending_decision is None
    assert state.pending_consequence is not None
    assert state.pending_consequence.option_id == choice["id"]


def test_informational_findings_do_not_block(tmp_path: Path):
    judge = FakeSemanticJudge(
        JudgeFindings(
            findings=(
                JudgeFinding(
                    kind="pacing",
                    severity="informational",
                    detail="the segment lingers slightly long",
                ),
            )
        )
    )
    pack, store, orch = _build_orchestrator(tmp_path, judge)
    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-00", None))
    assert any(t == "segment_ready" for t, _ in events)
    assert store.load_session("s1").revision > 0


def test_judge_failure_fails_closed(tmp_path: Path):
    class ExplodingJudge(FakeSemanticJudge):
        async def judge_segment(self, pack, state, plan, draft, pending_choice=None):
            raise RuntimeError("judge model unavailable")

    pack, store, orch = _build_orchestrator(tmp_path, ExplodingJudge())
    with pytest.raises(RuntimeGenerationUnavailable):
        _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-00", None))
    assert store.load_events("s1") == ()


def test_judge_receives_the_committed_choice_meaning(tmp_path: Path):
    """On a consequence turn the judge must see the exact committed Choice
    Meaning so it can detect Choice Meaning reversal."""
    judge = FakeSemanticJudge()
    pack, _store, orch = _build_orchestrator(tmp_path, judge)
    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
    ready = next(data for t, data in opening if t == "segment_ready")
    choice = ready["choices"][0]

    _collect_events(orch.execute_turn(pack, "s1", ready["revision"], "cmd-select", choice["id"]))

    consequence_call = judge.calls[-1]
    pending = consequence_call["pending_choice"]
    assert pending is not None
    assert pending.id == choice["id"]
    assert pending.action_id == choice["action_id"]
    assert pending.intent == choice["intent"]


def test_judge_applies_to_cached_openings_too(tmp_path: Path):
    """A cached opening still passes through the judge before commit — the
    offline cache tooling cannot bypass the runtime acceptance gate."""
    from src.story.runtime.pacing import compute_pacing_envelope
    from src.story.runtime.pack_cache import CachedOpening, PackCache
    from src.story.runtime.simulator import simulate_segment
    from src.story.runtime.validator import (
        validate_segment_draft,
        validate_segment_plan,
    )

    pack = compile_source(budget_test_pack_dict())
    cache = PackCache(tmp_path / "pack_cache")
    state = initial_session_state(pack, "cache_builder", session_seed=1)
    director = FakeDirector()
    writer = FakeSegmentWriter()
    pacing = compute_pacing_envelope(state, pack)
    plan = asyncio.run(director.plan_segment(pack, state, pacing))
    plan = validate_segment_plan(pack, state, plan, pacing)
    draft = asyncio.run(writer.write_segment(pack, state, plan))
    draft = validate_segment_draft(plan, draft)
    cache.save_opening(
        pack.pack_hash,
        CachedOpening(
            segment_plan=plan,
            segment_draft=draft,
            seg_events=simulate_segment(pack, state, plan, draft),
            pacing=pacing,
        ),
    )

    judge = FakeSemanticJudge(_blocking_findings())
    store = StoryEventStore(tmp_path / "cached_judge.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=3))
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        pack_cache=cache,
        semantic_judge=judge,
    )
    with pytest.raises(RuntimeGenerationUnavailable):
        _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-cache", None))
    assert store.load_events("s1") == ()
