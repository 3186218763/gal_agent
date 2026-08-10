from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    GoalDelta,
    RelationshipDelta,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.simulator import simulate_resolution, simulate_scene
from src.story.runtime.validator import validate_action_resolution
from src.story.script_pack import compile_source
from src.story.state import (
    EventEnvelope,
    FactTruthStatus,
    FactVisibility,
    NarrativeBlock,
    SceneCommitted,
    apply_event,
    initial_session_state,
)
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
            WrittenChoice(option_id=item.option_id, label=item.intent[:80])
            for item in plan.choices
        ),
    )


def decision_state():
    state, pack = compiled_state()
    plan = valid_decision_plan()
    draft = valid_scene_draft(plan)
    events = simulate_scene(pack, state, plan, draft)
    envelopes = tuple(
        EventEnvelope(
            event_id=f"setup-{index}",
            session_id=state.session_id,
            sequence=state.revision + index,
            event=event,
        )
        for index, event in enumerate(events, start=1)
    )
    next_state = state
    for envelope in envelopes:
        next_state = apply_event(next_state, envelope)
    return next_state, pack


def committed_fact_decision_state(
    fact_id: str = "who_took_notebook",
    evidence_event_ids: tuple[str, ...] = ("scene:evidence:1",),
    evidence_required: int = 2,
):
    state, pack = decision_state()
    fact = state.facts[fact_id].model_copy(
        update={
            "truth_status": FactTruthStatus.COMMITTED,
            "value": "alice",
            "visibility": (
                FactVisibility.EVIDENCED if evidence_event_ids else FactVisibility.HIDDEN
            ),
            "evidence_event_ids": evidence_event_ids,
            "evidence_required": evidence_required,
        }
    )
    facts = dict(state.facts)
    facts[fact_id] = fact
    return state.model_copy(update={"facts": facts}), pack


def test_scene_simulation_applies_complete_batch_without_writing_store():
    state, pack = compiled_state()
    plan = valid_decision_plan()
    draft = valid_scene_draft(plan)
    events = simulate_scene(pack, state, plan, draft)
    assert isinstance(events[-1], SceneCommitted)
    assert [item.id for item in events[-1].choices] == [item.option_id for item in plan.choices]
    assert state.revision == 0


def test_resolution_effect_events_have_deterministic_order():
    state, _ = decision_state()
    choice = state.pending_decision.choices[0]
    resolution = ActionResolution(
        action_id=choice.action_id,
        outcome="success",
        relationship_deltas=(RelationshipDelta(character_id="alice", axis="trust", delta=3),),
        goal_deltas=(GoalDelta(goal_id="alice_find_ally", delta=0.1),),
        reveal_fact_ids=("cafe_is_open",),
        learned_facts={"alice": ("cafe_is_open",)},
    )
    events = simulate_resolution(state, choice, resolution, "request-01")
    assert [event.type for event in events] == [
        "player_action_selected",
        "action_resolved",
        "relationship_changed",
        "goal_advanced",
        "fact_revealed",
        "character_learned_fact",
    ]


def test_resolution_can_add_final_evidence_then_reveal():
    state, pack = committed_fact_decision_state(
        fact_id="who_took_notebook",
        evidence_event_ids=("scene:evidence:1",),
        evidence_required=2,
    )
    choice = state.pending_decision.choices[0]
    resolution = ActionResolution(
        action_id=choice.action_id,
        outcome="success",
        evidence_fact_ids=("who_took_notebook",),
        reveal_fact_ids=("who_took_notebook",),
    )
    validate_action_resolution(pack, state, resolution, expected_action_id=choice.action_id)
    events = simulate_resolution(state, choice, resolution, "request-02")
    assert [event.type for event in events][-2:] == ["fact_evidenced", "fact_revealed"]
