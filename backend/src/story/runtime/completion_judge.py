"""Deterministic completion evaluation over committed semantic evidence."""

from __future__ import annotations

from dataclasses import dataclass

from src.story.runtime.segment_contracts import CompletionAssessment, CompletionResult
from src.story.script_pack.models import CompletionEvidenceSource, CompletionRequirementSource
from src.story.state import EventEnvelope, FactCommitted, FactRevealed, SessionState


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
        return _EvidenceResult(
            satisfied=False,
            rationale=(
                "no qualifying relationship turning point "
                f"{node.relationship_turning_point.turning_point_id}"
            ),
        )
    if node.obligation_fulfilled is not None:
        return _EvidenceResult(
            satisfied=False,
            rationale=(
                "no qualifying fulfilled obligation with burden >= "
                f"{node.obligation_fulfilled.min_burden}"
            ),
        )
    if node.cost_incurred is not None:
        return _EvidenceResult(
            satisfied=False,
            rationale=f"no qualifying cost with severity >= {node.cost_incurred.min_severity}",
        )
    assert node.stance_defended is not None
    return _EvidenceResult(
        satisfied=False,
        rationale=(
            "no qualifying defended stance with "
            f"{node.stance_defended.min_challenges} challenges and cost severity >= "
            f"{node.stance_defended.min_cost_severity}"
        ),
    )


def _fact_revealed(
    fact_id: str,
    event_trace: tuple[EventEnvelope, ...],
) -> _EvidenceResult:
    revealed = tuple(
        envelope
        for envelope in event_trace
        if isinstance(envelope.event, FactRevealed) and envelope.event.fact_id == fact_id
    )
    if not revealed:
        return _EvidenceResult(
            satisfied=False,
            rationale=f"fact {fact_id} was not revealed by event",
        )

    citation_ids = {envelope.event_id for envelope in revealed}
    for envelope in event_trace:
        if isinstance(envelope.event, FactCommitted) and envelope.event.fact_id == fact_id:
            citation_ids.add(envelope.event_id)
            citation_ids.update(envelope.event.evidence_event_ids)

    return _EvidenceResult(
        satisfied=True,
        cited_event_ids=_ordered_citations(citation_ids, event_trace),
        rationale=f"fact {fact_id} was revealed",
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
