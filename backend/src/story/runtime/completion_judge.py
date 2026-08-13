"""Deterministic completion evaluation over committed semantic evidence."""

from __future__ import annotations

from dataclasses import dataclass

from src.story.runtime.segment_contracts import CompletionAssessment, CompletionResult
from src.story.script_pack.models import CompletionEvidenceSource, CompletionRequirementSource
from src.story.state import (
    CostIncurred,
    EventEnvelope,
    FactCommitted,
    FactRevealed,
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
    derive_cost_incurred,
)


@dataclass(frozen=True)
class _EvidenceResult:
    satisfied: bool
    cited_event_ids: tuple[str, ...] = ()
    rationale: str = ""


class CompletionJudge:
    """Evaluate authored evidence expressions without model interpretation."""

    def evaluate(
        self,
        requirements: tuple[CompletionRequirementSource, ...],
        final_state: SessionState,
        event_trace: tuple[EventEnvelope, ...],
    ) -> CompletionResult:
        assessments = tuple(
            self._assess(requirement, final_state, event_trace) for requirement in requirements
        )
        return CompletionResult(
            assessments=assessments,
            cleared=(
                all(assessment.satisfied for assessment in assessments) if assessments else False
            ),
        )

    def _assess(
        self,
        requirement: CompletionRequirementSource,
        final_state: SessionState,
        event_trace: tuple[EventEnvelope, ...],
    ) -> CompletionAssessment:
        result = _evaluate_node(requirement, final_state, event_trace)
        return CompletionAssessment(
            requirement_id=requirement.id,
            satisfied=result.satisfied,
            cited_event_ids=result.cited_event_ids,
            rationale=result.rationale,
        )


def _evaluate_node(
    node: CompletionEvidenceSource,
    final_state: SessionState,
    event_trace: tuple[EventEnvelope, ...],
) -> _EvidenceResult:
    if node.all is not None:
        children = tuple(_evaluate_node(child, final_state, event_trace) for child in node.all)
        return _combine_all(children, event_trace)
    if node.any is not None:
        children = tuple(_evaluate_node(child, final_state, event_trace) for child in node.any)
        return _combine_any(children)
    if node.fact_revealed is not None:
        return _fact_revealed(node.fact_revealed.fact_id, event_trace)
    if node.relationship_turning_point is not None:
        return _turning_point(
            node.relationship_turning_point.turning_point_id,
            event_trace,
        )
    if node.obligation_fulfilled is not None:
        return _obligation_fulfilled(
            node.obligation_fulfilled.min_burden,
            event_trace,
        )
    if node.cost_incurred is not None:
        return _cost_incurred(node.cost_incurred.min_severity, event_trace)
    assert node.stance_defended is not None
    return _stance_defended(
        node.stance_defended.min_challenges,
        node.stance_defended.min_cost_severity,
        event_trace,
    )


def _event_indexes(
    event_trace: tuple[EventEnvelope, ...],
) -> tuple[dict[str, EventEnvelope], dict[str, int], set[str]]:
    by_id: dict[str, EventEnvelope] = {}
    positions: dict[str, int] = {}
    duplicates: set[str] = set()
    for position, envelope in enumerate(event_trace):
        if envelope.event_id in by_id:
            duplicates.add(envelope.event_id)
            continue
        by_id[envelope.event_id] = envelope
        positions[envelope.event_id] = position
    return by_id, positions, duplicates


