from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field

from src.story.state.models import (
    BeliefRecord,
    CompletionAssessmentRecord,
    DramaticArcPhase,
    EndingRuntime,
    FrozenModel,
    GoalStatus,
    NarrativeBlock,
    NarrativeThread,
    PresentedChoice,
    PromiseStatus,
    StoryPhase,
    ThreadStatus,
    utc_now,
)


class SceneCommitted(FrozenModel):
    type: Literal["scene_committed"] = "scene_committed"
    scene_id: str
    terminal: Literal["continue", "decision", "ending"] = "continue"
    location_id: str
    present_character_ids: tuple[str, ...]
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)
    decision_id: str | None = None
    choices: tuple[PresentedChoice, ...] = ()
    # One-line scene summary authored with the segment (None in events
    # recorded before summaries existed).
    summary: str | None = None
    # Deterministic verbatim excerpts of the scene's distinctive lines
    # (empty in events recorded before scene archives existed).
    key_lines: tuple[str, ...] = ()


class SceneAcknowledged(FrozenModel):
    type: Literal["scene_acknowledged"] = "scene_acknowledged"
    scene_id: str


class EntityAttributeSet(FrozenModel):
    """Canon Ledger: establish or update one canonical entity attribute."""

    type: Literal["entity_attribute_set"] = "entity_attribute_set"
    entity_id: str = Field(min_length=1, max_length=120)
    entity_name: str = Field(min_length=1, max_length=120)
    attribute: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=200)
    scene_id: str = ""


class NarrativePromiseMarked(FrozenModel):
    """Canon Ledger: the prose put a promise on record (Chekhov's gun)."""

    type: Literal["narrative_promise_marked"] = "narrative_promise_marked"
    promise_id: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1, max_length=400)
    scene_id: str = ""


class NarrativePromiseSettled(FrozenModel):
    """Canon Ledger: a prose promise was paid off or explicitly released."""

    type: Literal["narrative_promise_settled"] = "narrative_promise_settled"
    promise_id: str = Field(min_length=1, max_length=120)
    outcome: Literal["paid", "released"]
    scene_id: str = ""


class MotifUsed(FrozenModel):
    """Canon Ledger: a structural motif/gesture/image was performed."""

    type: Literal["motif_used"] = "motif_used"
    motif_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    scene_id: str = ""


class PlayerActionSelected(FrozenModel):
    type: Literal["player_action_selected"] = "player_action_selected"
    decision_id: str
    option_id: str
    action_id: str = ""
    intent: str = ""
    target_character_id: str | None = None
    idempotency_key: str
    stance_axis: str | None = None
    stance_value: str | None = None
    accepted_risk: str | None = None
    # Kept as a compatibility alias for existing semantic cost evidence.
    accepted_cost_category: str | None = None
    potential_obligation_kind: str | None = None
    conflict_axis_id: str | None = None


class ActionResolved(FrozenModel):
    type: Literal["action_resolved"] = "action_resolved"
    source_choice_event_id: str | None = None
    action_id: str
    outcome: Literal["success", "partial", "resisted", "backfire"]


class FactCommitted(FrozenModel):
    type: Literal["fact_committed"] = "fact_committed"
    fact_id: str
    value: Any
    evidence_event_ids: tuple[str, ...] = ()


class FactEvidenced(FrozenModel):
    type: Literal["fact_evidenced"] = "fact_evidenced"
    fact_id: str
    evidence_event_id: str


class FactRevealed(FrozenModel):
    type: Literal["fact_revealed"] = "fact_revealed"
    fact_id: str
    # Finale exemption: an ending scene may commit and reveal a multi-evidence
    # latent fact in one shot (the finale is the payoff; the evidence ladder
    # still applies to every non-ending reveal).  Defaults to False so
    # replaying pre-exemption event streams keeps the strict semantics.
    finale: bool = False


class CharacterLearnedFact(FrozenModel):
    type: Literal["character_learned_fact"] = "character_learned_fact"
    character_id: str
    fact_id: str


class BeliefChanged(FrozenModel):
    type: Literal["belief_changed"] = "belief_changed"
    character_id: str
    belief_id: str
    belief: BeliefRecord


class CharacterDramaticStateChanged(FrozenModel):
    type: Literal["character_dramatic_state_changed"] = "character_dramatic_state_changed"
    character_id: str
    source_event_id: str
    current_desire: str | None
    current_fear: str | None
    emotional_condition: str | None
    judgment_of_protagonist: str | None
    boundary_being_tested: str | None


class RelationshipChanged(FrozenModel):
    type: Literal["relationship_changed"] = "relationship_changed"
    character_id: str
    axis: str
    delta: int = Field(ge=-100, le=100)
    source_choice_event_id: str | None = None
    relationship_event_id: str | None = None


class DramaticQuestionSet(FrozenModel):
    type: Literal["dramatic_question_set"] = "dramatic_question_set"
    key: str
    text: str
    source_event_id: str


class StanceExpressed(FrozenModel):
    type: Literal["stance_expressed"] = "stance_expressed"
    key: str
    axis: str
    value: str
    relation: Literal["established", "reinforced", "qualified", "contradicted"]
    source_choice_event_id: str


class StanceChallenged(FrozenModel):
    type: Literal["stance_challenged"] = "stance_challenged"
    stance_key: str
    scene_event_id: str
    challenging_character_id: str | None = None


class RelationshipEventRecorded(FrozenModel):
    type: Literal["relationship_event_recorded"] = "relationship_event_recorded"
    character_id: str
    tag: str
    source_choice_event_id: str
    scene_event_id: str


class RelationshipTurningPointReached(FrozenModel):
    type: Literal["relationship_turning_point_reached"] = "relationship_turning_point_reached"
    turning_point_id: str
    character_id: str
    relationship_event_ids: tuple[str, ...] = Field(min_length=1)


