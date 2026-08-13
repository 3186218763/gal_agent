from collections.abc import Iterator

import pytest

from src.story.state import (
    DramaticQuestionSet,
    EventReferenceError,
    NarrativeBlock,
    PlayerActionSelected,
    ProposedEvent,
    RelationshipChanged,
    RelationshipEventRecorded,
    SceneCommitted,
    prepare_event_batch,
)


def _id_factory() -> Iterator[str]:
    yield from ("id-choice", "id-effect", "id-relationship", "id-scene")


def _proposals():
    return (
        ProposedEvent(
            local_ref="choice:selected",
            event=PlayerActionSelected(
                decision_id="d1",
                option_id="protect_alice",
                idempotency_key="k1",
                accepted_cost_category="alice_trust",
            ),
        ),
        ProposedEvent(
            local_ref="effect:relationship_1",
            event=RelationshipChanged(
                character_id="alice",
                axis="trust",
                delta=-5,
                source_choice_event_id="choice:selected",
                relationship_event_id="relationship:current",
            ),
        ),
        ProposedEvent(
            local_ref="relationship:current",
            event=RelationshipEventRecorded(
                character_id="alice",
                tag="hurt_by_distance",
                source_choice_event_id="choice:selected",
                scene_event_id="scene:current",
            ),
        ),
        ProposedEvent(
            local_ref="scene:current",
            event=SceneCommitted(
                scene_id="scene-1",
                location_id="cafe",
                present_character_ids=("alice",),
                blocks=(NarrativeBlock(kind="narration", text="Alice steps back."),),
            ),
        ),
    )


def test_prepare_event_batch_resolves_forward_and_backward_local_refs():
    ids = _id_factory()
    proposals = _proposals()

    envelopes = prepare_event_batch(
        "s1",
        4,
        proposals,
        event_id_factory=lambda: next(ids),
    )

    assert tuple(item.event_id for item in envelopes) == (
        "id-choice",
        "id-effect",
        "id-relationship",
        "id-scene",
    )
    assert tuple(item.sequence for item in envelopes) == (5, 6, 7, 8)
    assert envelopes[1].event.source_choice_event_id == "id-choice"
    assert envelopes[1].event.relationship_event_id == "id-relationship"
    assert envelopes[2].event.source_choice_event_id == "id-choice"
    assert envelopes[2].event.scene_event_id == "id-scene"


def test_prepare_event_batch_resolves_explicit_committed_event_id():
    proposal = ProposedEvent(
        local_ref="question:current",
        event=DramaticQuestionSet(
            key="trust_alice",
            text="Will the protagonist trust Alice?",
            source_event_id="committed-scene-id",
        ),
    )

    envelopes = prepare_event_batch(
        "s1",
        0,
        (proposal,),
        committed_event_ids={"committed-scene-id"},
        event_id_factory=lambda: "question-id",
    )

    assert envelopes[0].event.source_event_id == "committed-scene-id"


def test_prepare_event_batch_rejects_unknown_event_reference():
    proposal = ProposedEvent(
        local_ref="relationship:current",
        event=RelationshipEventRecorded(
            character_id="alice",
            tag="public_trust",
            source_choice_event_id="choice:missing",
            scene_event_id="committed-scene-id",
        ),
    )

    with pytest.raises(EventReferenceError, match="choice:missing"):
        prepare_event_batch(
            "s1",
            0,
            (proposal,),
            committed_event_ids={"committed-scene-id"},
        )


def test_prepare_event_batch_rejects_duplicate_local_refs():
    proposal = ProposedEvent(
        local_ref="choice:selected",
        event=PlayerActionSelected(
            decision_id="d1",
            option_id="a",
            idempotency_key="k1",
        ),
    )

    with pytest.raises(EventReferenceError, match="duplicate local reference"):
        prepare_event_batch("s1", 0, (proposal, proposal))


def test_prepare_event_batch_rejects_empty_batch():
    with pytest.raises(EventReferenceError, match="cannot be empty"):
        prepare_event_batch("s1", 0, ())


def test_prepare_event_batch_rejects_duplicate_preallocated_ids():
    with pytest.raises(EventReferenceError, match="duplicate IDs"):
        prepare_event_batch(
            "s1",
            0,
            _proposals()[:2],
            event_id_factory=lambda: "same-id",
        )


def test_prepare_event_batch_rejects_preallocated_id_collision_with_history():
    proposal = ProposedEvent(
        local_ref="question:current",
        event=DramaticQuestionSet(
            key="trust_alice",
            text="Will the protagonist trust Alice?",
            source_event_id="committed-scene-id",
        ),
    )

    with pytest.raises(EventReferenceError, match="collides"):
        prepare_event_batch(
            "s1",
            0,
            (proposal,),
            committed_event_ids={"committed-scene-id", "same-id"},
            event_id_factory=lambda: "same-id",
        )


def test_prepare_event_batch_does_not_replace_non_reference_text():
    proposal = ProposedEvent(
        local_ref="scene:current",
        event=SceneCommitted(
            scene_id="scene-1",
            location_id="cafe",
            present_character_ids=("alice",),
            blocks=(
                NarrativeBlock(
                    kind="narration",
                    text="The note literally says choice:selected.",
                ),
            ),
        ),
    )

    envelopes = prepare_event_batch(
        "s1",
        0,
        (proposal,),
        event_id_factory=lambda: "scene-id",
    )

    assert envelopes[0].event.blocks[0].text.endswith("choice:selected.")
