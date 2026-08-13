import pytest

from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    LearnedFactPlan,
    RelationshipDelta,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.validator import (
    ProposalRejected,
    validate_action_resolution,
    validate_scene_draft,
    validate_scene_plan,
)
from src.story.script_pack import compile_source
from src.story.state import NarrativeBlock, initial_session_state
from tests.story_factories import minimal_script_pack_dict


def compiled_state():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "session_01", session_seed=42)
    return state, pack


def valid_decision_plan() -> ScenePlan:
    return ScenePlan(
        scene_id="scene_01",
        summary="Alice waits for the protagonist to choose.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="decision_01",
        choices=(
            ChoicePlan(option_id="ask", action_id="ask", intent="ask directly"),
            ChoicePlan(option_id="observe", action_id="observe", intent="watch carefully"),
        ),
    )


def valid_scene_draft(plan: ScenePlan) -> SceneDraft:
    return SceneDraft(
        scene_id=plan.scene_id,
        blocks=(NarrativeBlock(kind="narration", text="The cafe hums quietly."),),
        choices=tuple(
            WrittenChoice(option_id=item.option_id, label=item.intent[:80]) for item in plan.choices
        ),
    )


def test_plan_rejects_unknown_character_and_action():
    state, pack = compiled_state()
    plan = valid_decision_plan().model_copy(
        update={
            "present_character_ids": ("invented",),
            "choices": (
                ChoicePlan(option_id="x", action_id="hack", intent="cheat"),
                ChoicePlan(option_id="y", action_id="observe", intent="watch"),
            ),
        }
    )
    with pytest.raises(ProposalRejected) as exc:
        validate_scene_plan(pack, state, plan)
    assert "unknown character" in str(exc.value)
    assert "unavailable action" in str(exc.value)


def test_resolution_rejects_out_of_bounds_relationship_change():
    state, pack = compiled_state()
    resolution = ActionResolution(
        action_id="ask",
        outcome="success",
        relationship_deltas=(RelationshipDelta(character_id="alice", axis="trust", delta=50),),
    )
    with pytest.raises(ProposalRejected, match="relationship delta"):
        validate_action_resolution(pack, state, resolution)


def test_valid_decision_plan_and_draft_pass():
    state, pack = compiled_state()
    plan = valid_decision_plan()
    draft = valid_scene_draft(plan)
    assert validate_scene_plan(pack, state, plan) is plan
    assert validate_scene_draft(plan, draft) is draft


def test_action_resolution_rejects_duplicate_learned_fact_characters_and_ids():
    state, pack = compiled_state()
    resolution = ActionResolution(
        action_id="ask",
        outcome="success",
        learned_facts=(
            LearnedFactPlan(character_id="alice", fact_ids=("cafe_is_open", "cafe_is_open")),
            LearnedFactPlan(character_id="alice", fact_ids=("cafe_is_open",)),
        ),
    )
    with pytest.raises(ProposalRejected, match="learned fact"):
        validate_action_resolution(pack, state, resolution, expected_action_id="ask")
