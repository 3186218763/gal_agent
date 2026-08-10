from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field

from src.story.state.models import (
    BeliefRecord,
    EndingRuntime,
    FrozenModel,
    GoalStatus,
    NarrativeThread,
    StoryPhase,
    ThreadStatus,
    utc_now,
)


class SceneCommitted(FrozenModel):
    type: Literal["scene_committed"] = "scene_committed"
    scene_id: str
    terminal: Literal["continue", "decision", "ending"]
    location_id: str
    present_character_ids: tuple[str, ...]
    decision_id: str | None = None


class SceneAcknowledged(FrozenModel):
    type: Literal["scene_acknowledged"] = "scene_acknowledged"
    scene_id: str


class PlayerActionSelected(FrozenModel):
    type: Literal["player_action_selected"] = "player_action_selected"
    decision_id: str
    option_id: str
    idempotency_key: str


class ActionResolved(FrozenModel):
    type: Literal["action_resolved"] = "action_resolved"
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


class CharacterLearnedFact(FrozenModel):
    type: Literal["character_learned_fact"] = "character_learned_fact"
    character_id: str
    fact_id: str


class BeliefChanged(FrozenModel):
    type: Literal["belief_changed"] = "belief_changed"
    character_id: str
    belief_id: str
    belief: BeliefRecord


class RelationshipChanged(FrozenModel):
    type: Literal["relationship_changed"] = "relationship_changed"
    character_id: str
    axis: str
    delta: int = Field(ge=-100, le=100)


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


StoryEvent = Annotated[
    SceneCommitted
    | SceneAcknowledged
    | PlayerActionSelected
    | ActionResolved
    | FactCommitted
    | FactEvidenced
    | FactRevealed
    | CharacterLearnedFact
    | BeliefChanged
    | RelationshipChanged
    | GoalAdvanced
    | ThreadOpened
    | ThreadAdvanced
    | ThreadClosed
    | PhaseAdvanced
    | EndingEntered
    | SessionEnded,
    Field(discriminator="type"),
]


class EventEnvelope(FrozenModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    sequence: int = Field(ge=1)
    occurred_at: datetime = Field(default_factory=utc_now)
    event: StoryEvent
