import pytest

from src.story.script_pack import compile_source
from src.story.state import (
    ArcPressureAdvanced,
    CharacterLearnedFact,
    CharacterRuntime,
    ConsequenceRealized,
    ConsequenceScheduled,
    CostIncurred,
    DramaticArcPhase,
    DramaticQuestionSet,
    EndingEntered,
    EndingRuntime,
    EventEnvelope,
    FactCommitted,
    FactRevealed,
    FactTruthStatus,
    FactVisibility,
    NarrativeBlock,
    ObligationCreated,
    ObligationResolved,
    PendingDecisionReference,
    PhaseAdvanced,
    PlayerActionSelected,
    PresentedChoice,
    PromiseChanged,
    PromiseOpened,
    PromiseStatus,
    RelationshipChanged,
    RelationshipEventRecorded,
    RelationshipTurningPointReached,
    SceneAcknowledged,
    SceneCommitted,
    SessionEnded,
    SessionState,
    SessionStatus,
    StanceChallenged,
    StanceExpressed,
    StoryPhase,
    WorldSnapshot,
    apply_event,
    apply_events,
    initial_session_state,
)
from src.story.state.events import (
    CompletionEvaluated,
    DecisionPresented,
    EndingGenerated,
)
from src.story.state.models import CompletionAssessmentRecord
from src.story.state.reducer import StateTransitionError
from tests.story_factories import minimal_script_pack_dict


def _state():
    return initial_session_state(
        compile_source(minimal_script_pack_dict()),
        "session_01",
        session_seed=42,
    )


def _envelope(state, event, offset=1):
    return EventEnvelope(
        session_id=state.session_id,
        sequence=state.revision + offset,
        event=event,
    )


def _narration(text: str = "Something happens.") -> NarrativeBlock:
    return NarrativeBlock(kind="narration", text=text)


def _decision_choices() -> tuple[PresentedChoice, PresentedChoice]:
    return (
        PresentedChoice(
            id="ask_alice",
            action_id="ask",
            label="Ask Alice",
            intent="ask directly",
        ),
        PresentedChoice(
            id="observe_alice",
            action_id="observe",
            label="Watch quietly",
            intent="observe",
        ),
    )


def _decision_state():
    state = _state()
    return apply_event(
        state,
        _envelope(
            state,
            SceneCommitted(
                scene_id="scene_01",
                terminal="decision",
                location_id="cafe",
                present_character_ids=("alice",),
                blocks=(_narration("Alice waits."),),
                decision_id="decision_01",
                choices=_decision_choices(),
            ),
        ),
    )


def _state_at_max_scenes_with_ending_entered():
    state = _state()
    max_scenes = state.world.max_scenes
    world = state.world.model_copy(update={"scene_count": max_scenes})
    state = state.model_copy(update={"world": world})
    return apply_event(
        state,
        _envelope(
            state,
            EndingEntered(
                ending=EndingRuntime(
                    ending_id="fallback_ending",
                    entered_at_revision=1,
                    required_payoffs=("Close the current conflict.",),
                    final_scene_budget=1,
                    title="Closing Time",
                    blocks=(_narration("The lights dim on a quiet night."),),
                )
            ),
        ),
    )


def test_fact_commit_evidence_and_reveal_are_separate():
    original = _state()
    committed = apply_event(
        original,
        _envelope(
            original,
            FactCommitted(
                fact_id="who_took_notebook",
                value="alice",
                evidence_event_ids=("evidence_01",),
            ),
        ),
    )

    fact = committed.facts["who_took_notebook"]
    assert fact.truth_status == FactTruthStatus.COMMITTED
    assert fact.visibility == FactVisibility.EVIDENCED
    assert original.facts["who_took_notebook"].value is None

    revealed = apply_event(
        committed,
        _envelope(committed, FactRevealed(fact_id="who_took_notebook")),
    )
    assert revealed.facts["who_took_notebook"].visibility == FactVisibility.REVEALED


def test_committed_fact_cannot_be_rewritten():
    state = _state()
    state = apply_event(
        state,
        _envelope(
            state,
            FactCommitted(
                fact_id="who_took_notebook",
                value="alice",
                evidence_event_ids=("evidence_01",),
            ),
        ),
    )

    with pytest.raises(StateTransitionError, match="already committed"):
        apply_event(
            state,
            _envelope(
                state,
                FactCommitted(
                    fact_id="who_took_notebook",
                    value="stranger",
                    evidence_event_ids=("evidence_02",),
                ),
            ),
        )


