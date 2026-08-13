import pytest

from src.story.runtime.contracts import (
    NarrativeBlock,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.segment_contracts import (
    SegmentDraft,
    SegmentPlan,
)
from src.story.runtime.simulator import segment_events, simulate_segment
from src.story.script_pack.compiler import compile_source
from src.story.state import (
    SceneAcknowledged,
    initial_session_state,
)
from tests.story_factories import minimal_script_pack_dict


def _make_pack():
    return compile_source(minimal_script_pack_dict())


def _make_state():
    pack = _make_pack()
    return initial_session_state(pack, "s1", session_seed=1)


def _make_continue_plan(scene_id="scene_01"):
    return ScenePlan(
        scene_id=scene_id,
        summary="A scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="continue",
    )


def _make_continue_draft(scene_id="scene_01"):
    return SceneDraft(
        scene_id=scene_id,
        blocks=(NarrativeBlock(kind="narration", text="A quiet moment."),),
    )


def test_decision_segment_events():
    state = _make_state()
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_plan(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(_make_continue_draft(),),
        choices=(
            WrittenChoice(option_id="ask", label="Ask directly"),
            WrittenChoice(option_id="observe", label="Watch carefully"),
        ),
    )
    events = segment_events(_make_pack(), state, plan, draft)
    event_types = [type(e).__name__ for e in events]
    assert "SceneCommitted" in event_types
    assert "DecisionPresented" in event_types
    # No SceneAcknowledged needed for single-scene segment.
    assert "SceneAcknowledged" not in event_types


def test_multi_scene_segment_auto_acks():
    state = _make_state()
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(
            _make_continue_plan("scene_01"),
            _make_continue_plan("scene_02"),
        ),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_02",
        scene_drafts=(
            _make_continue_draft("scene_01"),
            _make_continue_draft("scene_02"),
        ),
        choices=(
            WrittenChoice(option_id="ask", label="Ask"),
            WrittenChoice(option_id="observe", label="Watch"),
        ),
    )
    events = segment_events(_make_pack(), state, plan, draft)
    ack_count = sum(1 for e in events if isinstance(e, SceneAcknowledged))
    assert ack_count == 1  # auto-ack between scene 1 and scene 2


def test_ending_segment_events():
    state = _make_state()
    # Force state past min_scenes for ending.
    state = state.model_copy(update={"world": state.world.model_copy(update={"scene_count": 10})})
    from src.story.runtime.segment_contracts import EndingProposal

    plan = SegmentPlan(
        segment_id="seg_03",
        scenes=(_make_continue_plan(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="The Long Goodbye",
            tone="bittersweet",
            terminal_state_summary="Alice left the city.",
        ),
    )
    draft = SegmentDraft(
        segment_id="seg_03",
        scene_drafts=(_make_continue_draft(),),
        ending=__import__("src.story.runtime.contracts", fromlist=["EndingDraft"]).EndingDraft(
            ending_id="ending_s1_001",
            title="The Long Goodbye",
            blocks=(NarrativeBlock(kind="narration", text="They parted."),),
            tone="bittersweet",
            terminal_state_summary="Alice left the city.",
        ),
    )
    events = segment_events(_make_pack(), state, plan, draft)
    event_types = [type(e).__name__ for e in events]
    assert "EndingGenerated" in event_types


def test_simulate_segment_validates():
    state = _make_state()
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_plan(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(_make_continue_draft(),),
        choices=(
            WrittenChoice(option_id="ask", label="Ask"),
            WrittenChoice(option_id="observe", label="Watch"),
        ),
    )
    events = simulate_segment(_make_pack(), state, plan, draft)
    assert len(events) > 0


def test_simulate_segment_exceeding_max_raises():
    from src.story.state import StateTransitionError

    state = _make_state()
    # Set scene_count to max to force overflow.
    state = state.model_copy(update={"world": state.world.model_copy(update={"scene_count": 20})})
    plan = SegmentPlan(
        segment_id="seg_04",
        scenes=(_make_continue_plan(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_04",
        scene_drafts=(_make_continue_draft(),),
        choices=(
            WrittenChoice(option_id="ask", label="Ask"),
            WrittenChoice(option_id="observe", label="Watch"),
        ),
    )
    with pytest.raises(StateTransitionError):
        simulate_segment(_make_pack(), state, plan, draft)