def _fact_revealed(
    fact_id: str,
    event_trace: tuple[EventEnvelope, ...],
) -> _EvidenceResult:
    by_id, positions, duplicates = _event_indexes(event_trace)
    for revealed in event_trace:
        if not isinstance(revealed.event, FactRevealed) or revealed.event.fact_id != fact_id:
            continue
        commits = tuple(
            envelope
            for envelope in event_trace
            if isinstance(envelope.event, FactCommitted)
            and envelope.event.fact_id == fact_id
            and positions.get(envelope.event_id, len(event_trace))
            < positions.get(revealed.event_id, -1)
        )
        for committed in reversed(commits):
            evidence_ids = committed.event.evidence_event_ids
            if (
                committed.event_id in duplicates
                or not evidence_ids
                or any(
                    event_id in duplicates
                    or event_id not in by_id
                    or positions[event_id] >= positions[committed.event_id]
                    for event_id in evidence_ids
                )
            ):
                continue
            cited = {revealed.event_id, committed.event_id, *evidence_ids}
            return _EvidenceResult(
                satisfied=True,
                cited_event_ids=_ordered_citations(cited, event_trace),
                rationale=f"fact {fact_id} was committed and revealed",
            )
    return _EvidenceResult(
        satisfied=False,
        rationale=f"fact {fact_id} lacks committed reveal evidence",
    )


def _turning_point(
    turning_point_id: str,
    event_trace: tuple[EventEnvelope, ...],
) -> _EvidenceResult:
    by_id, positions, duplicates = _event_indexes(event_trace)
    for envelope in event_trace:
        event = envelope.event
        if not isinstance(event, RelationshipTurningPointReached):
            continue
        if event.turning_point_id != turning_point_id or envelope.event_id in duplicates:
            continue
        if len(event.relationship_event_ids) != len(set(event.relationship_event_ids)):
            continue
        constituent_events = tuple(by_id.get(event_id) for event_id in event.relationship_event_ids)
        if any(item is None for item in constituent_events):
            continue
        if any(
            item.event_id in duplicates
            or not isinstance(item.event, RelationshipEventRecorded)
            or item.event.character_id != event.character_id
            or positions[item.event_id] >= positions[envelope.event_id]
            or not _relationship_event_is_grounded(
                item,
                by_id,
                positions,
                duplicates,
            )
            for item in constituent_events
            if item is not None
        ):
            continue
        cited = {envelope.event_id, *event.relationship_event_ids}
        return _EvidenceResult(
            satisfied=True,
            cited_event_ids=_ordered_citations(cited, event_trace),
            rationale=f"relationship turning point {turning_point_id} was reached",
        )
    return _EvidenceResult(
        satisfied=False,
        rationale=f"no qualifying relationship turning point {turning_point_id}",
    )


def _relationship_event_is_grounded(
    relationship_envelope: EventEnvelope,
    by_id: dict[str, EventEnvelope],
    positions: dict[str, int],
    duplicates: set[str],
) -> bool:
    event = relationship_envelope.event
    assert isinstance(event, RelationshipEventRecorded)
    choice = by_id.get(event.source_choice_event_id)
    scene = by_id.get(event.scene_event_id)
    return (
        choice is not None
        and choice.event_id not in duplicates
        and isinstance(choice.event, PlayerActionSelected)
        and positions[choice.event_id] < positions[relationship_envelope.event_id]
        and scene is not None
        and scene.event_id not in duplicates
        and isinstance(scene.event, SceneCommitted)
    )


def _obligation_fulfilled(
    min_burden: int,
    event_trace: tuple[EventEnvelope, ...],
) -> _EvidenceResult:
    by_id, positions, duplicates = _event_indexes(event_trace)
    for resolution in event_trace:
        event = resolution.event
        if (
            not isinstance(event, ObligationResolved)
            or event.outcome != "fulfilled"
            or resolution.event_id in duplicates
        ):
            continue
        creations = tuple(
            envelope
            for envelope in event_trace
            if isinstance(envelope.event, ObligationCreated)
            and envelope.event.obligation_id == event.obligation_id
            and envelope.event.burden >= min_burden
            and positions[envelope.event_id] < positions[resolution.event_id]
        )
        for creation in reversed(creations):
            choice = by_id.get(creation.event.source_choice_event_id)
            scene = by_id.get(event.resolution_scene_event_id)
            if (
                creation.event_id in duplicates
                or choice is None
                or choice.event_id in duplicates
                or not isinstance(choice.event, PlayerActionSelected)
                or choice.event.potential_obligation_kind != creation.event.kind
                or positions[choice.event_id] >= positions[creation.event_id]
                or scene is None
                or scene.event_id in duplicates
                or not isinstance(scene.event, SceneCommitted)
                or positions[creation.event_id] >= positions[scene.event_id]
                or positions[scene.event_id] >= positions[resolution.event_id]
            ):
                continue
            cited = {creation.event_id, scene.event_id, resolution.event_id}
            return _EvidenceResult(
                satisfied=True,
                cited_event_ids=_ordered_citations(cited, event_trace),
                rationale=(
                    f"obligation {event.obligation_id} was fulfilled "
                    f"with burden {creation.event.burden}"
                ),
            )
    return _EvidenceResult(
        satisfied=False,
        rationale=f"no qualifying fulfilled obligation with burden >= {min_burden}",
    )