def test_character_learning_updates_both_knowledge_indexes():
    state = _state()
    state = apply_event(
        state,
        _envelope(
            state,
            FactCommitted(
                fact_id="who_took_notebook",
                value="alice",
                evidence_event_ids=("evidence_01",),
            ),
        ),
    )
    state = apply_event(
        state,
        _envelope(
            state,
            CharacterLearnedFact(
                character_id="alice",
                fact_id="who_took_notebook",
            ),
        ),
    )

    assert "who_took_notebook" in state.characters["alice"].knowledge
    assert "alice" in state.facts["who_took_notebook"].known_by


def test_event_batch_is_atomic_when_later_event_fails():
    original = _state()
    events = [
        _envelope(
            original,
            RelationshipChanged(character_id="alice", axis="trust", delta=5),
            offset=1,
        ),
        _envelope(original, FactRevealed(fact_id="who_took_notebook"), offset=2),
    ]

    with pytest.raises(StateTransitionError):
        apply_events(original, events)

    assert original.world.relationships["alice"]["trust"] == 35
    assert original.revision == 0


def test_scene_acknowledgement_requires_matching_pending_scene():
    original = _state()
    committed = apply_event(
        original,
        _envelope(
            original,
            SceneCommitted(
                scene_id="scene_01",
                terminal="continue",
                location_id="cafe",
                present_character_ids=("alice",),
                blocks=(_narration("The cafe is quiet."),),
            ),
        ),
    )
    assert committed.pending_scene is not None
    assert committed.pending_scene.scene_id == "scene_01"
    assert committed.world.scene_count == 1

    acknowledged = apply_event(
        committed,
        _envelope(committed, SceneAcknowledged(scene_id="scene_01")),
    )
    assert acknowledged.pending_scene is None


def test_decision_scene_persists_only_allowed_choices():
    state = _state()
    event = SceneCommitted(
        scene_id="scene_01",
        terminal="decision",
        location_id="cafe",
        present_character_ids=("alice",),
        blocks=(NarrativeBlock(kind="narration", text="Alice waits."),),
        decision_id="decision_01",
        choices=(
            PresentedChoice(
                id="ask_alice", action_id="ask", label="Ask Alice", intent="ask directly"
            ),
            PresentedChoice(
                id="observe_alice", action_id="observe", label="Watch quietly", intent="observe"
            ),
        ),
    )
    committed = apply_event(state, _envelope(state, event))
    assert committed.pending_scene.blocks[0].text == "Alice waits."
    assert [item.id for item in committed.pending_decision.choices] == [
        "ask_alice",
        "observe_alice",
    ]


def test_player_cannot_select_unpresented_choice():
    committed = _decision_state()
    with pytest.raises(StateTransitionError, match="not offered"):
        apply_event(
            committed,
            _envelope(
                committed,
                PlayerActionSelected(
                    decision_id="decision_01",
                    option_id="invented",
                    idempotency_key="request_01",
                ),
            ),
        )


def test_ending_scene_can_commit_at_normal_scene_limit():
    state = _state_at_max_scenes_with_ending_entered()
    committed = apply_event(
        state,
        _envelope(
            state,
            SceneCommitted(
                scene_id="ending_safe_exit",
                terminal="ending",
                location_id="cafe",
                present_character_ids=("alice",),
                blocks=(NarrativeBlock(kind="narration", text="The story closes."),),
            ),
        ),
    )
    assert committed.world.scene_count == state.world.scene_count


def test_phase_can_only_advance_one_step():
    state = _state()
    with pytest.raises(StateTransitionError, match="one step"):
        apply_event(
            state,
            _envelope(state, PhaseAdvanced(phase=StoryPhase.ESCALATION)),
        )


def test_decision_id_is_only_valid_for_decision_scene():
    state = _state()

    with pytest.raises(StateTransitionError, match="decision_id"):
        apply_event(
            state,
            _envelope(
                state,
                SceneCommitted(
                    scene_id="scene_01",
                    terminal="continue",
                    location_id="cafe",
                    present_character_ids=("alice",),
                    blocks=(_narration("The cafe is quiet."),),
                    decision_id="decision_01",
                ),
            ),
        )


