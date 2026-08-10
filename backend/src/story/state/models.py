from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.story.script_pack.models import CompiledScriptPack


def utc_now() -> datetime:
    return datetime.now(UTC)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionStatus(str, Enum):
    ACTIVE = "active"
    RESOLVING = "resolving"
    ENDED = "ended"


class StoryPhase(str, Enum):
    OPENING = "opening"
    EXPLORATION = "exploration"
    ESCALATION = "escalation"
    CRISIS = "crisis"
    RESOLUTION = "resolution"


class FactTruthStatus(str, Enum):
    POSSIBLE = "possible"
    STAGED = "staged"
    COMMITTED = "committed"


class FactVisibility(str, Enum):
    HIDDEN = "hidden"
    EVIDENCED = "evidenced"
    REVEALED = "revealed"


class GoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ThreadStatus(str, Enum):
    OPEN = "open"
    ADVANCING = "advancing"
    DORMANT = "dormant"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class FactRecord(FrozenModel):
    id: str
    truth_status: FactTruthStatus
    value: Any = None
    visibility: FactVisibility = FactVisibility.HIDDEN
    evidence_required: int = Field(default=0, ge=0)
    evidence_event_ids: tuple[str, ...] = ()
    committed_by_event_id: str | None = None
    known_by: frozenset[str] = frozenset()


class BeliefRecord(FrozenModel):
    value: Any
    confidence: float = Field(default=0.5, ge=0, le=1)
    source_event_id: str | None = None


class CharacterRuntime(FrozenModel):
    character_id: str
    knowledge: frozenset[str] = frozenset()
    beliefs: dict[str, BeliefRecord] = Field(default_factory=dict)
    suspicions: dict[str, BeliefRecord] = Field(default_factory=dict)
    intentions: tuple[str, ...] = ()
    emotional_state: dict[str, float] = Field(default_factory=dict)


class GoalRuntime(FrozenModel):
    goal_id: str
    status: GoalStatus = GoalStatus.ACTIVE
    progress: float = Field(default=0, ge=0, le=1)
    evidence_event_ids: tuple[str, ...] = ()

    @property
    def completed(self) -> bool:
        return self.status == GoalStatus.COMPLETED


class NarrativeThread(FrozenModel):
    id: str
    type: str
    status: ThreadStatus = ThreadStatus.OPEN
    introduced_at: str
    involved_character_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
    urgency: float = Field(default=0.5, ge=0, le=1)
    payoff_due_before: StoryPhase = StoryPhase.RESOLUTION
    last_advanced_event_id: str | None = None


class PendingSceneReference(FrozenModel):
    scene_id: str
    revision: int = Field(ge=1)
    terminal: str


class PendingDecisionReference(FrozenModel):
    decision_id: str
    scene_id: str
    revision: int = Field(ge=1)


class EndingRuntime(FrozenModel):
    ending_id: str
    entered_at_revision: int = Field(ge=1)
    required_payoffs: tuple[str, ...]
    final_scene_budget: int = Field(ge=1)


class WorldSnapshot(FrozenModel):
    location_id: str
    time_label: str
    present_character_ids: tuple[str, ...]
    object_states: dict[str, Any] = Field(default_factory=dict)
    relationships: dict[str, dict[str, int]] = Field(default_factory=dict)
    goals: dict[str, GoalRuntime] = Field(default_factory=dict)
    phase: StoryPhase = StoryPhase.OPENING
    pressure: float = Field(default=0.1, ge=0, le=1)
    scene_count: int = Field(default=0, ge=0)
    max_scenes: int = Field(ge=1)
    reserved_resolution_scenes: int = Field(ge=1)


class SessionState(FrozenModel):
    session_id: str
    pack_id: str
    pack_hash: str
    revision: int = Field(default=0, ge=0)
    status: SessionStatus = SessionStatus.ACTIVE
    session_seed: int
    created_at: datetime = Field(default_factory=utc_now)
    world: WorldSnapshot
    facts: dict[str, FactRecord]
    characters: dict[str, CharacterRuntime]
    threads: dict[str, NarrativeThread] = Field(default_factory=dict)
    pending_scene: PendingSceneReference | None = None
    pending_decision: PendingDecisionReference | None = None
    ending: EndingRuntime | None = None


def initial_session_state(
    pack: CompiledScriptPack,
    session_id: str,
    session_seed: int,
) -> SessionState:
    source = pack.source
    initial_known = set(source.world.initial_situation.known_facts)

    facts: dict[str, FactRecord] = {}
    for fact in source.facts.fixed:
        visibility = (
            FactVisibility.REVEALED
            if fact.visibility == "revealed" or fact.id in initial_known
            else FactVisibility.HIDDEN
        )
        facts[fact.id] = FactRecord(
            id=fact.id,
            truth_status=FactTruthStatus.COMMITTED,
            value=True,
            visibility=visibility,
            known_by=frozenset(fact.known_by),
        )
    for question in source.facts.latent_questions:
        facts[question.id] = FactRecord(
            id=question.id,
            truth_status=FactTruthStatus.POSSIBLE,
            visibility=FactVisibility.HIDDEN,
            evidence_required=question.evidence_required,
        )

    characters: dict[str, CharacterRuntime] = {}
    for character in source.characters:
        knowledge = set(character.knowledge)
        knowledge.update(
            fact.id for fact in source.facts.fixed if character.id in fact.known_by
        )
        characters[character.id] = CharacterRuntime(
            character_id=character.id,
            knowledge=frozenset(knowledge),
            beliefs={key: BeliefRecord(value=value) for key, value in character.beliefs.items()},
        )

    world = WorldSnapshot(
        location_id=source.world.initial_situation.location,
        time_label=source.world.initial_situation.time_label,
        present_character_ids=source.world.initial_situation.present_characters,
        relationships={
            character.id: dict(character.initial_relationship) for character in source.characters
        },
        goals={goal.id: GoalRuntime(goal_id=goal.id) for goal in source.goals},
        max_scenes=source.experience.max_scenes,
        reserved_resolution_scenes=source.experience.reserved_resolution_scenes,
    )

    return SessionState(
        session_id=session_id,
        pack_id=source.identity.id,
        pack_hash=pack.pack_hash,
        session_seed=session_seed,
        world=world,
        facts=facts,
        characters=characters,
    )
