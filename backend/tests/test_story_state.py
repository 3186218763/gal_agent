from pathlib import Path

import pytest
from pydantic import ValidationError

from src.story.script_pack import compile_script_pack, compile_source
from src.story.state import (
    FactTruthStatus,
    FactVisibility,
    StoryPhase,
    initial_session_state,
)
from src.story.state.models import (
    CompletionAssessmentRecord,
    CompletionState,
    EndingRuntime,
    NarrativeBlock,
)
from tests.story_factories import minimal_script_pack_dict


def test_initial_state_separates_truth_visibility_and_character_knowledge():
    pack = compile_source(minimal_script_pack_dict())

    state = initial_session_state(pack, "session_01", session_seed=42)

    assert state.revision == 0
    assert state.world.phase == StoryPhase.OPENING
    assert state.world.location_id == "cafe"
    assert state.facts["cafe_is_open"].truth_status == FactTruthStatus.COMMITTED
    assert state.facts["cafe_is_open"].visibility == FactVisibility.REVEALED
    assert state.facts["who_took_notebook"].truth_status == FactTruthStatus.POSSIBLE
    assert state.facts["who_took_notebook"].value is None
    assert "cafe_is_open" in state.characters["alice"].knowledge
    assert state.world.relationships["alice"]["trust"] == 35
    assert state.world.goals["alice_find_ally"].progress == 0


def test_real_pack_state_keeps_private_fixed_fact_hidden():
    pack = compile_script_pack(
        Path(__file__).resolve().parents[1] / "script_packs" / "cafe_mystery"
    )

    state = initial_session_state(pack, "session_02", session_seed=7)

    assert state.facts["org_exists"].truth_status == FactTruthStatus.COMMITTED
    assert state.facts["org_exists"].visibility == FactVisibility.HIDDEN
    assert "org_exists" in state.characters["alice"].knowledge
    assert "org_exists" in state.characters["bob"].knowledge


def test_session_state_is_immutable():
    state = initial_session_state(
        compile_source(minimal_script_pack_dict()),
        "session_01",
        session_seed=42,
    )

    with pytest.raises(ValidationError):
        state.revision = 1


# ---------------------------------------------------------------------------
# CompletionAssessmentRecord / CompletionState
# ---------------------------------------------------------------------------


def test_completion_assessment_record_minimal():
    record = CompletionAssessmentRecord(
        requirement_id="core_truth",
        satisfied=True,
    )
    assert record.requirement_id == "core_truth"
    assert record.satisfied is True
    assert record.cited_event_ids == ()
    assert record.rationale == ""


def test_completion_assessment_record_full():
    record = CompletionAssessmentRecord(
        requirement_id="protagonist_choice",
        satisfied=False,
        cited_event_ids=("evt-1", "evt-2"),
        rationale="No irreversible choice was made",
    )
    assert record.satisfied is False
    assert len(record.cited_event_ids) == 2


def test_completion_state():
    assessment = CompletionAssessmentRecord(
        requirement_id="req_a",
        satisfied=True,
        rationale="ok",
    )
    state = CompletionState(cleared=True, assessments=(assessment,))
    assert state.cleared is True
    assert len(state.assessments) == 1


def test_completion_state_not_cleared():
    state = CompletionState(cleared=False, assessments=())
    assert state.cleared is False


# ---------------------------------------------------------------------------
# EndingRuntime v2 optional fields
# ---------------------------------------------------------------------------


def test_ending_runtime_v2_fields():
    ending = EndingRuntime(
        ending_id="ending_sess_001",
        entered_at_revision=10,
        title="The Long Goodbye",
        blocks=(NarrativeBlock(kind="narration", text="They parted."),),
        tone="bittersweet",
        terminal_state_summary="Alice left the city.",
    )
    assert ending.tone == "bittersweet"
    assert ending.terminal_state_summary == "Alice left the city."
    assert ending.required_payoffs == ()
    assert ending.final_scene_budget == 1


def test_ending_runtime_v1_backward_compat():
    ending = EndingRuntime(
        ending_id="ally_ending",
        entered_at_revision=5,
        required_payoffs=("Alice and the protagonist cooperate.",),
        final_scene_budget=2,
        title="Together",
        blocks=(NarrativeBlock(kind="narration", text="The end."),),
    )
    assert ending.tone is None
    assert ending.required_payoffs == ("Alice and the protagonist cooperate.",)