def test_ending_entry_revision_must_match_event_sequence():
    state = _state()

    with pytest.raises(StateTransitionError, match="ending revision"):
        apply_event(
            state,
            _envelope(
                state,
                EndingEntered(
                    ending=EndingRuntime(
                        ending_id="fallback_ending",
                        entered_at_revision=2,
                        required_payoffs=("Close the current conflict.",),
                        final_scene_budget=1,
                        title="Closing Time",
                        blocks=(_narration("The lights dim on a quiet night."),),
                    )
                ),
            ),
        )


def test_ended_session_rejects_new_events():
    state = _state()
    state = apply_event(
        state,
        _envelope(
            state,
            EndingEntered(
                ending=EndingRuntime(
                    ending_id="fallback_ending",
                    entered_at_revision=1,
                    required_payoffs=("Close the current conflict.",),
                    final_scene_budget=1,
                    title="Closing Time",
                    blocks=(_narration("The lights dim on a quiet night."),),
                )
            ),
        ),
    )
    state = apply_event(
        state,
        _envelope(state, SessionEnded(ending_id="fallback_ending")),
    )

    with pytest.raises(StateTransitionError, match="ended session"):
        apply_event(
            state,
            _envelope(
                state,
                RelationshipChanged(character_id="alice", axis="trust", delta=1),
            ),
        )


def test_decision_presented_event_serialization():
    event = DecisionPresented(
        decision_id="dec_01",
        choices=(
            PresentedChoice(id="opt_a", action_id="ask", label="Ask", intent="Ask directly"),
            PresentedChoice(
                id="opt_b", action_id="observe", label="Watch", intent="Watch carefully"
            ),
        ),
    )
    assert event.type == "decision_presented"
    assert len(event.choices) == 2


def test_ending_generated_event_serialization():
    event = EndingGenerated(
        ending_id="ending_sess_001",
        title="The Long Goodbye",
        tone="bittersweet",
        terminal_state_summary="Alice left the city.",
        blocks=(NarrativeBlock(kind="narration", text="They parted."),),
    )
    assert event.type == "ending_generated"
    assert event.tone == "bittersweet"


def test_completion_evaluated_event_serialization():
    event = CompletionEvaluated(
        cleared=True,
        assessments=(
            CompletionAssessmentRecord(
                requirement_id="req_a",
                satisfied=True,
                rationale="Fact committed",
            ),
        ),
    )
    assert event.type == "completion_evaluated"
    assert event.cleared is True


def test_scene_committed_default_terminal_is_continue():
    event = SceneCommitted(
        scene_id="scene_01",
        location_id="cafe",
        present_character_ids=("alice",),
        blocks=(NarrativeBlock(kind="narration", text="A quiet day."),),
    )
    assert event.terminal == "continue"
    assert event.decision_id is None
    assert event.choices == ()


# ---------------------------------------------------------------------------
# Reducer tests for new segment-engine event types
# ---------------------------------------------------------------------------


def _make_minimal_state(revision=0, **overrides):
    world = WorldSnapshot(
        location_id="cafe",
        time_label="opening",
        present_character_ids=("alice",),
        max_scenes=20,
        reserved_resolution_scenes=3,
    )
    base = SessionState(
        session_id="s1",
        pack_id="test_pack",
        pack_hash="abcd" * 16,
        revision=revision,
        session_seed=1,
        world=world,
        facts={},
        characters={},
    )
    return base.model_copy(update=overrides)


def test_decision_presented_sets_pending_decision():
    state = _make_minimal_state(revision=4)
    event = DecisionPresented(
        decision_id="dec_01",
        choices=(
            PresentedChoice(id="opt_a", action_id="ask", label="Ask", intent="Ask directly"),
            PresentedChoice(
                id="opt_b", action_id="observe", label="Watch", intent="Watch carefully"
            ),
        ),
    )
    envelope = EventEnvelope(session_id="s1", sequence=5, event=event)
    result = apply_event(state, envelope)
    assert result.pending_decision is not None
    assert result.pending_decision.decision_id == "dec_01"
    assert len(result.pending_decision.choices) == 2


def test_decision_presented_rejects_duplicate():
    state = _make_minimal_state(
        revision=4,
        pending_decision=PendingDecisionReference(
            decision_id="old",
            scene_id="scene_0",
            revision=4,
            choices=(
                PresentedChoice(id="x", action_id="ask", label="X", intent="x"),
                PresentedChoice(id="y", action_id="ask", label="Y", intent="y"),
            ),
        ),
    )
    event = DecisionPresented(
        decision_id="dec_01",
        choices=(
            PresentedChoice(id="opt_a", action_id="ask", label="Ask", intent="Ask directly"),
            PresentedChoice(
                id="opt_b", action_id="observe", label="Watch", intent="Watch carefully"
            ),
        ),
    )
    envelope = EventEnvelope(session_id="s1", sequence=5, event=event)
    with pytest.raises(StateTransitionError, match="already pending"):
        apply_event(state, envelope)