class PromiseOpened(FrozenModel):
    type: Literal["promise_opened"] = "promise_opened"
    promise_id: str
    expectation: str
    source_event_id: str
    involved_character_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
    soft_deadline_decision: int = Field(ge=1)
    hard_deadline_decision: int = Field(ge=1)


class PromiseChanged(FrozenModel):
    type: Literal["promise_changed"] = "promise_changed"
    promise_id: str
    status: PromiseStatus
    payoff_event_ids: tuple[str, ...] = ()


class ObligationCreated(FrozenModel):
    type: Literal["obligation_created"] = "obligation_created"
    obligation_id: str
    kind: str
    burden: int = Field(ge=1, le=3)
    source_choice_event_id: str
    character_id: str | None = None


class ObligationResolved(FrozenModel):
    type: Literal["obligation_resolved"] = "obligation_resolved"
    obligation_id: str
    outcome: Literal["fulfilled", "broken", "released"]
    resolution_scene_event_id: str


class ConsequenceScheduled(FrozenModel):
    type: Literal["consequence_scheduled"] = "consequence_scheduled"
    consequence_id: str
    cause_event_id: str
    required_effect: str
    due_after_decision: int = Field(ge=1)
    hard_deadline_decision: int = Field(ge=1)


class ConsequenceRealized(FrozenModel):
    type: Literal["consequence_realized"] = "consequence_realized"
    consequence_id: str
    effect_event_ids: tuple[str, ...] = Field(min_length=1)


class ConsequenceBroken(FrozenModel):
    type: Literal["consequence_broken"] = "consequence_broken"
    consequence_id: str
    reason: str
    evidence_event_ids: tuple[str, ...] = Field(min_length=1)


class CostIncurred(FrozenModel):
    type: Literal["cost_incurred"] = "cost_incurred"
    category: str
    severity: int = Field(ge=1, le=3)
    source_choice_event_id: str
    effect_event_ids: tuple[str, ...] = Field(min_length=1)


class ArcPressureAdvanced(FrozenModel):
    type: Literal["arc_pressure_advanced"] = "arc_pressure_advanced"
    phase: DramaticArcPhase


class GoalAdvanced(FrozenModel):
    type: Literal["goal_advanced"] = "goal_advanced"
    goal_id: str
    delta: float = Field(ge=-1, le=1)
    status: GoalStatus | None = None
    evidence_event_id: str | None = None


class ThreadOpened(FrozenModel):
    type: Literal["thread_opened"] = "thread_opened"
    thread: NarrativeThread


class ThreadAdvanced(FrozenModel):
    type: Literal["thread_advanced"] = "thread_advanced"
    thread_id: str
    urgency: float | None = Field(default=None, ge=0, le=1)


class ThreadClosed(FrozenModel):
    type: Literal["thread_closed"] = "thread_closed"
    thread_id: str
    status: Literal[ThreadStatus.RESOLVED, ThreadStatus.ABANDONED]


class PhaseAdvanced(FrozenModel):
    type: Literal["phase_advanced"] = "phase_advanced"
    phase: StoryPhase


class EndingEntered(FrozenModel):
    type: Literal["ending_entered"] = "ending_entered"
    ending: EndingRuntime


class SessionEnded(FrozenModel):
    type: Literal["session_ended"] = "session_ended"
    ending_id: str


class DecisionPresented(FrozenModel):
    type: Literal["decision_presented"] = "decision_presented"
    decision_id: str
    choices: tuple[PresentedChoice, ...] = Field(min_length=2, max_length=4)


class EndingGenerated(FrozenModel):
    type: Literal["ending_generated"] = "ending_generated"
    ending_id: str
    title: str = Field(min_length=1, max_length=120)
    tone: str = Field(min_length=1)
    terminal_state_summary: str = Field(min_length=1)
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)


class CompletionEvaluated(FrozenModel):
    type: Literal["completion_evaluated"] = "completion_evaluated"
    cleared: bool
    assessments: tuple[CompletionAssessmentRecord, ...]


class BeatCompleted(FrozenModel):
    """A Beat Map beat was performed by the segment just committed.

    Emitted by the simulator for every ``SegmentPlan.beat_ids`` entry so the
    DramaManager never re-performs a beat (``once`` semantics).
    """

    type: Literal["beat_completed"] = "beat_completed"
    beat_id: str
    source_event_id: str


StoryEvent = Annotated[
    SceneCommitted
    | SceneAcknowledged
    | EntityAttributeSet
    | NarrativePromiseMarked
    | NarrativePromiseSettled
    | MotifUsed
    | PlayerActionSelected
    | ActionResolved
    | FactCommitted
    | FactEvidenced
    | FactRevealed
    | CharacterLearnedFact
    | BeliefChanged
    | CharacterDramaticStateChanged
    | RelationshipChanged
    | DramaticQuestionSet
    | StanceExpressed
    | StanceChallenged
    | RelationshipEventRecorded
    | RelationshipTurningPointReached
    | PromiseOpened
    | PromiseChanged
    | ObligationCreated
    | ObligationResolved
    | ConsequenceScheduled
    | ConsequenceRealized
    | ConsequenceBroken
    | CostIncurred
    | ArcPressureAdvanced
    | GoalAdvanced
    | ThreadOpened
    | ThreadAdvanced
    | ThreadClosed
    | PhaseAdvanced
    | EndingEntered
    | EndingGenerated
    | DecisionPresented
    | BeatCompleted
    | CompletionEvaluated
    | SessionEnded,
    Field(discriminator="type"),
]


class EventEnvelope(FrozenModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    sequence: int = Field(ge=1)
    occurred_at: datetime = Field(default_factory=utc_now)
    event: StoryEvent
