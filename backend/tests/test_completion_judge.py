from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.segment_contracts import CompletionResult
from src.story.script_pack.models import CompletionEvidenceSource, CompletionRequirementSource
from src.story.state import (
    ActionResolved,
    CostIncurred,
    EventEnvelope,
    FactCommitted,
    FactRevealed,
    NarrativeBlock,
    ObligationCreated,
    ObligationResolved,
    PlayerActionSelected,
    RelationshipChanged,
    RelationshipEventRecorded,
    RelationshipTurningPointReached,
    SceneCommitted,
    SessionState,
    StanceChallenged,
    StanceExpressed,
    WorldSnapshot,
)


def _state():
    return SessionState(
        session_id="s1",
        pack_id="test_pack",
        pack_hash="abcd" * 16,
        session_seed=1,
        world=WorldSnapshot(
            location_id="cafe",
            time_label="opening",
            present_character_ids=("alice",),
            max_scenes=20,
            reserved_resolution_scenes=3,
        ),
        facts={},
        characters={},
    )


def _envelope(event_id, sequence, event):
    return EventEnvelope(
        event_id=event_id,
        session_id="s1",
        sequence=sequence,
        event=event,
    )


def _resequence(event_trace):
    return tuple(
        envelope.model_copy(update={"sequence": sequence})
        for sequence, envelope in enumerate(event_trace, start=1)
    )


def _replace_event(event_trace, event_id, event):
    return tuple(
        envelope.model_copy(update={"event": event}) if envelope.event_id == event_id else envelope
        for envelope in event_trace
    )


def _requirement(requirement_id="requirement", **evidence):
    return CompletionRequirementSource(
        id=requirement_id,
        description="Completion evidence.",
        **evidence,
    )


def _scene(scene_id="scene-1"):
    return SceneCommitted(
        scene_id=scene_id,
        location_id="cafe",
        present_character_ids=("alice",),
        blocks=(NarrativeBlock(kind="narration", text="The truth lands."),),
    )


def _fact_trace():
    return (
        _envelope(
            "fact-evidence",
            1,
            ActionResolved(action_id="observe", outcome="success"),
        ),
        _envelope(
            "fact-committed",
            2,
            FactCommitted(
                fact_id="notebook_holder",
                value="alice",
                evidence_event_ids=("fact-evidence",),
            ),
        ),
        _envelope("fact-revealed", 3, FactRevealed(fact_id="notebook_holder")),
    )


def _relationship_and_cost_trace(start=4):
    return (
        _envelope(
            "choice-1",
            start,
            PlayerActionSelected(
                decision_id="d1",
                option_id="protect_alice",
                idempotency_key="k1",
                accepted_cost_category="alice_trust",
            ),
        ),
        _envelope(
            "relationship-1",
            start + 1,
            RelationshipEventRecorded(
                character_id="alice",
                tag="public_trust",
                source_choice_event_id="choice-1",
                scene_event_id="scene-1",
            ),
        ),
        _envelope(
            "choice-2",
            start + 2,
            PlayerActionSelected(
                decision_id="d2",
                option_id="accept_truth",
                idempotency_key="k2",
            ),
        ),
        _envelope(
            "relationship-2",
            start + 3,
            RelationshipEventRecorded(
                character_id="alice",
                tag="accepted_truth",
                source_choice_event_id="choice-2",
                scene_event_id="scene-2",
            ),
        ),
        _envelope("scene-1", start + 4, _scene("scene-1")),
        _envelope("scene-2", start + 5, _scene("scene-2")),
        _envelope(
            "turning-point",
            start + 6,
            RelationshipTurningPointReached(
                turning_point_id="alice_mutual_trust",
                character_id="alice",
                relationship_event_ids=("relationship-1", "relationship-2"),
            ),
        ),
        _envelope(
            "effect-1",
            start + 7,
            RelationshipChanged(
                character_id="alice",
                axis="trust",
                delta=-5,
                source_choice_event_id="choice-1",
                relationship_event_id="relationship-1",
            ),
        ),
        _envelope(
            "cost-1",
            start + 8,
            CostIncurred(
                category="alice_trust",
                severity=1,
                source_choice_event_id="choice-1",
                effect_event_ids=("effect-1", "relationship-1"),
            ),
        ),
    )


def _obligation_trace():
    return (
        _envelope(
            "choice-1",
            1,
            PlayerActionSelected(
                decision_id="d1",
                option_id="keep_secret",
                idempotency_key="k1",
                accepted_cost_category="responsibility",
                potential_obligation_kind="keep_secret",
            ),
        ),
        _envelope(
            "obligation-created",
            2,
            ObligationCreated(
                obligation_id="secret-1",
                kind="keep_secret",
                burden=2,
                source_choice_event_id="choice-1",
            ),
        ),
        _envelope("scene-2", 3, _scene("scene-2")),
        _envelope(
            "obligation-resolved",
            4,
            ObligationResolved(
                obligation_id="secret-1",
                outcome="fulfilled",
                resolution_scene_event_id="scene-2",
            ),
        ),
    )