def test_ending_generated_sets_ending_and_resolving():
    state = _make_minimal_state(revision=9)
    event = EndingGenerated(
        ending_id="ending_s1_10",
        title="The Long Goodbye",
        tone="bittersweet",
        terminal_state_summary="Alice left the city.",
        blocks=(NarrativeBlock(kind="narration", text="They parted."),),
    )
    envelope = EventEnvelope(session_id="s1", sequence=10, event=event)
    result = apply_event(state, envelope)
    assert result.status == SessionStatus.RESOLVING
    assert result.ending is not None
    assert result.ending.ending_id == "ending_s1_10"
    assert result.ending.tone == "bittersweet"
    assert result.world.phase == StoryPhase.RESOLUTION


def test_ending_generated_rejects_if_ending_exists():
    existing_ending = EndingRuntime(
        ending_id="old",
        entered_at_revision=5,
        title="Old",
        blocks=(NarrativeBlock(kind="narration", text="."),),
    )
    state = _make_minimal_state(revision=9, ending=existing_ending)
    event = EndingGenerated(
        ending_id="new",
        title="New",
        tone="sad",
        terminal_state_summary="Bye",
        blocks=(NarrativeBlock(kind="narration", text="."),),
    )
    envelope = EventEnvelope(session_id="s1", sequence=10, event=event)
    with pytest.raises(StateTransitionError, match="ending already"):
        apply_event(state, envelope)


def test_completion_evaluated_sets_completion():
    ending = EndingRuntime(
        ending_id="e1",
        entered_at_revision=10,
        title="End",
        blocks=(NarrativeBlock(kind="narration", text="."),),
    )
    state = _make_minimal_state(revision=10, ending=ending)
    event = CompletionEvaluated(
        cleared=True,
        assessments=(
            CompletionAssessmentRecord(
                requirement_id="req_a",
                satisfied=True,
                rationale="ok",
            ),
        ),
    )
    envelope = EventEnvelope(session_id="s1", sequence=11, event=event)
    result = apply_event(state, envelope)
    assert result.completion is not None
    assert result.completion.cleared is True
    assert len(result.completion.assessments) == 1


def test_completion_evaluated_rejects_without_ending():
    state = _make_minimal_state(revision=10)
    event = CompletionEvaluated(cleared=False, assessments=())
    envelope = EventEnvelope(session_id="s1", sequence=11, event=event)
    with pytest.raises(StateTransitionError, match="ending"):
        apply_event(state, envelope)


# ---------------------------------------------------------------------------
# Dramatic authority and semantic event replay
# ---------------------------------------------------------------------------


def test_relationship_event_updates_character_semantics():
    state = _make_minimal_state(characters={"alice": CharacterRuntime(character_id="alice")})
    event = RelationshipEventRecorded(
        character_id="alice",
        tag="public_trust",
        source_choice_event_id="choice-1",
        scene_event_id="scene-1",
    )

    result = apply_event(
        state,
        EventEnvelope(
            event_id="relationship-1",
            session_id="s1",
            sequence=1,
            event=event,
        ),
    )

    assert result.characters["alice"].relationship_event_ids == ("relationship-1",)
    assert state.characters["alice"].relationship_event_ids == ()


def test_stance_establishment_reinforcement_and_challenge_are_replayed():
    state = _make_minimal_state(characters={"bob": CharacterRuntime(character_id="bob")})
    state = apply_event(
        state,
        EventEnvelope(
            event_id="stance-1",
            session_id="s1",
            sequence=1,
            event=StanceExpressed(
                key="trust_vs_evidence:trust",
                axis="trust_vs_evidence",
                value="trust",
                relation="established",
                source_choice_event_id="choice-1",
            ),
        ),
    )
    state = apply_event(
        state,
        EventEnvelope(
            event_id="challenge-1",
            session_id="s1",
            sequence=2,
            event=StanceChallenged(
                stance_key="trust_vs_evidence:trust",
                scene_event_id="scene-2",
                challenging_character_id="bob",
            ),
        ),
    )
    state = apply_event(
        state,
        EventEnvelope(
            event_id="stance-2",
            session_id="s1",
            sequence=3,
            event=StanceExpressed(
                key="trust_vs_evidence:trust",
                axis="trust_vs_evidence",
                value="trust",
                relation="reinforced",
                source_choice_event_id="choice-2",
            ),
        ),
    )

    stance = state.drama.stances["trust_vs_evidence:trust"]
    assert stance.relation == "reinforced"
    assert stance.expression_event_ids == ("stance-1", "stance-2")
    assert stance.source_choice_event_ids == ("choice-1", "choice-2")
    assert stance.challenge_event_ids == ("challenge-1",)