def _qualifying_costs(
    min_severity: int,
    event_trace: tuple[EventEnvelope, ...],
) -> tuple[tuple[EventEnvelope, set[str]], ...]:
    by_id, positions, duplicates = _event_indexes(event_trace)
    qualifying: list[tuple[EventEnvelope, set[str]]] = []
    for envelope in event_trace:
        event = envelope.event
        if (
            not isinstance(event, CostIncurred)
            or event.severity < min_severity
            or envelope.event_id in duplicates
        ):
            continue
        choice = by_id.get(event.source_choice_event_id)
        effects = tuple(by_id.get(event_id) for event_id in event.effect_event_ids)
        if (
            choice is None
            or choice.event_id in duplicates
            or not isinstance(choice.event, PlayerActionSelected)
            or choice.event.accepted_cost_category != event.category
            or positions[choice.event_id] >= positions[envelope.event_id]
            or any(item is None for item in effects)
            or any(
                item.event_id in duplicates
                or positions[item.event_id] >= positions[envelope.event_id]
                for item in effects
                if item is not None
            )
        ):
            continue
        if not _matches_derived_cost(envelope, choice, effects):
            continue
        cited = {choice.event_id, envelope.event_id, *event.effect_event_ids}
        qualifying.append((envelope, cited))
    return tuple(qualifying)


def _matches_derived_cost(
    cost_envelope: EventEnvelope,
    choice_envelope: EventEnvelope,
    effect_envelopes: tuple[EventEnvelope | None, ...],
) -> bool:
    cost = cost_envelope.event
    choice = choice_envelope.event
    assert isinstance(cost, CostIncurred)
    assert isinstance(choice, PlayerActionSelected)

    resolved_effects = tuple(envelope for envelope in effect_envelopes if envelope is not None)
    if len(resolved_effects) == 1 and isinstance(resolved_effects[0].event, ObligationCreated):
        effect = resolved_effects[0]
        derived = derive_cost_incurred(
            choice_envelope.event_id,
            choice,
            effect.event_id,
            effect.event,
        )
        return derived == cost

    if len(resolved_effects) != 2:
        return False
    relationship_change = next(
        (
            envelope
            for envelope in resolved_effects
            if isinstance(envelope.event, RelationshipChanged)
        ),
        None,
    )
    relationship_event = next(
        (
            envelope
            for envelope in resolved_effects
            if isinstance(envelope.event, RelationshipEventRecorded)
        ),
        None,
    )
    if relationship_change is None or relationship_event is None:
        return False
    derived = derive_cost_incurred(
        choice_envelope.event_id,
        choice,
        relationship_change.event_id,
        relationship_change.event,
        relationship_event,
    )
    return derived == cost


def _cost_incurred(
    min_severity: int,
    event_trace: tuple[EventEnvelope, ...],
) -> _EvidenceResult:
    qualifying = _qualifying_costs(min_severity, event_trace)
    if not qualifying:
        return _EvidenceResult(
            satisfied=False,
            rationale=f"no qualifying cost with severity >= {min_severity}",
        )
    envelope, cited = qualifying[0]
    return _EvidenceResult(
        satisfied=True,
        cited_event_ids=_ordered_citations(cited, event_trace),
        rationale=f"cost {envelope.event_id} met severity >= {min_severity}",
    )


