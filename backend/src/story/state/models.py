from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.story.script_pack.models import (
    CompiledScriptPack,
    ScriptPackSourceV1,
    ScriptPackSourceV2,
)


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


class DramaticArcPhase(str, Enum):
    APPROACH = "approach"
    FRACTURE = "fracture"
    ACCOUNTABILITY = "accountability"


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


class PromiseStatus(str, Enum):
    OPEN = "open"
    ESCALATED = "escalated"
    TRANSFORMED = "transformed"
    FULFILLED = "fulfilled"
    BROKEN = "broken"


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
    current_desire: str | None = None
    current_fear: str | None = None
    emotional_condition: str | None = None
    judgment_of_protagonist: str | None = None
    boundary_being_tested: str | None = None
    relationship_event_ids: tuple[str, ...] = ()
    unresolved_obligation_ids: frozenset[str] = frozenset()
    turning_point_ids: frozenset[str] = frozenset()


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


class NarrativeBlock(FrozenModel):
    kind: Literal["narration", "dialogue"]
    text: str = Field(min_length=1, max_length=4000)
    character_id: str | None = None

    @model_validator(mode="after")
    def validate_speaker(self) -> NarrativeBlock:
        if (self.kind == "dialogue") != (self.character_id is not None):
            raise ValueError("character_id is required only for dialogue blocks")
        return self


class PresentedChoice(FrozenModel):
    id: str = Field(min_length=1, max_length=100)
    action_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    intent: str = Field(min_length=1, max_length=240)
    target_character_id: str | None = None
    preview: str | None = Field(default=None, max_length=160)
    stance_axis: str | None = None
    stance_value: str | None = None
    accepted_risk: str | None = Field(default=None, max_length=240)
    potential_obligation_kind: str | None = None
    conflict_axis_id: str | None = None


class PendingSceneReference(FrozenModel):
    scene_id: str
    revision: int = Field(ge=1)
    terminal: str
    blocks: tuple[NarrativeBlock, ...] = ()


class SceneSummaryRecord(FrozenModel):
    """One committed scene's one-line summary, replayed from the event stream."""

    scene_id: str
    summary: str = Field(min_length=1, max_length=200)


class ProseBlockRecord(FrozenModel):
    """One committed narrative block kept verbatim in the recent-prose ring.

    The ring is derived state — rebuilt identically on replay — and only the
    newest ``RECENT_PROSE_BLOCK_CAP`` blocks are kept, so long playthroughs
    cannot grow it without bound.
    """

    scene_id: str
    kind: Literal["narration", "dialogue"]
    character_id: str | None = None
    text: str = Field(min_length=1, max_length=4000)


RECENT_PROSE_BLOCK_CAP = 60


class PendingDecisionReference(FrozenModel):
    decision_id: str
    scene_id: str
    revision: int = Field(ge=1)
    choices: tuple[PresentedChoice, ...] = Field(min_length=2, max_length=4)


class PendingConsequenceReference(FrozenModel):
    choice_event_id: str
    decision_id: str
    option_id: str
    action_id: str
    intent: str
    target_character_id: str | None = None
    stance_axis: str | None = None
    stance_value: str | None = None
    accepted_risk: str | None = None
    potential_obligation_kind: str | None = None
    conflict_axis_id: str | None = None
    outcome: Literal["success", "partial", "resisted", "backfire"] | None = None
    resolution_event_id: str | None = None


class EndingRuntime(FrozenModel):
    ending_id: str
    entered_at_revision: int = Field(ge=1)
    required_payoffs: tuple[str, ...] = ()
    final_scene_budget: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=120)
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)
    tone: str | None = None
    terminal_state_summary: str | None = None


class CompletionAssessmentRecord(FrozenModel):
    requirement_id: str
    satisfied: bool
    cited_event_ids: tuple[str, ...] = ()
    rationale: str = ""


class CompletionState(FrozenModel):
    cleared: bool
    assessments: tuple[CompletionAssessmentRecord, ...]


class DramaticQuestionRuntime(FrozenModel):
    key: str
    text: str
    source_event_id: str