def test_stance_update_must_match_existing_axis_and_value():
    state = apply_event(
        _make_minimal_state(),
        EventEnvelope(
            event_id="stance-1",
            session_id="s1",
            sequence=1,
            event=StanceExpressed(
                key="trust_vs_evidence:trust",
                axis="trust_vs_evidence",
                value="trust",
                relation="established",
                source_choice_event_id="choice-1",
            ),
        ),
    )

    with pytest.raises(StateTransitionError, match="axis and value"):
        apply_event(
            state,
            EventEnvelope(
                event_id="stance-2",
                session_id="s1",
                sequence=2,
                event=StanceExpressed(
                    key="trust_vs_evidence:trust",
                    axis="trust_vs_evidence",
                    value="evidence",
                    relation="contradicted",
                    source_choice_event_id="choice-2",
                ),
            ),
        )


def test_promise_lifecycle_records_payoff_evidence_and_rejects_terminal_changes():
    state = _make_minimal_state(characters={"alice": CharacterRuntime(character_id="alice")})
    state = apply_event(
        state,
        EventEnvelope(
            event_id="promise-opened-1",
            session_id="s1",
            sequence=1,
            event=PromiseOpened(
                promise_id="explain_lie",
                expectation="Alice will explain why she lied.",
                source_event_id="scene-1",
                involved_character_ids=("alice",),
                soft_deadline_decision=2,
                hard_deadline_decision=4,
            ),
        ),
    )
    state = apply_event(
        state,
        EventEnvelope(
            event_id="promise-changed-1",
            session_id="s1",
            sequence=2,
            event=PromiseChanged(
                promise_id="explain_lie",
                status=PromiseStatus.FULFILLED,
                payoff_event_ids=("scene-3",),
            ),
        ),
    )

    promise = state.drama.promises["explain_lie"]
    assert promise.status == PromiseStatus.FULFILLED
    assert promise.payoff_event_ids == ("scene-3",)

    with pytest.raises(StateTransitionError, match="terminal"):
        apply_event(
            state,
            EventEnvelope(
                event_id="promise-changed-2",
                session_id="s1",
                sequence=3,
                event=PromiseChanged(
                    promise_id="explain_lie",
                    status=PromiseStatus.BROKEN,
                    payoff_event_ids=("scene-4",),
                ),
            ),
        )


def test_obligation_cannot_be_resolved_twice():
    state = _make_minimal_state(characters={"alice": CharacterRuntime(character_id="alice")})
    state = apply_event(
        state,
        EventEnvelope(
            event_id="created-1",
            session_id="s1",
            sequence=1,
            event=ObligationCreated(
                obligation_id="secret-1",
                kind="keep_secret",
                burden=2,
                source_choice_event_id="choice-1",
                character_id="alice",
            ),
        ),
    )
    assert state.characters["alice"].unresolved_obligation_ids == frozenset({"secret-1"})

    state = apply_event(
        state,
        EventEnvelope(
            event_id="resolved-1",
            session_id="s1",
            sequence=2,
            event=ObligationResolved(
                obligation_id="secret-1",
                outcome="fulfilled",
                resolution_scene_event_id="scene-2",
            ),
        ),
    )
    assert state.characters["alice"].unresolved_obligation_ids == frozenset()

    with pytest.raises(StateTransitionError, match="already resolved"):
        apply_event(
            state,
            EventEnvelope(
                event_id="resolved-2",
                session_id="s1",
                sequence=3,
                event=ObligationResolved(
                    obligation_id="secret-1",
                    outcome="broken",
                    resolution_scene_event_id="scene-3",
                ),
            ),
        )