def _stance_trace(*, unrelated_cost=False, challenge_before_establishment=False):
    choice_for_cost = "choice-other" if unrelated_cost else "choice-2"
    category = "bob_trust" if unrelated_cost else "alice_trust"
    challenge_sequence = 1 if challenge_before_establishment else 4
    return (
        (
            _envelope(
                "challenge-1",
                challenge_sequence,
                StanceChallenged(
                    stance_key="trust_vs_evidence:trust",
                    scene_event_id="scene-challenge",
                ),
            )
            if challenge_before_establishment
            else _envelope(
                "choice-1",
                1,
                PlayerActionSelected(
                    decision_id="d1",
                    option_id="trust",
                    idempotency_key="k1",
                ),
            )
        ),
        _envelope(
            "stance-established",
            2,
            StanceExpressed(
                key="trust_vs_evidence:trust",
                axis="trust_vs_evidence",
                value="trust",
                relation="established",
                source_choice_event_id="choice-1",
            ),
        ),
        _envelope("scene-challenge", 3, _scene("scene-challenge")),
        _envelope(
            "challenge-1" if not challenge_before_establishment else "choice-1",
            4,
            (
                StanceChallenged(
                    stance_key="trust_vs_evidence:trust",
                    scene_event_id="scene-challenge",
                )
                if not challenge_before_establishment
                else PlayerActionSelected(
                    decision_id="d1",
                    option_id="trust",
                    idempotency_key="k1",
                )
            ),
        ),
        _envelope(
            "choice-2",
            5,
            PlayerActionSelected(
                decision_id="d2",
                option_id="defend_trust",
                idempotency_key="k2",
                accepted_cost_category="alice_trust",
            ),
        ),
        _envelope(
            "stance-reinforced",
            6,
            StanceExpressed(
                key="trust_vs_evidence:trust",
                axis="trust_vs_evidence",
                value="trust",
                relation="reinforced",
                source_choice_event_id="choice-2",
            ),
        ),
        _envelope(
            "relationship-2",
            7,
            RelationshipEventRecorded(
                character_id="alice",
                tag="hurt_by_choice",
                source_choice_event_id=choice_for_cost,
                scene_event_id="scene-cost",
            ),
        ),
        _envelope(
            "effect-2",
            8,
            RelationshipChanged(
                character_id="alice",
                axis="trust",
                delta=-5,
                source_choice_event_id=choice_for_cost,
                relationship_event_id="relationship-2",
            ),
        ),
        _envelope(
            "cost-2",
            9,
            CostIncurred(
                category=category,
                severity=1,
                source_choice_event_id=choice_for_cost,
                effect_event_ids=("effect-2", "relationship-2"),
            ),
        ),
    )


def test_judge_evaluates_recursive_requirements_from_complete_history():
    requirement = _requirement(
        "complete_arc",
        all=(
            CompletionEvidenceSource(fact_revealed={"fact_id": "notebook_holder"}),
            CompletionEvidenceSource(
                any=(
                    CompletionEvidenceSource(
                        relationship_turning_point={"turning_point_id": "alice_mutual_trust"}
                    ),
                    CompletionEvidenceSource(obligation_fulfilled={"min_burden": 2}),
                )
            ),
            CompletionEvidenceSource(cost_incurred={"min_severity": 1}),
        ),
    )
    trace = _fact_trace() + _relationship_and_cost_trace()

    result = CompletionJudge().evaluate((requirement,), _state(), trace)

    assert result.cleared is True
    assert result.assessments[0].cited_event_ids == (
        "fact-evidence",
        "fact-committed",
        "fact-revealed",
        "choice-1",
        "relationship-1",
        "relationship-2",
        "turning-point",
        "effect-1",
        "cost-1",
    )


def test_fact_revealed_requires_commit_and_reveal_in_trace():
    requirement = _requirement(fact_revealed={"fact_id": "notebook_holder"})

    result = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        _fact_trace()[-1:],
    )

    assert result.cleared is False


def test_fact_revealed_requires_nonempty_evidence_before_commit():
    requirement = _requirement(fact_revealed={"fact_id": "notebook_holder"})
    evidence, committed, revealed = _fact_trace()
    empty_commit = committed.model_copy(
        update={"event": committed.event.model_copy(update={"evidence_event_ids": ()})}
    )
    late_evidence = _resequence((committed, evidence, revealed))

    empty_result = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        (evidence, empty_commit, revealed),
    )
    late_result = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        late_evidence,
    )

    assert empty_result.cleared is False
    assert late_result.cleared is False


