import pytest

from src.story.runtime.contracts import (
    ChoicePlan,
    NarrativeBlock,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.segment_contracts import (
    EndingProposal,
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
    ThreadOperation,
)
from src.story.runtime.validator import (
    ProposalRejected,
    validate_segment_draft,
    validate_segment_plan,
)
from src.story.script_pack.compiler import compile_source
from src.story.state import StoryPhase
from tests.story_factories import minimal_script_pack_dict


def _make_pack():
    return compile_source(minimal_script_pack_dict())


def _make_pacing(**overrides):
    defaults = {
        "phase": StoryPhase.EXPLORATION,
        "scene_count": 5,
        "min_scenes": 8,
        "max_scenes": 20,
        "reserved_resolution_scenes": 3,
        "remaining_budget": 15,
        "can_end": False,
        "must_end": False,
        "in_convergence": False,
        "max_new_threads": 3,
        "quiet_scene_allowance": 2,
    }
    defaults.update(overrides)
    return PacingEnvelope(**defaults)


def _make_state():
    from src.story.state import initial_session_state
    pack = _make_pack()
    return initial_session_state(pack, "s1", session_seed=1)


def _make_continue_scene(scene_id="scene_01"):
    return ScenePlan(
        scene_id=scene_id,
        summary="A scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="continue",
    )


def _make_decision_scene(scene_id="scene_02"):
    return ScenePlan(
        scene_id=scene_id,
        summary="Decision point",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="dec_01",
        choices=(
            ChoicePlan(option_id="ask", action_id="ask", intent="Ask directly"),
            ChoicePlan(option_id="observe", action_id="observe", intent="Watch carefully"),
        ),
    )


def test_valid_decision_segment_plan():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing()
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_scene(),),
        terminal="decision",
    )
    result = validate_segment_plan(pack, state, plan, pacing)
    assert result.segment_id == "seg_01"


def test_valid_ending_segment_plan():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(can_end=True)
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_make_continue_scene(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Finale", tone="epic", terminal_state_summary="The end.",
        ),
    )
    result = validate_segment_plan(pack, state, plan, pacing)
    assert result.terminal == "ending"


def test_ending_before_min_scenes_rejected():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(can_end=False, scene_count=2, min_scenes=8)
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_make_continue_scene(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Finale", tone="epic", terminal_state_summary="The end.",
        ),
    )
    with pytest.raises(ProposalRejected, match="min_scenes"):
        validate_segment_plan(pack, state, plan, pacing)


def test_ending_without_proposal_rejected():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(can_end=True)
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_make_continue_scene(),),
        terminal="ending",
    )
    with pytest.raises(ProposalRejected, match="ending_proposal"):
        validate_segment_plan(pack, state, plan, pacing)


def test_must_end_forces_ending():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(must_end=True, scene_count=20, max_scenes=20)
    plan = SegmentPlan(
        segment_id="seg_03",
        scenes=(_make_continue_scene(),),
        terminal="decision",
    )
    with pytest.raises(ProposalRejected, match="must_end"):
        validate_segment_plan(pack, state, plan, pacing)


def test_thread_op_in_convergence_rejected():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(in_convergence=True, max_new_threads=0)
    plan = SegmentPlan(
        segment_id="seg_04",
        scenes=(_make_continue_scene(),),
        terminal="decision",
        thread_ops=(
            ThreadOperation(
                kind="open",
                thread_id="new_thread",
                thread_type="mystery",
            ),
        ),
    )
    with pytest.raises(ProposalRejected, match="convergence"):
        validate_segment_plan(pack, state, plan, pacing)


def test_too_many_scenes_rejected():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(remaining_budget=1, scene_count=19, max_scenes=20)
    plan = SegmentPlan(
        segment_id="seg_05",
        scenes=(_make_continue_scene("s1"), _make_continue_scene("s2")),
        terminal="decision",
    )
    with pytest.raises(ProposalRejected, match="budget"):
        validate_segment_plan(pack, state, plan, pacing)


# --- Segment draft validation ---


def test_valid_segment_draft():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_scene(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="Text."),),
            ),
        ),
        choices=(
            WrittenChoice(option_id="ask", label="Ask directly"),
            WrittenChoice(option_id="observe", label="Watch carefully"),
        ),
    )
    result = validate_segment_draft(plan, draft)
    assert result.segment_id == "seg_01"


def test_segment_draft_id_mismatch_rejected():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_scene(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_02",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="Text."),),
            ),
        ),
    )
    with pytest.raises(ProposalRejected, match="segment_id"):
        validate_segment_draft(plan, draft)


def test_segment_draft_scene_count_mismatch_rejected():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_scene("s1"), _make_continue_scene("s2")),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(
            SceneDraft(
                scene_id="s1",
                blocks=(NarrativeBlock(kind="narration", text="Text."),),
            ),
        ),
    )
    with pytest.raises(ProposalRejected, match="scene count"):
        validate_segment_draft(plan, draft)
