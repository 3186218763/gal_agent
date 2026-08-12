"""Deterministic completion judge — evaluates final state against author requirements."""

from __future__ import annotations

from typing import Protocol

from src.story.runtime.segment_contracts import (
    CompletionAssessment,
    CompletionResult,
)
from src.story.state import (
    EventEnvelope,
    FactCommitted,
    FactTruthStatus,
    GoalAdvanced,
    SessionState,
)


class _EvidenceHintsLike(Protocol):
    fact_ids: tuple[str, ...]
    goal_ids: tuple[str, ...]


class _RequirementLike(Protocol):
    id: str
    description: str
    evidence_hints: _EvidenceHintsLike


class CompletionJudge:
    """Evaluates the final state and event trace against completion requirements.

    The judge is deterministic: it checks whether evidence hints (fact IDs,
    goal IDs) are satisfied in the final state. It cannot add or alter
    requirements. The kernel computes ``cleared = all(satisfied)``.
    """

    def evaluate(
        self,
        requirements: tuple[_RequirementLike, ...],
        final_state: SessionState,
        event_trace: tuple[EventEnvelope, ...],
    ) -> CompletionResult:
        assessments: list[CompletionAssessment] = []

        for req in requirements:
            hints = req.evidence_hints
            fact_ids = tuple(getattr(hints, "fact_ids", ()))
            goal_ids = tuple(getattr(hints, "goal_ids", ()))

            satisfied = True
            cited: list[str] = []
            rationale_parts: list[str] = []

            for fact_id in fact_ids:
                fact = final_state.facts.get(fact_id)
                if fact is not None and fact.truth_status == FactTruthStatus.COMMITTED:
                    cited.extend(
                        env.event_id
                        for env in event_trace
                        if isinstance(env.event, FactCommitted)
                        and env.event.fact_id == fact_id
                    )
                    rationale_parts.append(f"fact {fact_id} is committed")
                else:
                    satisfied = False
                    rationale_parts.append(f"fact {fact_id} is not committed")

            for goal_id in goal_ids:
                goal = final_state.world.goals.get(goal_id)
                if goal is not None and goal.completed:
                    cited.extend(
                        env.event_id
                        for env in event_trace
                        if isinstance(env.event, GoalAdvanced)
                        and env.event.goal_id == goal_id
                    )
                    rationale_parts.append(f"goal {goal_id} is completed")
                else:
                    satisfied = False
                    rationale_parts.append(f"goal {goal_id} is not completed")

            if not fact_ids and not goal_ids:
                satisfied = False
                rationale_parts.append(
                    "no evidence hints provided; cannot auto-satisfy"
                )

            assessments.append(
                CompletionAssessment(
                    requirement_id=req.id,
                    satisfied=satisfied,
                    cited_event_ids=tuple(dict.fromkeys(cited)),
                    rationale="; ".join(rationale_parts),
                )
            )

        cleared = all(a.satisfied for a in assessments) if assessments else False
        return CompletionResult(
            assessments=tuple(assessments),
            cleared=cleared,
        )
