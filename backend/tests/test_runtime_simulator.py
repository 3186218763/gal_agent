from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    GoalDelta,
    LearnedFactPlan,
    RelationshipDelta,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.simulator import (
    choice_selection_event,
    simulate_consequence,
    simulate_scene,
)
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
            WrittenChoice(option_id=item.option_id, label=item.intent[:80]) for item in plan.choices
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


def _select_and_resolve(state, pack, resolution, key):
    """Authoritative two-step flow: commit the choice, then simulate its
    consequence with the source choice event carried forward."""
    choice = state.pending_decision.choices[0]
    selection = choice_selection_event(state, choice, key)
    envelope = EventEnvelope(
        event_id=f"sel-{key}",
        session_id=state.session_id,
        sequence=state.revision + 1,
        event=selection,
    )
    selected_state = apply_event(state, envelope)
    consequence_events = simulate_consequence(pack, selected_state, resolution)
    return (selection, *consequence_events)


def test_resolution_effect_events_have_deterministic_order():
    state, pack = decision_state()
    resolution = ActionResolution(
        action_id=state.pending_decision.choices[0].action_id,
        outcome="success",
        relationship_deltas=(RelationshipDelta(character_id="alice", axis="trust", delta=3),),
        goal_deltas=(GoalDelta(goal_id="alice_find_ally", delta=0.1),),
        reveal_fact_ids=("cafe_is_open",),
        learned_facts=(LearnedFactPlan(character_id="alice", fact_ids=("cafe_is_open",)),),
    )
    events = _select_and_resolve(state, pack, resolution, "request-01")
    assert [event.type for event in events] == [
        "player_action_selected",
        "action_resolved",
        "relationship_changed",
        "relationship_event_recorded",
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
    events = _select_and_resolve(state, pack, resolution, "request-02")
    assert [event.type for event in events][-2:] == ["fact_evidenced", "fact_revealed"]


def meaningful_decision_state():
    """Decision state whose offered choice carries stance, risk, obligation."""
    state, pack = compiled_state()
    plan = ScenePlan(
        scene_id="scene_01",
        summary="Alice waits for the protagonist to choose.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="decision_01",
        choices=(
            ChoicePlan(
                option_id="ask",
                action_id="ask",
                intent="ask directly",
                stance_axis="trust",
                stance_value="trust",
                accepted_risk="keep_secret",
                potential_obligation_kind="keep_secret",
            ),
            ChoicePlan(option_id="observe", action_id="observe", intent="watch carefully"),
        ),
    )
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


def test_choice_meaning_commitments_are_emitted_deterministically():
    """Stance, obligation, and derived cost come from the committed Choice
    Meaning — the planner cannot veto them."""
    state, pack = meaningful_decision_state()
    choice = state.pending_decision.choices[0]
    selection = choice_selection_event(state, choice, "meaning-key")
    envelope = EventEnvelope(
        event_id="sel-meaning",
        session_id=state.session_id,
        sequence=state.revision + 1,
        event=selection,
    )
    selected_state = apply_event(state, envelope)
    pending = selected_state.pending_consequence
    resolution = ActionResolution(action_id=pending.action_id, outcome="success")
    events = simulate_consequence(pack, selected_state, resolution)
    types = [event.type for event in events]
    assert "stance_expressed" in types
    assert "obligation_created" in types
    assert "cost_incurred" in types

    stance = next(event for event in events if event.type == "stance_expressed")
    assert stance.key == "trust:trust"
    assert stance.relation == "established"
    assert stance.source_choice_event_id == pending.choice_event_id
    obligation = next(event for event in events if event.type == "obligation_created")
    assert obligation.kind == "keep_secret"
    assert obligation.obligation_id == f"obligation:{pending.choice_event_id}"
    cost = next(event for event in events if event.type == "cost_incurred")
    assert cost.category == "keep_secret"
    assert cost.severity == obligation.burden
    assert cost.source_choice_event_id == pending.choice_event_id
