from src.story.script_pack import RelationshipTurningPointSource
from src.story.state import (
    CostIncurred,
    EventEnvelope,
    ObligationCreated,
    PlayerActionSelected,
    RelationshipChanged,
    RelationshipEventRecorded,
    RelationshipTurningPointReached,
    derive_cost_incurred,
    derive_relationship_turning_points,
)


def _envelope(event_id, sequence, event):
    return EventEnvelope(
        event_id=event_id,
        session_id="s1",
        sequence=sequence,
        event=event,
    )


def _definition():
    return RelationshipTurningPointSource(
        id="alice_mutual_trust",
        character_id="alice",
        all_of_event_tags=("public_trust", "accepted_truth"),
        min_distinct_source_choices=2,
    )


def _relationship_event(
    *,
    character_id="alice",
    tag="public_trust",
    source_choice_event_id="choice-1",
    scene_event_id="scene-1",
):
    return RelationshipEventRecorded(
        character_id=character_id,
        tag=tag,
        source_choice_event_id=source_choice_event_id,
        scene_event_id=scene_event_id,
    )


def _costly_choice(
    category="bob_trust",
    *,
    obligation_kind=None,
):
    return PlayerActionSelected(
        decision_id="d1",
        option_id="protect_alice",
        idempotency_key="k1",
        accepted_cost_category=category,
        potential_obligation_kind=obligation_kind,
    )


def test_turning_point_requires_all_tags_and_distinct_choices():
    trace = (
        _envelope("r1", 1, _relationship_event()),
        _envelope(
            "r2",
            2,
            _relationship_event(
                tag="accepted_truth",
                source_choice_event_id="choice-2",
                scene_event_id="scene-2",
            ),
        ),
    )

    events = derive_relationship_turning_points((_definition(),), trace)

    assert events == (
        RelationshipTurningPointReached(
            turning_point_id="alice_mutual_trust",
            character_id="alice",
            relationship_event_ids=("r1", "r2"),
        ),
    )


def test_turning_point_requires_distinct_source_choices():
    trace = (
        _envelope("r1", 1, _relationship_event()),
        _envelope(
            "r2",
            2,
            _relationship_event(tag="accepted_truth", scene_event_id="scene-2"),
        ),
    )

    assert derive_relationship_turning_points((_definition(),), trace) == ()


def test_turning_point_requires_every_declared_tag():
    trace = (_envelope("r1", 1, _relationship_event()),)

    assert derive_relationship_turning_points((_definition(),), trace) == ()


def test_turning_point_ignores_other_characters_relationship_events():
    trace = (
        _envelope("r1", 1, _relationship_event()),
        _envelope(
            "r2",
            2,
            _relationship_event(
                character_id="bob",
                tag="accepted_truth",
                source_choice_event_id="choice-2",
                scene_event_id="scene-2",
            ),
        ),
    )

    assert derive_relationship_turning_points((_definition(),), trace) == ()


def test_turning_point_is_not_derived_after_it_was_reached():
    trace = (
        _envelope("r1", 1, _relationship_event()),
        _envelope(
            "r2",
            2,
            _relationship_event(
                tag="accepted_truth",
                source_choice_event_id="choice-2",
                scene_event_id="scene-2",
            ),
        ),
        _envelope(
            "tp1",
            3,
            RelationshipTurningPointReached(
                turning_point_id="alice_mutual_trust",
                character_id="alice",
                relationship_event_ids=("r1", "r2"),
            ),
        ),
    )

    assert derive_relationship_turning_points((_definition(),), trace) == ()


def test_turning_point_evidence_preserves_history_order_with_extra_matches():
    trace = (
        _envelope(
            "r3",
            1,
            _relationship_event(
                tag="accepted_truth",
                source_choice_event_id="choice-2",
                scene_event_id="scene-2",
            ),
        ),
        _envelope("r1", 2, _relationship_event()),
        _envelope(
            "r2",
            3,
            _relationship_event(
                source_choice_event_id="choice-3",
                scene_event_id="scene-3",
            ),
        ),
    )

    events = derive_relationship_turning_points((_definition(),), trace)

    assert events[0].relationship_event_ids == ("r3", "r1", "r2")


def test_relationship_loss_derives_cost_for_same_choice_and_category():
    choice = _costly_choice()
    change = RelationshipChanged(
        character_id="bob",
        axis="trust",
        delta=-10,
        source_choice_event_id="choice-1",
        relationship_event_id="relationship-1",
    )
    semantic = _relationship_event(
        character_id="bob",
        tag="resented_public_choice",
        source_choice_event_id="choice-1",
    )

    cost = derive_cost_incurred(
        "choice-1",
        choice,
        "effect-1",
        change,
        _envelope("relationship-1", 2, semantic),
    )

    assert cost == CostIncurred(
        severity=2,
        category="bob_trust",
        source_choice_event_id="choice-1",
        effect_event_ids=("effect-1", "relationship-1"),
    )


