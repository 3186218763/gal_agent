"""Causal Trace derivation tests (ADR 0011).

A Player Choice counts as influential only when its committed Choice Meaning
is referenced by an accepted Story Consequence and visibly carried into
later state or dramatic development; important choices are available to the
Dynamic Ending review.  Different wording or nondeterministic output alone
is never evidence of impact.
"""

from __future__ import annotations

from src.story.state import (
    ActionResolved,
    CompletionAssessmentRecord,
    CompletionEvaluated,
    ConsequenceRealized,
    ConsequenceScheduled,
    CostIncurred,
    EventEnvelope,
    NarrativeBlock,
    ObligationCreated,
    ObligationResolved,
    PlayerActionSelected,
    RelationshipChanged,
    RelationshipEventRecorded,
    RelationshipTurningPointReached,
    SceneCommitted,
    StanceChallenged,
    StanceExpressed,
    derive_causal_traces,
)


def envelope(event_id: str, sequence: int, event) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        session_id="s1",
        sequence=sequence,
        event=event,
    )


def choice(sequence: int = 1, event_id: str = "c1") -> EventEnvelope:
    return envelope(
        event_id,
        sequence,
        PlayerActionSelected(
            decision_id="d1",
            option_id="o1",
            action_id="ask",
            intent="ask directly",
            idempotency_key="k1",
        ),
    )


def scene(sequence: int, event_id: str) -> EventEnvelope:
    return envelope(
        event_id,
        sequence,
        SceneCommitted(
            scene_id=f"scene_{event_id}",
            terminal="continue",
            location_id="cafe",
            present_character_ids=("alice",),
            blocks=(NarrativeBlock(kind="narration", text="The cafe hums."),),
        ),
    )


def completion(cited_event_ids: tuple[str, ...], sequence: int = 99) -> EventEnvelope:
    return envelope(
        "completion-1",
        sequence,
        CompletionEvaluated(
            cleared=True,
            assessments=(
                CompletionAssessmentRecord(
                    requirement_id="req-1",
                    satisfied=True,
                    cited_event_ids=cited_event_ids,
                    rationale="evidence cited",
                ),
            ),
        ),
    )


def test_no_committed_choices_yields_no_traces():
    trace = (scene(1, "a"), scene(2, "b"))
    assert derive_causal_traces(trace) == ()


def test_direct_consequence_chain_is_derived_from_committed_history():
    trace = (
        choice(),
        envelope(
            "r1", 2, ActionResolved(source_choice_event_id="c1", action_id="ask", outcome="success")
        ),
        envelope(
            "s1",
            3,
            StanceExpressed(
                key="trust:trust",
                axis="trust",
                value="trust",
                relation="established",
                source_choice_event_id="c1",
            ),
        ),
    )
    (trace_result,) = derive_causal_traces(trace)
    assert trace_result.choice_event_id == "c1"
    assert trace_result.direct_consequence_event_ids == ("r1", "s1")
    assert trace_result.development_event_ids == ()
    assert not trace_result.reaches_ending


def test_choice_without_accepted_consequence_has_no_impact():
    """A committed choice whose consequence never anchored has an empty
    trace — wording alone is not impact."""
    trace = (
        choice(),
        scene(2, "later"),
        completion(("later",)),
    )
    (trace_result,) = derive_causal_traces(trace)
    assert trace_result.direct_consequence_event_ids == ()
    assert trace_result.development_event_ids == ()
    assert not trace_result.reaches_ending


def test_development_chain_and_ending_relevance():
    trace = (
        choice(),
        envelope(
            "r1", 2, ActionResolved(source_choice_event_id="c1", action_id="ask", outcome="success")
        ),
        envelope(
            "s1",
            3,
            StanceExpressed(
                key="trust:trust",
                axis="trust",
                value="trust",
                relation="established",
                source_choice_event_id="c1",
            ),
        ),
        scene(4, "sc1"),
        envelope(
            "re1",
            5,
            RelationshipEventRecorded(
                character_id="alice",
                tag="relationship_changed_trust",
                source_choice_event_id="c1",
                scene_event_id="sc1",
            ),
        ),
        envelope(
            "tp1",
            6,
            RelationshipTurningPointReached(
                turning_point_id="alice_trust",
                character_id="alice",
                relationship_event_ids=("re1",),
            ),
        ),
        completion(("re1", "tp1"), sequence=7),
    )
    (trace_result,) = derive_causal_traces(trace)

    assert trace_result.direct_consequence_event_ids == ("r1", "s1", "re1")
    # The relationship event is carried into the scene and the turning point.
    assert trace_result.development_event_ids == ("sc1", "tp1")
    # Ending relevance comes from the CompletionEvaluated citation chain.
    assert trace_result.ending_contribution_event_ids == ("re1", "tp1")
    assert trace_result.reaches_ending