def test_relationship_turning_point_cites_constituent_relationship_events():
    requirement = _requirement(
        relationship_turning_point={"turning_point_id": "alice_mutual_trust"}
    )
    trace = _relationship_and_cost_trace()

    result = CompletionJudge().evaluate((requirement,), _state(), trace)

    assert result.cleared is True
    assert result.assessments[0].cited_event_ids == (
        "relationship-1",
        "relationship-2",
        "turning-point",
    )


def test_relationship_turning_point_requires_choice_and_scene_evidence():
    requirement = _requirement(
        relationship_turning_point={"turning_point_id": "alice_mutual_trust"}
    )
    trace = _relationship_and_cost_trace()
    missing_choice = tuple(envelope for envelope in trace if envelope.event_id != "choice-2")
    missing_scene = tuple(envelope for envelope in trace if envelope.event_id != "scene-2")

    assert CompletionJudge().evaluate((requirement,), _state(), trace).cleared is True
    assert CompletionJudge().evaluate((requirement,), _state(), missing_choice).cleared is False
    assert CompletionJudge().evaluate((requirement,), _state(), missing_scene).cleared is False


def test_obligation_fulfilled_requires_burden_and_resolution_scene():
    satisfied = _requirement(
        "fulfilled",
        obligation_fulfilled={"min_burden": 2},
    )
    too_heavy = _requirement(
        "too_heavy",
        obligation_fulfilled={"min_burden": 3},
    )

    result = CompletionJudge().evaluate(
        (satisfied, too_heavy),
        _state(),
        _obligation_trace(),
    )

    assert result.cleared is False
    assert result.assessments[0].satisfied is True
    assert result.assessments[0].cited_event_ids == (
        "obligation-created",
        "scene-2",
        "obligation-resolved",
    )
    assert result.assessments[1].satisfied is False


def test_obligation_fulfilled_rejects_missing_resolution_scene():
    requirement = _requirement(obligation_fulfilled={"min_burden": 2})
    trace = tuple(envelope for envelope in _obligation_trace() if envelope.event_id != "scene-2")

    result = CompletionJudge().evaluate((requirement,), _state(), trace)

    assert result.cleared is False


def test_obligation_fulfilled_requires_scene_after_creation():
    requirement = _requirement(obligation_fulfilled={"min_burden": 2})
    choice, creation, scene, resolution = _obligation_trace()
    trace = _resequence((choice, scene, creation, resolution))

    result = CompletionJudge().evaluate((requirement,), _state(), trace)

    assert result.cleared is False


def test_cost_incurred_validates_choice_category_and_effects():
    requirement = _requirement(cost_incurred={"min_severity": 1})
    valid_trace = _relationship_and_cost_trace()

    valid = CompletionJudge().evaluate((requirement,), _state(), valid_trace)
    wrong_category = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        valid_trace[:-1]
        + (
            _envelope(
                "cost-1",
                valid_trace[-1].sequence,
                valid_trace[-1].event.model_copy(update={"category": "bob_trust"}),
            ),
        ),
    )
    missing_effect = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        tuple(item for item in valid_trace if item.event_id != "effect-1"),
    )

    assert valid.cleared is True
    assert valid.assessments[0].cited_event_ids == (
        "choice-1",
        "relationship-1",
        "effect-1",
        "cost-1",
    )
    assert wrong_category.cleared is False
    assert missing_effect.cleared is False


def test_cost_incurred_rejects_missing_source_choice():
    requirement = _requirement(cost_incurred={"min_severity": 1})
    trace = tuple(
        envelope for envelope in _relationship_and_cost_trace() if envelope.event_id != "choice-1"
    )

    result = CompletionJudge().evaluate((requirement,), _state(), trace)

    assert result.cleared is False


def test_cost_incurred_rejects_choice_category_mismatch():
    requirement = _requirement(cost_incurred={"min_severity": 1})
    trace = _relationship_and_cost_trace()
    choice = trace[0].event.model_copy(update={"accepted_cost_category": "bob_trust"})

    result = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        _replace_event(trace, "choice-1", choice),
    )

    assert result.cleared is False


def test_cost_incurred_rejects_non_cost_effect_type():
    requirement = _requirement(cost_incurred={"min_severity": 1})
    trace = _replace_event(
        _relationship_and_cost_trace(),
        "effect-1",
        ActionResolved(action_id="observe", outcome="success"),
    )

    result = CompletionJudge().evaluate((requirement,), _state(), trace)

    assert result.cleared is False


