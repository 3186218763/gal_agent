"""Seam tests for issue 04: deterministic digest, obligation ledger, mutual exclusion."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.segment_context import _event_trace_digest, _fact_summary_views
from src.story.runtime.simulator import simulate_segment
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.validator import ProposalRejected, validate_segment_plan
from src.story.script_pack.compiler import compile_source
from src.story.state import (
    EventEnvelope,
    FactCommitted,
    ObligationCreated,
    ObligationResolved,
    apply_events,
    initial_session_state,
)
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)

OBLIGATION_ID = "obligation:test_promise"


def _apply(state, *events):
    envelopes = tuple(
        EventEnvelope(
            event_id=f"evt-{state.revision + index}",
            session_id=state.session_id,
            sequence=state.revision + index,
            event=event,
        )
        for index, event in enumerate(events, start=1)
    )
    return apply_events(state, envelopes)


def _state_with_open_obligation():
    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=42)
    state = _apply(
        state,
        ObligationCreated(
            obligation_id=OBLIGATION_ID,
            kind="promise_kept",
            burden=1,
            source_choice_event_id="choice-1",
        ),
    )
    return pack, state


def test_digest_is_deterministic_across_replays():
    _pack, state = _state_with_open_obligation()

    first = _event_trace_digest(state)
    second = _event_trace_digest(state)

    assert first == second
    assert first["outstanding_obligations"] == [
        {
            "obligation_id": OBLIGATION_ID,
            "kind": "promise_kept",
            "burden": 1,
            "character_id": None,
            "source_choice_event_id": "choice-1",
        }
    ]


def test_obligation_registers_on_creation_and_retires_on_resolution():
    _pack, state = _state_with_open_obligation()

    assert [item["obligation_id"] for item in _event_trace_digest(state)["outstanding_obligations"]] == [
        OBLIGATION_ID
    ]

    settled = _apply(
        state,
        ObligationResolved(
            obligation_id=OBLIGATION_ID,
            outcome="fulfilled",
            resolution_scene_event_id="scene-evt-1",
        ),
    )
    # retired: the settled obligation never appears in the writer's view again
    assert _event_trace_digest(settled)["outstanding_obligations"] == []


def test_latent_facts_present_one_exclusive_truth():
    pack, state = _state_with_open_obligation()

    views = {view["id"]: view for view in _fact_summary_views(pack, state)}
    latent = next(view for view in views.values() if view["kind"] == "latent")
    assert latent["mutually_exclusive"] is True
    assert latent["candidates"], "uncommitted latent facts list their candidate group"

    committed = _apply(
        state,
        FactCommitted(fact_id=latent["id"], value=latent["candidates"][0]["value"], evidence_event_ids=()),
    )
    views = {view["id"]: view for view in _fact_summary_views(pack, committed)}
    settled_view = views[latent["id"]]
    assert "candidates" not in settled_view
    assert "mutually_exclusive" not in settled_view
    assert settled_view["value"] == latent["candidates"][0]["value"]


def test_segment_settlement_validates_simulates_and_rewrites(tmp_path: Path):
    pack, state = _state_with_open_obligation()

    pacing = compute_pacing_envelope(state, pack)

    async def base_plan():
        plan = await FakeDirector().plan_segment(pack, state, pacing)
        return validate_segment_plan(pack, state, plan, pacing)

    plan = asyncio.new_event_loop().run_until_complete(base_plan())
    plan = plan.model_copy(update={"resolved_obligation_ids": (OBLIGATION_ID,)})

    # unknown ids are rejected before anything simulates or commits
    bad = plan.model_copy(update={"resolved_obligation_ids": ("obligation:ghost",)})
    with pytest.raises(ProposalRejected) as excinfo:
        validate_segment_plan(pack, state, bad, pacing)
    assert "obligation:ghost" in " ".join(excinfo.value.errors)

    # the settlement event rides the segment with a placeholder scene citation
    draft = _draft_for(pack, state, plan)
    events = simulate_segment(pack, state, plan, draft)
    resolved_event = next(
        event for event in events if isinstance(event, ObligationResolved)
    )
    assert resolved_event.obligation_id == OBLIGATION_ID
    assert resolved_event.resolution_scene_event_id.startswith("scene_ref:")

    # the authoritative flow rewrites the placeholder to the committed scene id
    orchestrator = TurnOrchestrator(
        store=StoryEventStore(tmp_path / "settlement.db"),
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    event_ids = tuple(f"s1:{index}" for index in range(1, len(events) + 1))
    rewritten = orchestrator._resolve_internal_references("s1", list(events), event_ids)
    final = next(event for event in rewritten if isinstance(event, ObligationResolved))
    assert not final.resolution_scene_event_id.startswith("scene_ref:")
    assert final.resolution_scene_event_id in event_ids


async def _draft_async(pack, state, plan):
    return await FakeSegmentWriter().write_segment(pack, state, plan)


def _draft_for(pack, state, plan):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_draft_async(pack, state, plan))
    finally:
        loop.close()