def test_scheduled_consequence_can_be_realized_once():
    state = apply_event(
        _make_minimal_state(),
        EventEnvelope(
            event_id="scheduled-1",
            session_id="s1",
            sequence=1,
            event=ConsequenceScheduled(
                consequence_id="alice_withdraws",
                cause_event_id="choice-1",
                required_effect="Alice withdraws after the public accusation.",
                due_after_decision=2,
                hard_deadline_decision=3,
            ),
        ),
    )
    state = apply_event(
        state,
        EventEnvelope(
            event_id="realized-1",
            session_id="s1",
            sequence=2,
            event=ConsequenceRealized(
                consequence_id="alice_withdraws",
                effect_event_ids=("relationship-1", "scene-2"),
            ),
        ),
    )

    consequence = state.drama.scheduled_consequences["alice_withdraws"]
    assert consequence.status == "realized"
    assert consequence.realization_event_id == "realized-1"

    with pytest.raises(StateTransitionError, match="already realized"):
        apply_event(
            state,
            EventEnvelope(
                event_id="realized-2",
                session_id="s1",
                sequence=3,
                event=ConsequenceRealized(
                    consequence_id="alice_withdraws",
                    effect_event_ids=("scene-3",),
                ),
            ),
        )


def test_relationship_turning_point_is_one_time_and_updates_character():
    state = _make_minimal_state(characters={"alice": CharacterRuntime(character_id="alice")})
    reached = RelationshipTurningPointReached(
        turning_point_id="alice_mutual_trust",
        character_id="alice",
        relationship_event_ids=("relationship-1", "relationship-2"),
    )
    state = apply_event(
        state,
        EventEnvelope(
            event_id="turning-point-1",
            session_id="s1",
            sequence=1,
            event=reached,
        ),
    )

    assert state.drama.reached_turning_point_ids == frozenset({"alice_mutual_trust"})
    assert state.characters["alice"].turning_point_ids == frozenset({"alice_mutual_trust"})

    with pytest.raises(StateTransitionError, match="already reached"):
        apply_event(
            state,
            EventEnvelope(
                event_id="turning-point-2",
                session_id="s1",
                sequence=2,
                event=reached,
            ),
        )


def test_cost_event_is_recorded_as_completion_evidence():
    state = apply_event(
        _make_minimal_state(),
        EventEnvelope(
            event_id="cost-1",
            session_id="s1",
            sequence=1,
            event=CostIncurred(
                category="loyalty",
                severity=2,
                source_choice_event_id="choice-1",
                effect_event_ids=("relationship-change-1", "relationship-event-1"),
            ),
        ),
    )

    assert state.drama.cost_event_ids == ("cost-1",)


def test_dramatic_question_replaces_previous_primary_question():
    state = apply_event(
        _make_minimal_state(),
        EventEnvelope(
            event_id="question-1",
            session_id="s1",
            sequence=1,
            event=DramaticQuestionSet(
                key="trust_alice",
                text="Will the protagonist trust Alice?",
                source_event_id="scene-1",
            ),
        ),
    )
    state = apply_event(
        state,
        EventEnvelope(
            event_id="question-2",
            session_id="s1",
            sequence=2,
            event=DramaticQuestionSet(
                key="expose_alice",
                text="Will the protagonist expose Alice's lie?",
                source_event_id="scene-2",
            ),
        ),
    )

    assert state.drama.primary_question.key == "expose_alice"
    assert state.drama.primary_question.source_event_id == "scene-2"


def test_arc_pressure_advances_monotonically():
    state = apply_event(
        _make_minimal_state(),
        EventEnvelope(
            event_id="arc-1",
            session_id="s1",
            sequence=1,
            event=ArcPressureAdvanced(phase=DramaticArcPhase.FRACTURE),
        ),
    )
    assert state.drama.arc_phase == DramaticArcPhase.FRACTURE

    with pytest.raises(StateTransitionError, match="advance exactly one step"):
        apply_event(
            state,
            EventEnvelope(
                event_id="arc-2",
                session_id="s1",
                sequence=2,
                event=ArcPressureAdvanced(phase=DramaticArcPhase.APPROACH),
            ),
        )


def test_player_action_selection_increments_dramatic_decision_count():
    state = _decision_state()
    result = apply_event(
        state,
        _envelope(
            state,
            PlayerActionSelected(
                decision_id="decision_01",
                option_id="ask_alice",
                idempotency_key="request_01",
                stance_axis="trust_vs_evidence",
                stance_value="trust",
                accepted_cost_category="loyalty",
                potential_obligation_kind="keep_secret",
                conflict_axis_id="trust_vs_evidence",
            ),
        ),
    )

    assert result.drama.decision_count == 1
