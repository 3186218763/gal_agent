"""Seam tests for issue 06: choice-density gating with a hard block floor."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.story.runtime.contracts import (
    EndingDraft,
    NarrativeBlock,
    RuntimeGenerationUnavailable,
    SceneDraft,
)
from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.segment_contracts import EndingProposal
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.validator import segment_density_errors
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
from tests.test_turn_orchestrator import _collect_events, _valid_unified_output


def _short_output(pack, state, pacing):
    """A structurally valid proposal whose blocks fall under the floor."""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        output = loop.run_until_complete(_valid_unified_output(pack, state, pacing))
    finally:
        loop.close()
    plan = output.segment_plan
    short_draft = output.segment_draft.model_copy(
        update={
            "scene_drafts": (
                SceneDraft(
                    scene_id=plan.scenes[0].scene_id,
                    blocks=(
                        NarrativeBlock(kind="narration", text="A rushed, too-short beat."),
                    ),
                ),
            )
        }
    )
    return plan, short_draft


def _floor_for(state, pack) -> int:
    pacing = compute_pacing_envelope(state, pack)
    return pacing.target_block_range[0]


def test_density_floor_comes_from_pacing_range():
    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=42)
    pacing = compute_pacing_envelope(state, pack)

    plan, draft = _short_output(pack, state, pacing)

    errors = segment_density_errors(plan, draft, pacing)
    assert errors, "a short decision segment must be rejected"
    assert str(pacing.target_block_range[0]) in errors[0]


def test_endings_have_their_own_finale_floor():
    """Endings are exempt from the DECISION floor but gated by ENDING_BLOCK_FLOOR.

    Only draft.ending.blocks is player-visible for endings (scene drafts are
    not committed), so scene prose never counts toward the finale floor.
    """
    from src.story.runtime.validator import ENDING_BLOCK_FLOOR

    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=42)
    pacing = compute_pacing_envelope(state, pack)

    plan, draft = _short_output(pack, state, pacing)
    ending_plan = plan.model_copy(
        update={
            "terminal": "ending",
            "scenes": (
                plan.scenes[0].model_copy(update={"terminal": "ending", "choices": ()}),
            ),
            "ending_proposal": EndingProposal(
                title="Farewell",
                tone="quiet",
                terminal_state_summary="They part ways.",
            ),
        }
    )

    # Short/no ending blocks are rejected even with dense scene drafts.
    errors = segment_density_errors(ending_plan, draft, pacing)
    assert errors, "a short finale must be rejected"
    assert str(ENDING_BLOCK_FLOOR) in errors[0]

    # A full payoff ending clears the floor.
    full_ending = draft.model_copy(
        update={
            "ending": EndingDraft(
                ending_id="end_goodbye",
                title="Farewell",
                blocks=tuple(
                    NarrativeBlock(kind="narration", text=f"Payoff beat {i}.")
                    for i in range(ENDING_BLOCK_FLOOR)
                ),
                tone="quiet",
                terminal_state_summary="They part ways.",
            )
        }
    )
    assert segment_density_errors(ending_plan, full_ending, pacing) == []


class _Agent:
    """Unified agent: short proposals until told otherwise."""

    def __init__(self, short_output, short_count: int) -> None:
        self.short_output = short_output
        self.short_left = short_count
        self.notes: list[tuple[str, ...]] = []

    async def generate(self, pack, state, pacing, *, rejection_notes=(), pending_choice=None):
        self.notes.append(rejection_notes)
        if self.short_left > 0:
            self.short_left -= 1
            from src.story.runtime.unified_segment import UnifiedSegmentOutput

            return UnifiedSegmentOutput(
                segment_plan=self.short_output[0], segment_draft=self.short_output[1]
            )
        return await _valid_unified_output(pack, state, pacing)


def _orchestrator(store, agent):
    return TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=None,
        planner=FakePlanner(),
        unified_agent=agent,
    )


def _short_agent(pack, short_count):
    state = initial_session_state(pack, "s1", session_seed=42)
    pacing = compute_pacing_envelope(state, pack)
    return _Agent(_short_output(pack, state, pacing), short_count)


def test_short_decision_segment_regenerates_then_commits(tmp_path: Path):
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "density_retry.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))
    agent = _short_agent(pack, short_count=1)
    orch = _orchestrator(store, agent)

    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-density", None))

    assert any(t == "segment_ready" for t, _data in events)
    stages = [data["stage"] for t, data in events if t == "progress"]
    assert "regenerating" in stages
    # the floor reason reached the writer as a rejection note
    assert agent.notes[1], "regeneration must carry the density reason"
    assert any("validator/density" in note for note in agent.notes[1])
    assert any("target_block_range" in note for note in agent.notes[1])

    record = store.load_turn_diagnostics("s1")[0]
    assert record["regenerations"] == 1
    assert record["validator_violations"], "density rejections are diagnosable"
    assert "target_block_range" in record["validator_violations"][0]


def test_second_density_rejection_fails_closed(tmp_path: Path):
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "density_fail.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))
    orch = _orchestrator(store, _short_agent(pack, short_count=99))

    with pytest.raises(RuntimeGenerationUnavailable) as excinfo:
        _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-density-fail", None))
    assert "density" in str(excinfo.value)

    record = store.load_turn_diagnostics("s1")[0]
    assert record["outcome"] == "failed"
    assert record["validator_violations"]