def test_cost_incurred_rejects_relationship_effect_from_other_choice():
    requirement = _requirement(cost_incurred={"min_severity": 1})
    trace = _relationship_and_cost_trace()
    effect = trace[-2].event.model_copy(update={"source_choice_event_id": "choice-2"})

    result = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        _replace_event(trace, "effect-1", effect),
    )

    assert result.cleared is False


def test_cost_incurred_accepts_declared_obligation_effect():
    requirement = _requirement(cost_incurred={"min_severity": 2})
    choice, obligation, _, _ = _obligation_trace()
    cost = _envelope(
        "cost-1",
        3,
        CostIncurred(
            category="responsibility",
            severity=2,
            source_choice_event_id="choice-1",
            effect_event_ids=("obligation-created",),
        ),
    )

    result = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        _resequence((choice, obligation, cost)),
    )

    assert result.cleared is True
    assert result.assessments[0].cited_event_ids == (
        "choice-1",
        "obligation-created",
        "cost-1",
    )


def test_stance_defended_requires_ordered_challenge_and_same_choice_cost():
    requirement = _requirement(stance_defended={"min_challenges": 1, "min_cost_severity": 1})

    valid = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        _stance_trace(),
    )
    wrong_cost = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        _stance_trace(unrelated_cost=True),
    )
    wrong_order = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        _stance_trace(challenge_before_establishment=True),
    )

    assert valid.cleared is True
    assert set(valid.assessments[0].cited_event_ids) >= {
        "choice-1",
        "stance-established",
        "challenge-1",
        "choice-2",
        "stance-reinforced",
        "relationship-2",
        "effect-2",
        "cost-2",
    }
    assert wrong_cost.cleared is False
    assert wrong_order.cleared is False


def test_stance_defended_requires_reinforcing_choice_after_challenge():
    requirement = _requirement(stance_defended={"min_challenges": 1, "min_cost_severity": 1})
    trace = _stance_trace()
    by_id = {envelope.event_id: envelope for envelope in trace}
    reordered = _resequence(
        (
            by_id["choice-1"],
            by_id["stance-established"],
            by_id["scene-challenge"],
            by_id["choice-2"],
            by_id["challenge-1"],
            by_id["stance-reinforced"],
            by_id["relationship-2"],
            by_id["effect-2"],
            by_id["cost-2"],
        )
    )

    result = CompletionJudge().evaluate((requirement,), _state(), reordered)

    assert result.cleared is False


def test_stance_defended_rejects_valid_cost_from_another_choice():
    requirement = _requirement(stance_defended={"min_challenges": 1, "min_cost_severity": 1})
    base = _stance_trace()[:6]
    other_choice = _envelope(
        "choice-other",
        7,
        PlayerActionSelected(
            decision_id="d3",
            option_id="distance_from_alice",
            idempotency_key="k3",
            accepted_cost_category="alice_trust",
        ),
    )
    relationship = _stance_trace(unrelated_cost=True)[6].model_copy(
        update={
            "event": _stance_trace(unrelated_cost=True)[6].event.model_copy(
                update={"source_choice_event_id": "choice-other"}
            )
        }
    )
    effect = _stance_trace(unrelated_cost=True)[7].model_copy(
        update={
            "event": _stance_trace(unrelated_cost=True)[7].event.model_copy(
                update={"source_choice_event_id": "choice-other"}
            )
        }
    )
    cost = _stance_trace(unrelated_cost=True)[8].model_copy(
        update={
            "event": _stance_trace(unrelated_cost=True)[8].event.model_copy(
                update={
                    "source_choice_event_id": "choice-other",
                    "category": "alice_trust",
                }
            )
        }
    )

    result = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        _resequence(base + (other_choice, relationship, effect, cost)),
    )

    assert result.cleared is False


def test_any_selects_first_satisfied_branch_in_authored_order():
    requirement = _requirement(
        any=(
            CompletionEvidenceSource(cost_incurred={"min_severity": 1}),
            CompletionEvidenceSource(
                relationship_turning_point={"turning_point_id": "alice_mutual_trust"}
            ),
        )
    )

    result = CompletionJudge().evaluate(
        (requirement,),
        _state(),
        _relationship_and_cost_trace(),
    )

    assert result.cleared is True
    assert "cost-1" in result.assessments[0].cited_event_ids
    assert "turning-point" not in result.assessments[0].cited_event_ids


def test_judge_empty_requirements_not_cleared():
    result = CompletionJudge().evaluate((), _state(), ())

    assert isinstance(result, CompletionResult)
    assert result.cleared is False
    assert result.assessments == ()