def test_obligation_cost_and_consequence_development():
    trace = (
        choice(),
        envelope(
            "r1", 2, ActionResolved(source_choice_event_id="c1", action_id="ask", outcome="success")
        ),
        envelope(
            "ob1",
            3,
            ObligationCreated(
                obligation_id="obligation:c1",
                kind="keep_secret",
                burden=2,
                source_choice_event_id="c1",
            ),
        ),
        envelope(
            "cost1",
            4,
            CostIncurred(
                category="keep_secret",
                severity=2,
                source_choice_event_id="c1",
                effect_event_ids=("ob1",),
            ),
        ),
        envelope(
            "cs1",
            5,
            ConsequenceScheduled(
                consequence_id="cons-1",
                cause_event_id="ob1",
                required_effect="the secret surfaces",
                due_after_decision=2,
                hard_deadline_decision=3,
            ),
        ),
        scene(6, "sc1"),
        envelope(
            "obr1",
            7,
            ObligationResolved(
                obligation_id="obligation:c1",
                outcome="fulfilled",
                resolution_scene_event_id="sc1",
            ),
        ),
        envelope(
            "cr1",
            8,
            ConsequenceRealized(consequence_id="cons-1", effect_event_ids=("sc1",)),
        ),
        completion(("obr1", "sc1"), sequence=9),
    )
    (trace_result,) = derive_causal_traces(trace)

    assert trace_result.direct_consequence_event_ids == ("r1", "ob1", "cost1")
    # The scheduled consequence, its realization, the obligation resolution,
    # and the scene it resolved in all carry the choice forward.
    assert "cs1" in trace_result.development_event_ids
    assert "cr1" in trace_result.development_event_ids
    assert "obr1" in trace_result.development_event_ids
    assert "sc1" in trace_result.development_event_ids
    assert trace_result.reaches_ending


def test_ending_relevance_requires_citations_not_prose():
    """A later block that merely *talks about* the choice is not impact;
    only cited committed events count."""
    trace = (
        choice(),
        envelope(
            "r1", 2, ActionResolved(source_choice_event_id="c1", action_id="ask", outcome="success")
        ),
        scene(3, "sc1"),
        completion(("sc1",), sequence=4),
    )
    (trace_result,) = derive_causal_traces(trace)
    assert trace_result.direct_consequence_event_ids == ("r1",)
    # sc1 is cited but is not one of the choice's own anchors — the scene
    # never references the choice's committed events.
    assert trace_result.ending_contribution_event_ids == ()
    assert not trace_result.reaches_ending


def test_relationship_cost_and_stance_challenge_development():
    trace = (
        choice(),
        envelope(
            "r1", 2, ActionResolved(source_choice_event_id="c1", action_id="ask", outcome="success")
        ),
        envelope(
            "rc1",
            3,
            RelationshipChanged(
                character_id="alice",
                axis="trust",
                delta=-8,
                source_choice_event_id="c1",
                relationship_event_id="re1",
            ),
        ),
        envelope(
            "re1",
            4,
            RelationshipEventRecorded(
                character_id="alice",
                tag="relationship_changed_trust",
                source_choice_event_id="c1",
                scene_event_id="sc1",
            ),
        ),
        envelope(
            "st1",
            5,
            StanceExpressed(
                key="trust:trust",
                axis="trust",
                value="trust",
                relation="established",
                source_choice_event_id="c1",
            ),
        ),
        envelope(
            "cost1",
            6,
            CostIncurred(
                category="honesty",
                severity=1,
                source_choice_event_id="c1",
                effect_event_ids=("rc1", "re1"),
            ),
        ),
        scene(7, "sc1"),
        envelope(
            "ch1",
            8,
            StanceChallenged(
                stance_key="trust:trust",
                scene_event_id="sc1",
                challenging_character_id="bob",
            ),
        ),
    )
    (trace_result,) = derive_causal_traces(trace)

    assert trace_result.direct_consequence_event_ids == ("r1", "rc1", "re1", "st1", "cost1")
    assert "sc1" in trace_result.development_event_ids
    assert "ch1" in trace_result.development_event_ids
