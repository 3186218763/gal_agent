import pytest

from src.story.script_pack import compile_source
from src.story.state import (
    CharacterLearnedFact,
    EndingEntered,
    EndingRuntime,
    EventEnvelope,
    FactCommitted,
    FactRevealed,
    FactTruthStatus,
    FactVisibility,
    PhaseAdvanced,
    RelationshipChanged,
    SceneAcknowledged,
    SceneCommitted,
    SessionEnded,
    StoryPhase,
    apply_event,
    apply_events,
    initial_session_state,
)
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
