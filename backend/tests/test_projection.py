"""Tests for segment-aware session projection fields."""

from __future__ import annotations

from src.story.projection import (
    CompletionSummary,
    EndingProjection,
    project_session,
)
from src.story.script_pack import compile_source
from src.story.state import (
    CompletionAssessmentRecord,
    CompletionEvaluated,
    DecisionPresented,
    EndingGenerated,
    EventEnvelope,
    NarrativeBlock,
    PresentedChoice,
    apply_events,
    initial_session_state,
)
from tests.story_factories import minimal_pack_v2_dict, minimal_script_pack_dict


def _envelope(event, seq: int):
    """Wrap an event in an envelope with the given sequence number."""
    return EventEnvelope(session_id="s1", sequence=seq, event=event)


def test_projection_includes_segment_blocks():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    proj = project_session(state)
    assert hasattr(proj, "segment_blocks")
    assert proj.segment_blocks == ()


def test_projection_includes_segment_revision():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    proj = project_session(state)
    assert hasattr(proj, "segment_revision")
    assert proj.segment_revision is None


def test_ending_projection():
    proj = EndingProjection(
        ending_id="ending_s1",
        title="Finale",
        tone="epic",
        terminal_state_summary="The end.",
    )
    assert proj.ending_id == "ending_s1"


def test_completion_summary():
    summary = CompletionSummary(
        requirement_id="req_a",
        description="Find the truth",
        satisfied=True,
        rationale="Fact committed",
    )
    assert summary.satisfied is True


# ---------------------------------------------------------------------------
# Population tests — verify project_session reads segment data from state
# ---------------------------------------------------------------------------


def test_segment_choices_populated_from_decision():
    """DecisionPresented should populate segment_revision and segment_choices."""
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    choices = (
        PresentedChoice(id="c1", action_id="a1", label="Go left", intent="explore"),
        PresentedChoice(id="c2", action_id="a2", label="Go right", intent="explore"),
    )
    event = DecisionPresented(decision_id="d1", choices=choices)
    state = apply_events(state, (_envelope(event, 1),))
    proj = project_session(state)
    assert proj.segment_revision == 1
    assert len(proj.segment_choices) == 2
    assert proj.segment_choices[0].id == "c1"


def test_segment_ending_populated_from_ending_generated():
    """EndingGenerated should populate segment_ending with tone/summary."""
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    event = EndingGenerated(
        ending_id="ending_good",
        title="Dawn",
        tone="hopeful",
        terminal_state_summary="Everyone survived.",
        blocks=(NarrativeBlock(kind="narration", text="The sun rose."),),
    )
    state = apply_events(state, (_envelope(event, 1),))
    proj = project_session(state)
    assert proj.segment_ending is not None
    assert proj.segment_ending.ending_id == "ending_good"
    assert proj.segment_ending.tone == "hopeful"
    assert proj.segment_ending.terminal_state_summary == "Everyone survived."


def test_segment_blocks_populated_from_ending():
    """EndingGenerated blocks should appear in segment_blocks."""
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    blocks = (NarrativeBlock(kind="narration", text="Finale text."),)
    event = EndingGenerated(
        ending_id="ending_good",
        title="Dawn",
        tone="hopeful",
        terminal_state_summary="Everyone survived.",
        blocks=blocks,
    )
    state = apply_events(state, (_envelope(event, 1),))
    proj = project_session(state)
    assert len(proj.segment_blocks) == 1
    assert proj.segment_blocks[0].text == "Finale text."


def test_completion_summaries_populated():
    """CompletionEvaluated should populate cleared and completion_summaries."""
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    # Enter ending first (required by reducer)
    ending_event = EndingGenerated(
        ending_id="ending_good",
        title="Dawn",
        tone="hopeful",
        terminal_state_summary="Everyone survived.",
        blocks=(NarrativeBlock(kind="narration", text="The end."),),
    )
    state = apply_events(state, (_envelope(ending_event, 1),))

    assessments = (
        CompletionAssessmentRecord(
            requirement_id="req_truth",
            satisfied=True,
            rationale="Fact committed",
        ),
    )
    completion_event = CompletionEvaluated(cleared=True, assessments=assessments)
    state = apply_events(state, (_envelope(completion_event, 2),))
    proj = project_session(state)
    assert proj.cleared is True
    assert len(proj.completion_summaries) == 1
    assert proj.completion_summaries[0].requirement_id == "req_truth"
    assert proj.completion_summaries[0].satisfied is True


def test_completion_summaries_with_pack_descriptions():
    """When a pack is provided, CompletionSummary.description comes from the pack."""
    pack = compile_source(minimal_pack_v2_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    ending_event = EndingGenerated(
        ending_id="ending_good",
        title="Dawn",
        tone="hopeful",
        terminal_state_summary="Everyone survived.",
        blocks=(NarrativeBlock(kind="narration", text="The end."),),
    )
    state = apply_events(state, (_envelope(ending_event, 1),))

    # Use the first completion requirement id from the pack
    req_id = pack.source.completion_requirements[0].id
    req_desc = pack.source.completion_requirements[0].description

    assessments = (
        CompletionAssessmentRecord(
            requirement_id=req_id,
            satisfied=True,
            rationale="Done",
        ),
    )
    completion_event = CompletionEvaluated(cleared=True, assessments=assessments)
    state = apply_events(state, (_envelope(completion_event, 2),))
    proj = project_session(state, pack=pack)
    assert proj.completion_summaries[0].description == req_desc


def test_segment_ending_none_for_plain_ending_entered():
    """EndingEntered (v1, no tone/summary) should not populate segment_ending."""
    from src.story.state import EndingEntered, EndingRuntime

    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    ending = EndingRuntime(
        ending_id="ending_neutral",
        entered_at_revision=1,
        title="Finale",
        blocks=(NarrativeBlock(kind="narration", text="The end."),),
    )
    event = EndingEntered(ending=ending)
    state = apply_events(state, (_envelope(event, 1),))
    proj = project_session(state)
    assert proj.segment_ending is None
