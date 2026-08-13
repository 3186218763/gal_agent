from __future__ import annotations

from src.story.script_pack import RelationshipTurningPointSource
from src.story.state.events import (
    CostIncurred,
    EventEnvelope,
    ObligationCreated,
    PlayerActionSelected,
    RelationshipChanged,
    RelationshipEventRecorded,
    RelationshipTurningPointReached,
)


def derive_relationship_turning_points(
    definitions: tuple[RelationshipTurningPointSource, ...],
    event_trace: tuple[EventEnvelope, ...],
) -> tuple[RelationshipTurningPointReached, ...]:
    reached = {
        envelope.event.turning_point_id
        for envelope in event_trace
        if isinstance(envelope.event, RelationshipTurningPointReached)
    }
    relationship_events = tuple(
        envelope
        for envelope in event_trace
        if isinstance(envelope.event, RelationshipEventRecorded)
    )

    derived: list[RelationshipTurningPointReached] = []
    for definition in definitions:
        if definition.id in reached:
            continue
        matching = tuple(
            envelope
            for envelope in relationship_events
            if envelope.event.character_id == definition.character_id
            and envelope.event.tag in definition.all_of_event_tags
        )
        tags = {envelope.event.tag for envelope in matching}
        source_choices = {envelope.event.source_choice_event_id for envelope in matching}
        if not set(definition.all_of_event_tags).issubset(tags):
            continue
        if len(source_choices) < definition.min_distinct_source_choices:
            continue
        relationship_event_ids = tuple(dict.fromkeys(envelope.event_id for envelope in matching))
        derived.append(
            RelationshipTurningPointReached(
                turning_point_id=definition.id,
                character_id=definition.character_id,
                relationship_event_ids=relationship_event_ids,
            )
        )

    return tuple(derived)


def derive_cost_incurred(
    choice_event_id: str,
    choice: PlayerActionSelected,
    effect_event_id: str,
    effect: RelationshipChanged | ObligationCreated,
    relationship_event: EventEnvelope | None = None,
) -> CostIncurred | None:
    category = choice.accepted_cost_category
    if category is None:
        return None

    if isinstance(effect, RelationshipChanged):
        if (
            relationship_event is None
            or not isinstance(relationship_event.event, RelationshipEventRecorded)
            or effect.delta >= 0
        ):
            return None
        semantic_event = relationship_event.event
        if (
            effect.source_choice_event_id != choice_event_id
            or semantic_event.source_choice_event_id != choice_event_id
            or effect.character_id != semantic_event.character_id
            or effect.relationship_event_id != relationship_event.event_id
        ):
            return None
        severity = min(3, abs(effect.delta) // 5)
        if severity < 1:
            return None
        return CostIncurred(
            severity=severity,
            category=category,
            source_choice_event_id=choice_event_id,
            effect_event_ids=(effect_event_id, effect.relationship_event_id),
        )

    if (
        effect.source_choice_event_id != choice_event_id
        or choice.potential_obligation_kind != effect.kind
    ):
        return None
    return CostIncurred(
        severity=effect.burden,
        category=category,
        source_choice_event_id=choice_event_id,
        effect_event_ids=(effect_event_id,),
    )