def _stance_defended(
    min_challenges: int,
    min_cost_severity: int,
    event_trace: tuple[EventEnvelope, ...],
) -> _EvidenceResult:
    by_id, positions, duplicates = _event_indexes(event_trace)
    costs = _qualifying_costs(min_cost_severity, event_trace)
    for reinforcement in event_trace:
        event = reinforcement.event
        if (
            not isinstance(event, StanceExpressed)
            or event.relation != "reinforced"
            or reinforcement.event_id in duplicates
        ):
            continue
        establishments = tuple(
            envelope
            for envelope in event_trace
            if isinstance(envelope.event, StanceExpressed)
            and envelope.event.key == event.key
            and envelope.event.axis == event.axis
            and envelope.event.value == event.value
            and envelope.event.relation == "established"
            and positions[envelope.event_id] < positions[reinforcement.event_id]
        )
        for establishment in reversed(establishments):
            challenges = tuple(
                envelope
                for envelope in event_trace
                if isinstance(envelope.event, StanceChallenged)
                and envelope.event.stance_key == event.key
                and positions[establishment.event_id]
                < positions[envelope.event_id]
                < positions[reinforcement.event_id]
                and envelope.event_id not in duplicates
            )
            if len(challenges) < min_challenges:
                continue
            reinforcement_choice = by_id.get(event.source_choice_event_id)
            if reinforcement_choice is None or not isinstance(
                reinforcement_choice.event, PlayerActionSelected
            ):
                continue
            if any(
                positions[challenge.event_id] >= positions[reinforcement_choice.event_id]
                for challenge in challenges
            ):
                continue
            matching_costs = tuple(
                (cost, cited)
                for cost, cited in costs
                if cost.event.source_choice_event_id == event.source_choice_event_id
                and positions[cost.event_id] > positions[reinforcement.event_id]
            )
            if not matching_costs:
                continue
            establishment_choice = by_id.get(establishment.event.source_choice_event_id)
            if (
                establishment.event_id in duplicates
                or establishment_choice is None
                or reinforcement_choice is None
                or not isinstance(establishment_choice.event, PlayerActionSelected)
                or not isinstance(reinforcement_choice.event, PlayerActionSelected)
                or positions[establishment_choice.event_id] >= positions[establishment.event_id]
                or positions[reinforcement_choice.event_id] >= positions[reinforcement.event_id]
            ):
                continue
            cost, cost_cited = matching_costs[0]
            cited = {
                establishment.event_id,
                establishment_choice.event_id,
                reinforcement.event_id,
                reinforcement_choice.event_id,
                cost.event_id,
                *(challenge.event_id for challenge in challenges),
                *cost_cited,
            }
            return _EvidenceResult(
                satisfied=True,
                cited_event_ids=_ordered_citations(cited, event_trace),
                rationale=(f"stance {event.key} was defended after {len(challenges)} challenge(s)"),
            )
    return _EvidenceResult(
        satisfied=False,
        rationale=(
            "no qualifying defended stance with "
            f"{min_challenges} challenges and cost severity >= {min_cost_severity}"
        ),
    )


def _combine_all(
    children: tuple[_EvidenceResult, ...],
    event_trace: tuple[EventEnvelope, ...],
) -> _EvidenceResult:
    cited = {
        event_id for child in children if child.satisfied for event_id in child.cited_event_ids
    }
    return _EvidenceResult(
        satisfied=all(child.satisfied for child in children),
        cited_event_ids=_ordered_citations(cited, event_trace),
        rationale="; ".join(child.rationale for child in children),
    )


def _combine_any(children: tuple[_EvidenceResult, ...]) -> _EvidenceResult:
    for child in children:
        if child.satisfied:
            return child
    return _EvidenceResult(
        satisfied=False,
        rationale="; ".join(child.rationale for child in children),
    )


def _ordered_citations(
    cited_event_ids: set[str],
    event_trace: tuple[EventEnvelope, ...],
) -> tuple[str, ...]:
    return tuple(
        envelope.event_id for envelope in event_trace if envelope.event_id in cited_event_ids
    )