def test_relationship_loss_severity_is_capped_at_three():
    change = RelationshipChanged(
        character_id="bob",
        axis="trust",
        delta=-100,
        source_choice_event_id="choice-1",
        relationship_event_id="relationship-1",
    )
    semantic = _relationship_event(
        character_id="bob",
        source_choice_event_id="choice-1",
    )

    cost = derive_cost_incurred(
        "choice-1",
        _costly_choice(),
        "effect-1",
        change,
        _envelope("relationship-1", 2, semantic),
    )

    assert cost is not None
    assert cost.severity == 3


def test_relationship_change_smaller_than_five_is_not_a_cost():
    change = RelationshipChanged(
        character_id="bob",
        axis="trust",
        delta=-4,
        source_choice_event_id="choice-1",
        relationship_event_id="relationship-1",
    )

    assert (
        derive_cost_incurred(
            "choice-1",
            _costly_choice(),
            "effect-1",
            change,
            _envelope(
                "relationship-1",
                2,
                _relationship_event(character_id="bob"),
            ),
        )
        is None
    )


def test_positive_relationship_change_is_not_a_cost():
    change = RelationshipChanged(
        character_id="bob",
        axis="trust",
        delta=10,
        source_choice_event_id="choice-1",
        relationship_event_id="relationship-1",
    )

    assert (
        derive_cost_incurred(
            "choice-1",
            _costly_choice(),
            "effect-1",
            change,
            _envelope(
                "relationship-1",
                2,
                _relationship_event(character_id="bob"),
            ),
        )
        is None
    )


def test_unrelated_relationship_loss_cannot_satisfy_choice_cost():
    change = RelationshipChanged(
        character_id="bob",
        axis="trust",
        delta=-10,
        source_choice_event_id="choice-2",
        relationship_event_id="relationship-1",
    )

    assert (
        derive_cost_incurred(
            "choice-1",
            _costly_choice(),
            "effect-1",
            change,
            _envelope(
                "relationship-1",
                2,
                _relationship_event(character_id="bob"),
            ),
        )
        is None
    )


def test_relationship_cost_requires_semantic_event_from_same_choice():
    change = RelationshipChanged(
        character_id="bob",
        axis="trust",
        delta=-10,
        source_choice_event_id="choice-1",
        relationship_event_id="relationship-1",
    )
    semantic = _relationship_event(
        character_id="bob",
        source_choice_event_id="choice-2",
    )

    assert (
        derive_cost_incurred(
            "choice-1",
            _costly_choice(),
            "effect-1",
            change,
            _envelope("relationship-1", 2, semantic),
        )
        is None
    )


def test_relationship_cost_requires_matching_semantic_event():
    change = RelationshipChanged(
        character_id="bob",
        axis="trust",
        delta=-10,
        source_choice_event_id="choice-1",
        relationship_event_id="relationship-1",
    )
    semantic = _relationship_event(character_id="alice")

    assert (
        derive_cost_incurred(
            "choice-1",
            _costly_choice(),
            "effect-1",
            change,
            _envelope("relationship-1", 2, semantic),
        )
        is None
    )


def test_relationship_cost_requires_the_linked_semantic_event_id():
    change = RelationshipChanged(
        character_id="bob",
        axis="trust",
        delta=-10,
        source_choice_event_id="choice-1",
        relationship_event_id="relationship-1",
    )
    semantic = _relationship_event(character_id="bob")

    assert (
        derive_cost_incurred(
            "choice-1",
            _costly_choice(),
            "effect-1",
            change,
            _envelope("relationship-2", 2, semantic),
        )
        is None
    )


def test_choice_without_accepted_cost_category_cannot_incur_cost():
    choice = PlayerActionSelected(
        decision_id="d1",
        option_id="protect_alice",
        idempotency_key="k1",
    )
    obligation = ObligationCreated(
        obligation_id="secret-1",
        kind="keep_secret",
        burden=2,
        source_choice_event_id="choice-1",
    )

    assert derive_cost_incurred("choice-1", choice, "obligation-1", obligation) is None


def test_obligation_cost_uses_pack_burden_not_model_severity():
    obligation = ObligationCreated(
        obligation_id="secret-1",
        kind="keep_secret",
        burden=3,
        source_choice_event_id="choice-1",
    )

    cost = derive_cost_incurred(
        "choice-1",
        _costly_choice("responsibility", obligation_kind="keep_secret"),
        "obligation-1",
        obligation,
    )

    assert cost == CostIncurred(
        severity=3,
        category="responsibility",
        source_choice_event_id="choice-1",
        effect_event_ids=("obligation-1",),
    )


def test_obligation_cost_requires_same_choice():
    obligation = ObligationCreated(
        obligation_id="secret-1",
        kind="keep_secret",
        burden=2,
        source_choice_event_id="choice-2",
    )

    assert (
        derive_cost_incurred(
            "choice-1",
            _costly_choice("responsibility", obligation_kind="keep_secret"),
            "obligation-1",
            obligation,
        )
        is None
    )


def test_obligation_cost_requires_declared_kind():
    obligation = ObligationCreated(
        obligation_id="secret-1",
        kind="keep_secret",
        burden=2,
        source_choice_event_id="choice-1",
    )

    assert (
        derive_cost_incurred(
            "choice-1",
            _costly_choice("responsibility", obligation_kind="explain_lie"),
            "obligation-1",
            obligation,
        )
        is None
    )
