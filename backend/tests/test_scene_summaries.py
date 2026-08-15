"""Seam tests for issue 03: self-produced scene summaries, committed and reused."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.contracts import ScenePlan
from src.story.runtime.segment_context import build_segment_writer_context
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.unified_segment import build_unified_context
from src.story.script_pack.compiler import compile_source
from src.story.state import SceneCommitted, initial_session_state
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)
from tests.test_turn_orchestrator import _collect_events, _valid_unified_output


def _scene_plan(**overrides) -> ScenePlan:
    fields = {
        "scene_id": "scene_01",
        "summary": "Alice waits for the protagonist to choose.",
        "location_id": "cafe",
        "present_character_ids": ("alice",),
        "terminal": "continue",
    }
    fields.update(overrides)
    return ScenePlan(**fields)


def test_scene_plan_requires_a_summary():
    with pytest.raises(ValidationError):
        _scene_plan(summary="")


def test_scene_plan_summary_must_be_one_line():
    with pytest.raises(ValidationError):
        _scene_plan(summary="line one\nline two")


def _orchestrator(store, unified_agent=None):
    return TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        unified_agent=unified_agent,
    )


def _segment_plan_for(pack, state):
    """A validator-clean SegmentPlan for the current state, via the fakes."""
    from src.story.runtime.pacing import compute_pacing_envelope
    from src.story.runtime.validator import validate_segment_plan

    pacing = compute_pacing_envelope(state, pack)

    async def run():
        plan = await FakeDirector().plan_segment(pack, state, pacing)
        return validate_segment_plan(pack, state, plan, pacing)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(run())
    finally:
        loop.close()


def test_summaries_commit_and_replay(tmp_path: Path):
    """Produce → commit: SceneCommitted carries the plan summary and the
    projection replays it from the event stream after a cold reopen."""
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "summaries.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))
    orch = _orchestrator(store)

    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
    assert any(t == "segment_ready" for t, _data in opening)

    committed = [
        envelope.event
        for envelope in store.load_events("s1")
        if isinstance(envelope.event, SceneCommitted)
    ]
    assert committed, "the opening must commit scenes"
    assert all(event.summary for event in committed)

    # cold reopen rebuilds scene_summaries purely from the event stream
    reopened = StoryEventStore(tmp_path / "summaries.db")
    state = reopened.load_session("s1")
    assert len(state.scene_summaries) == len(committed)
    assert [record.scene_id for record in state.scene_summaries] == [
        event.scene_id for event in committed
    ]
    assert [record.summary for record in state.scene_summaries] == [
        event.summary for event in committed
    ]


def test_later_segments_reuse_all_summaries(tmp_path: Path):
    """Reuse: the next segment's writer context carries every earlier summary."""
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "summaries_reuse.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))

    seen_contexts: list[dict] = []

    class RecordingAgent:
        calls = 0

        async def generate(self, pack, state, pacing, *, rejection_notes=(), pending_choice=None):
            RecordingAgent.calls += 1
            seen_contexts.append(
                build_unified_context(pack, state, pacing, pending_choice=pending_choice)
            )
            return await _valid_unified_output(pack, state, pacing)

    orch = _orchestrator(store, unified_agent=RecordingAgent())

    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
    ready = next(data for t, data in opening if t == "segment_ready")

    digests = [ctx["event_trace"] for ctx in seen_contexts]
    # opening: no story yet
    assert digests[0]["scene_summaries"] == []

    # play a full choice turn — its segment must see the opening summaries
    choice_id = ready["choices"][0]["id"]
    _collect_events(orch.execute_turn(pack, "s1", ready["revision"], f"cmd-c-{choice_id}", choice_id))

    opening_summaries = digests[0]["scene_summaries"]
    later = seen_contexts[-1]["event_trace"]["scene_summaries"]
    assert later, "the post-choice segment must see committed scene summaries"
    assert later[: len(opening_summaries)] == opening_summaries
    assert RecordingAgent.calls == 2  # one model call per segment — no extra calls


def test_writer_context_digest_includes_scene_summaries(tmp_path: Path):
    """The split-writer context path sees the same replayed outline."""
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "summaries_writer.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))
    orch = _orchestrator(store)

    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
    ready = next(data for t, data in opening if t == "segment_ready")
    choice_id = ready["choices"][0]["id"]
    _collect_events(orch.execute_turn(pack, "s1", ready["revision"], f"cmd-c-{choice_id}", choice_id))

    state = store.load_session("s1")
    plan = _segment_plan_for(pack, state)
    ctx = build_segment_writer_context(pack, state, plan)
    summaries = ctx["event_trace"]["scene_summaries"]
    assert summaries == [
        {"scene_id": record.scene_id, "summary": record.summary}
        for record in state.scene_summaries
    ]
