"""Convergence-window reveal exemption for ending segments.

A multi-evidence latent question (evidence_required > 1) may be committed and
revealed in one ending scene — the finale is the payoff.  Validator, guard,
simulator, and reducer all honor the same boundary: every non-ending reveal
still rides the evidence ladder.
"""

from __future__ import annotations

import pytest

from src.story.runtime.contracts import (
    ChoicePlan,
    EndingDraft,
    FactCommitPlan,
    NarrativeBlock,
    SceneDraft,
    ScenePlan,
)
from src.story.runtime.guard import Guard
from src.story.runtime.segment_contracts import (
    EndingProposal,
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
)
from src.story.runtime.simulator import segment_events
from src.story.runtime.validator import (
    ProposalRejected,
    validate_scene_plan,
    validate_segment_plan,
)
from src.story.script_pack import compile_source
from src.story.state import (
    EventEnvelope,
    FactCommitted,
    FactRevealed,
    FactTruthStatus,
    FactVisibility,
    StoryPhase,
    apply_event,
    initial_session_state,
)
from src.story.state.reducer import StateTransitionError
from tests.story_factories import minimal_script_pack_dict


def _make_pack():
    data = minimal_script_pack_dict()
    data["facts"]["latent_questions"][0]["evidence_required"] = 2
    return compile_source(data)


def _make_state():
    return initial_session_state(_make_pack(), "s1", session_seed=1)


def _make_pacing(**overrides):
    defaults = {
        "phase": StoryPhase.RESOLUTION,
        "scene_count": 12,
        "min_scenes": 8,
        "max_scenes": 20,
        "reserved_resolution_scenes": 3,
        "remaining_budget": 5,
        "can_end": True,
        "must_end": False,
        "in_convergence": True,
        "max_new_threads": 0,
        "quiet_scene_allowance": 2,
        "target_block_range": (8, 25),
    }
    defaults.update(overrides)
    return PacingEnvelope(**defaults)


def _reveal_commit():
    return FactCommitPlan(
        fact_id="who_took_notebook",
        value="stranger",
        reason="explicit_revelation",
        reveal=True,
        learned_by=("alice",),
    )


def _make_ending_plan():
    scene = ScenePlan(
        scene_id="scene_20",
        summary="The notebook's keeper steps into the light.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="ending",
        fact_commits=(_reveal_commit(),),
    )
    return SegmentPlan(
        segment_id="seg_20",
        scenes=(scene,),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Closing Time",
            tone="reflective",
            terminal_state_summary="The mystery is settled.",
        ),
    )


def _make_ending_draft():
    return SegmentDraft(
        segment_id="seg_20",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_20",
                blocks=(NarrativeBlock(kind="narration", text="The truth lands at last."),),
            ),
        ),
        ending=EndingDraft(
            ending_id="truth_ending",
            title="Closing Time",
            blocks=(NarrativeBlock(kind="narration", text="Rain stops."),),
            tone="reflective",
            terminal_state_summary="The mystery is settled.",
        ),
    )


def test_ending_segment_may_reveal_multi_evidence_fact():
    plan = validate_segment_plan(_make_pack(), _make_state(), _make_ending_plan(), _make_pacing())
    assert plan.terminal == "ending"


def test_scene_plan_still_rejects_one_scene_reveal_by_default():
    state = _make_state()
    pack = _make_pack()
    scene = ScenePlan(
        scene_id="scene_20",
        summary="The notebook's keeper steps into the light.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="ending",
        fact_commits=(_reveal_commit(),),
    )
    with pytest.raises(ProposalRejected) as excinfo:
        validate_scene_plan(pack, state, scene)
    assert "cannot be revealed by one scene" in str(excinfo.value)


def test_segment_events_flags_finale_and_reducer_settles_question():
    state = _make_state()
    events = segment_events(_make_pack(), state, _make_ending_plan(), _make_ending_draft())
    reveals = [e for e in events if isinstance(e, FactRevealed)]
    assert len(reveals) == 1
    assert reveals[0].finale is True

    next_state = state
    for index, event in enumerate(events, start=1):
        next_state = apply_event(
            next_state,
            EventEnvelope(
                event_id=f"finale-{index}",
                session_id=state.session_id,
                sequence=next_state.revision + 1,
                event=event,
            ),
        )
    fact = next_state.facts["who_took_notebook"]
    assert fact.truth_status == FactTruthStatus.COMMITTED
    assert fact.visibility == FactVisibility.REVEALED
    assert fact.value == "stranger"
    assert next_state.ending is not None


def test_reducer_keeps_evidence_ladder_for_non_finale_reveal():
    state = _make_state()

    def _apply(event, index):
        return apply_event(
            state,
            EventEnvelope(
                event_id=f"fin-{index}",
                session_id=state.session_id,
                sequence=state.revision + index,
                event=event,
            ),
        )

    committed = _apply(
        FactCommitted(fact_id="who_took_notebook", value="stranger", evidence_event_ids=()), 1
    )
    with pytest.raises(StateTransitionError) as excinfo:
        apply_event(
            committed,
            EventEnvelope(
                event_id="fin-reveal",
                session_id=committed.session_id,
                sequence=committed.revision + 1,
                event=FactRevealed(fact_id="who_took_notebook"),
            ),
        )
    assert "lacks required evidence" in str(excinfo.value)


def test_guard_exempts_ending_segment_evidence_count():
    pack = _make_pack()
    state = _make_state()
    guard = Guard()

    ending_result = guard.check_segment(
        pack, state, _make_ending_plan(), _make_ending_draft()
    )
    assert not [
        v for v in ending_result.violations if "sufficient evidence" in v.detail
    ]

    decision_scene = ScenePlan(
        scene_id="scene_20",
        summary="A hasty accusation.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="dec_20",
        choices=(
            ChoicePlan(option_id="opt_a", action_id="ask", intent="ask"),
            ChoicePlan(option_id="opt_b", action_id="observe", intent="observe"),
        ),
        fact_commits=(_reveal_commit(),),
    )
    decision_plan = SegmentPlan(
        segment_id="seg_20",
        scenes=(decision_scene,),
        terminal="decision",
    )
    decision_draft = SegmentDraft(
        segment_id="seg_20",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_20",
                blocks=(NarrativeBlock(kind="narration", text="A hasty accusation."),),
            ),
        ),
        choices=(),
    )
    result = guard.check_segment(pack, state, decision_plan, decision_draft)
    assert [v for v in result.violations if "sufficient evidence" in v.detail]