class PromiseRuntime(FrozenModel):
    promise_id: str
    expectation: str
    source_event_id: str
    involved_character_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
    opened_at_decision: int = Field(ge=0)
    soft_deadline_decision: int = Field(ge=1)
    hard_deadline_decision: int = Field(ge=1)
    status: PromiseStatus = PromiseStatus.OPEN
    payoff_event_ids: tuple[str, ...] = ()


class ObligationRuntime(FrozenModel):
    obligation_id: str
    kind: str
    burden: int = Field(ge=1, le=3)
    source_choice_event_id: str
    character_id: str | None = None
    status: Literal["open", "fulfilled", "broken", "released"] = "open"
    resolution_scene_event_id: str | None = None
    resolution_event_id: str | None = None


class StanceRuntime(FrozenModel):
    key: str
    axis: str
    value: str
    relation: Literal["established", "reinforced", "qualified", "contradicted"]
    expression_event_ids: tuple[str, ...]
    source_choice_event_ids: tuple[str, ...]
    challenge_event_ids: tuple[str, ...] = ()


class ScheduledConsequenceRuntime(FrozenModel):
    consequence_id: str
    cause_event_id: str
    required_effect: str
    due_after_decision: int = Field(ge=1)
    hard_deadline_decision: int = Field(ge=1)
    status: Literal["scheduled", "realized", "broken"] = "scheduled"
    realization_event_id: str | None = None
    broken_event_id: str | None = None


class DramaticState(FrozenModel):
    primary_question: DramaticQuestionRuntime | None = None
    promises: dict[str, PromiseRuntime] = Field(default_factory=dict)
    obligations: dict[str, ObligationRuntime] = Field(default_factory=dict)
    stances: dict[str, StanceRuntime] = Field(default_factory=dict)
    scheduled_consequences: dict[str, ScheduledConsequenceRuntime] = Field(default_factory=dict)
    reached_turning_point_ids: frozenset[str] = frozenset()
    cost_event_ids: tuple[str, ...] = ()
    arc_phase: DramaticArcPhase = DramaticArcPhase.APPROACH
    decision_count: int = Field(default=0, ge=0)


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
    drama: DramaticState = Field(default_factory=DramaticState)
    threads: dict[str, NarrativeThread] = Field(default_factory=dict)
    pending_scene: PendingSceneReference | None = None
    pending_decision: PendingDecisionReference | None = None
    pending_consequence: PendingConsequenceReference | None = None
    ending: EndingRuntime | None = None
    completion: CompletionState | None = None
    scene_summaries: tuple[SceneSummaryRecord, ...] = ()
    recent_prose_blocks: tuple[ProseBlockRecord, ...] = ()


def initial_session_state(
    pack: CompiledScriptPack,
    session_id: str,
    session_seed: int,
) -> SessionState:
    source = pack.source

    # Read opening state from v1.0 (world.initial_situation) or v2.0 (opening_state)
    if isinstance(source, ScriptPackSourceV2):
        opening = source.opening_state
        initial_known = set(opening.known_facts)
    else:
        assert isinstance(source, ScriptPackSourceV1)
        opening_situation = source.world.initial_situation
        initial_known = set(opening_situation.known_facts)
        opening = opening_situation  # v1.0 InitialSituationSource has compatible fields

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
        knowledge.update(fact.id for fact in source.facts.fixed if character.id in fact.known_by)
        characters[character.id] = CharacterRuntime(
            character_id=character.id,
            knowledge=frozenset(knowledge),
            beliefs={key: BeliefRecord(value=value) for key, value in character.beliefs.items()},
        )

    # Starting pressure: v2.0 has opening_state.starting_pressure; v1.0 defaults to 0.1
    if isinstance(source, ScriptPackSourceV2):
        starting_pressure = source.opening_state.starting_pressure
    else:
        starting_pressure = 0.1

    world = WorldSnapshot(
        location_id=opening.location,
        time_label=opening.time_label,
        present_character_ids=opening.present_characters,
        relationships={
            character.id: dict(character.initial_relationship) for character in source.characters
        },
        goals={goal.id: GoalRuntime(goal_id=goal.id) for goal in source.goals},
        max_scenes=source.experience.max_scenes,
        reserved_resolution_scenes=source.experience.reserved_resolution_scenes,
        pressure=starting_pressure,
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
