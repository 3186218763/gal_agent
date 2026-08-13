from __future__ import annotations

from src.story.script_pack import RelationshipTurningPointSource
from src.story.state.events import (
    ActionResolved,
    CharacterDramaticStateChanged,
    CompletionEvaluated,
    ConsequenceBroken,
    ConsequenceRealized,
    ConsequenceScheduled,
    CostIncurred,
    EventEnvelope,
    ObligationCreated,
    ObligationResolved,
    PlayerActionSelected,
    RelationshipChanged,
    RelationshipEventRecorded,
    RelationshipTurningPointReached,
    StanceChallenged,
    StanceExpressed,
)
from src.story.state.models import FrozenModel


class ChoiceCausalTrace(FrozenModel):
    """Structured causality from one committed Choice Meaning forward.

    ``direct_consequence_event_ids`` are the accepted Story Consequence
    events anchored to the choice (action resolution and its immediate
    effects).  ``development_event_ids`` are later committed scenes and
    dramatic events that visibly carry the choice forward (relationship
    events and turning points, obligation resolutions, stance challenges,
    scheduled consequences).  ``ending_contribution_event_ids`` are the
    choice's own anchors that the ending's CompletionEvaluated cited —
    evidence the choice reached the Dynamic Ending's completion review.
    """

    choice_event_id: str
    decision_id: str
    action_id: str
    intent: str
    direct_consequence_event_ids: tuple[str, ...] = ()
    development_event_ids: tuple[str, ...] = ()
    ending_contribution_event_ids: tuple[str, ...] = ()
    reaches_ending: bool = False


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


def derive_causal_traces(
    event_trace: tuple[EventEnvelope, ...],
) -> tuple[ChoiceCausalTrace, ...]:
    """Derive a Causal Trace for every committed Player Choice.

    Causality is read strictly from committed structured history: direct
    anchors cite ``source_choice_event_id``, development events cite the
    choice's anchors, and ending relevance comes from the
    CompletionEvaluated citation chain.  Different wording or generated
    prose is never treated as impact (ADR 0011).
    """
    choices = tuple(
        envelope for envelope in event_trace if isinstance(envelope.event, PlayerActionSelected)
    )
    if not choices:
        return ()

    ending_cited: set[str] = set()
    for envelope in event_trace:
        if isinstance(envelope.event, CompletionEvaluated):
            for assessment in envelope.event.assessments:
                ending_cited.update(assessment.cited_event_ids)

    position = {envelope.event_id: index for index, envelope in enumerate(event_trace)}

    def ordered(event_ids: set[str]) -> tuple[str, ...]:
        return tuple(sorted(event_ids, key=lambda event_id: position.get(event_id, 0)))

    traces: list[ChoiceCausalTrace] = []
    for choice_envelope in choices:
        choice = choice_envelope.event
        choice_id = choice_envelope.event_id
        direct: set[str] = set()
        development: set[str] = set()

        # Direct anchors: consequences and effects that cite this choice.
        for envelope in event_trace:
            event = envelope.event
            if isinstance(event, ActionResolved) and event.source_choice_event_id == choice_id:
                direct.add(envelope.event_id)
            elif (
                isinstance(event, RelationshipChanged) and event.source_choice_event_id == choice_id
            ):
                direct.add(envelope.event_id)
                if event.relationship_event_id is not None:
                    development.add(event.relationship_event_id)
            elif isinstance(event, StanceExpressed) and event.source_choice_event_id == choice_id:
                direct.add(envelope.event_id)
            elif (
                isinstance(event, RelationshipEventRecorded)
                and event.source_choice_event_id == choice_id
            ):
                direct.add(envelope.event_id)
                if event.scene_event_id:
                    development.add(event.scene_event_id)
            elif isinstance(event, ObligationCreated) and event.source_choice_event_id == choice_id:
                direct.add(envelope.event_id)
            elif isinstance(event, CostIncurred) and event.source_choice_event_id == choice_id:
                direct.add(envelope.event_id)
                development.update(event.effect_event_ids)
            elif (
                isinstance(event, ConsequenceScheduled)
                and event.cause_event_id == choice_id
                or (
                    isinstance(event, CharacterDramaticStateChanged)
                    and event.source_event_id == choice_id
                )
            ):
                development.add(envelope.event_id)

        # Later dramatic development that visibly carries this choice forward.
        relationship_events = {
            envelope.event_id
            for envelope in event_trace
            if isinstance(envelope.event, RelationshipEventRecorded)
            and envelope.event.source_choice_event_id == choice_id
        }
        obligations = {
            envelope.event.obligation_id
            for envelope in event_trace
            if isinstance(envelope.event, ObligationCreated)
            and envelope.event.source_choice_event_id == choice_id
        }
        stance_keys = {
            envelope.event.key
            for envelope in event_trace
            if isinstance(envelope.event, StanceExpressed)
            and envelope.event.source_choice_event_id == choice_id
        }
        scheduled = {
            envelope.event.consequence_id
            for envelope in event_trace
            if isinstance(envelope.event, ConsequenceScheduled)
            and envelope.event.cause_event_id in (direct | {choice_id})
        }
        for envelope in event_trace:
            event = envelope.event
            if isinstance(event, RelationshipTurningPointReached):
                if set(event.relationship_event_ids) & relationship_events:
                    development.add(envelope.event_id)
            elif isinstance(event, ObligationResolved) and event.obligation_id in obligations:
                development.add(envelope.event_id)
                if event.resolution_scene_event_id:
                    development.add(event.resolution_scene_event_id)
            elif isinstance(event, StanceChallenged) and event.stance_key in stance_keys:
                development.add(envelope.event_id)
                if event.scene_event_id:
                    development.add(event.scene_event_id)
            elif isinstance(event, ConsequenceScheduled) and event.consequence_id in scheduled:
                development.add(envelope.event_id)
            elif isinstance(event, ConsequenceRealized) and event.consequence_id in scheduled:
                development.add(envelope.event_id)
                development.update(event.effect_event_ids)
            elif isinstance(event, ConsequenceBroken) and event.consequence_id in scheduled:
                development.add(envelope.event_id)
                development.update(event.evidence_event_ids)

        ending_contrib = ordered((direct | development | {choice_id}) & ending_cited)
        traces.append(
            ChoiceCausalTrace(
                choice_event_id=choice_id,
                decision_id=choice.decision_id,
                action_id=choice.action_id,
                intent=choice.intent,
                direct_consequence_event_ids=ordered(direct),
                development_event_ids=ordered(development - direct),
                ending_contribution_event_ids=ending_contrib,
                reaches_ending=bool(ending_contrib),
            )
        )
    return tuple(traces)
