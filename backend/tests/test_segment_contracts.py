import pytest
from pydantic import ValidationError

from src.story.runtime.contracts import (
    ChoicePlan,
    EndingDraft,
    NarrativeBlock,
    SceneDraft,
    ScenePlan,
)
from src.story.runtime.segment_contracts import (
    CompletionAssessment,
    CompletionResult,
    EndingProposal,
    GuardResult,
    GuardViolation,
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
    ThreadOperation,
)
from src.story.state import StoryPhase


def _make_scene_plan(scene_id="scene_01", terminal="continue"):
    return ScenePlan(
        scene_id=scene_id,
        summary="A scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal=terminal,
    )


def _make_decision_scene_plan(scene_id="scene_dec"):
    return ScenePlan(
        scene_id=scene_id,
        summary="A decision scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="dec_01",
        choices=(
            ChoicePlan(option_id="opt_1", action_id="act_1", intent="Go left"),
            ChoicePlan(option_id="opt_2", action_id="act_2", intent="Go right"),
        ),
    )


def _make_ending_scene_plan(scene_id="scene_end"):
    return ScenePlan(
        scene_id=scene_id,
        summary="An ending scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="ending",
    )


def _make_scene_draft(scene_id="scene_01"):
    return SceneDraft(
        scene_id=scene_id,
        blocks=(NarrativeBlock(kind="narration", text="Text."),),
    )


def test_ending_proposal():
    proposal = EndingProposal(
        title="The Long Goodbye",
        tone="bittersweet",
        terminal_state_summary="Alice left the city.",
    )
    assert proposal.title == "The Long Goodbye"


def test_thread_operation_open():
    op = ThreadOperation(
        kind="open",
        thread_id="thread_mystery",
        thread_type="mystery",
        involved_character_ids=("alice",),
    )
    assert op.kind == "open"
    assert op.thread_type == "mystery"


def test_thread_operation_advance():
    op = ThreadOperation(
        kind="advance",
        thread_id="thread_mystery",
        urgency=0.8,
    )
    assert op.kind == "advance"


def test_thread_operation_close():
    op = ThreadOperation(
        kind="close",
        thread_id="thread_mystery",
        close_status="resolved",
    )
    assert op.close_status == "resolved"


def test_pacing_envelope():
    env = PacingEnvelope(
        phase=StoryPhase.EXPLORATION,
        scene_count=5,
        min_scenes=8,
        max_scenes=20,
        reserved_resolution_scenes=3,
        remaining_budget=15,
        can_end=False,
        must_end=False,
        in_convergence=False,
        max_new_threads=3,
        quiet_scene_allowance=2,
    )
    assert env.phase == StoryPhase.EXPLORATION
    assert env.can_end is False


def test_segment_plan_decision():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_decision_scene_plan(),),
        terminal="decision",
    )
    assert plan.segment_id == "seg_01"
    assert plan.terminal == "decision"
    assert plan.ending_proposal is None


def test_segment_plan_ending():
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_make_ending_scene_plan(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Finale", tone="epic", terminal_state_summary="The end.",
        ),
    )
    assert plan.ending_proposal is not None
    assert plan.ending_proposal.title == "Finale"


def test_segment_plan_requires_min_one_scene():
    with pytest.raises(ValidationError):
        SegmentPlan(segment_id="seg_01", scenes=(), terminal="decision")


def test_segment_plan_ending_without_proposal_allowed():
    """Model allows construction; validate_segment_plan enforces ending_proposal."""
    plan = SegmentPlan(
        segment_id="seg_03",
        scenes=(_make_ending_scene_plan(),),
        terminal="ending",
    )
    assert plan.ending_proposal is None


def test_segment_plan_ending_with_continue_last_scene_allowed():
    """Model allows construction; validate_segment_plan enforces terminal consistency."""
    plan = SegmentPlan(
        segment_id="seg_04",
        scenes=(_make_scene_plan(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Finale", tone="epic", terminal_state_summary="The end.",
        ),
    )
    assert plan.scenes[-1].terminal == "continue"


def test_segment_plan_decision_with_continue_last_scene_allowed():
    """Model allows construction; validate_segment_plan enforces terminal consistency."""
    plan = SegmentPlan(
        segment_id="seg_05",
        scenes=(_make_scene_plan(),),
        terminal="decision",
    )
    assert plan.scenes[-1].terminal == "continue"


def test_segment_draft():
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(_make_scene_draft(),),
    )
    assert draft.segment_id == "seg_01"
    assert draft.ending is None


def test_segment_draft_with_ending():
    draft = SegmentDraft(
        segment_id="seg_02",
        scene_drafts=(_make_scene_draft(),),
        ending=EndingDraft(
            ending_id="ending_001",
            title="Finale",
            blocks=(NarrativeBlock(kind="narration", text="The end."),),
            tone="epic",
            terminal_state_summary="World saved.",
        ),
    )
    assert draft.ending is not None
    assert draft.ending.tone == "epic"


def test_guard_result_passed():
    result = GuardResult(passed=True)
    assert result.passed is True
    assert result.violations == ()


def test_guard_result_with_violations():
    result = GuardResult(
        passed=False,
        violations=(
            GuardViolation(
                kind="knowledge_leak",
                block_index=2,
                character_id="alice",
                detail="Alice reveals a secret she does not know.",
            ),
        ),
    )
    assert result.passed is False
    assert len(result.violations) == 1
    assert result.violations[0].kind == "knowledge_leak"


def test_completion_assessment():
    a = CompletionAssessment(
        requirement_id="req_a",
        satisfied=True,
        cited_event_ids=("evt-1",),
        rationale="Fact committed",
    )
    assert a.satisfied is True


def test_completion_result():
    result = CompletionResult(
        assessments=(
            CompletionAssessment(requirement_id="req_a", satisfied=True, rationale="ok"),
            CompletionAssessment(requirement_id="req_b", satisfied=False, rationale="no"),
        ),
        cleared=False,
    )
    assert len(result.assessments) == 2
    assert result.cleared is False
