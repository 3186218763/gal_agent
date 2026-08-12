from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.story.conditions import ConditionProgram

SafeId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$"),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IdentitySource(StrictModel):
    id: SafeId
    title: str = Field(min_length=1, max_length=120)
    language: str = Field(min_length=2, max_length=20)
    genres: tuple[str, ...] = ()
    expected_minutes: int = Field(ge=15, le=360)


class ExperienceSource(StrictModel):
    viewpoint: Literal["first_person", "third_person_limited"]
    prose_style: str = Field(min_length=1, max_length=200)
    tone: str = Field(min_length=1, max_length=200)
    choice_density: Literal["key_moments"] = "key_moments"
    min_scenes: int = Field(ge=4, le=200)
    max_scenes: int = Field(ge=8, le=240)
    reserved_resolution_scenes: int = Field(default=3, ge=1, le=8)
    # Forbidden content for v1.0 packs; v2.0 reads from WorldSettingSource instead.
    forbidden_content: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_scene_budget(self) -> ExperienceSource:
        if self.min_scenes >= self.max_scenes:
            raise ValueError("min_scenes must be smaller than max_scenes")
        if self.min_scenes + self.reserved_resolution_scenes > self.max_scenes:
            raise ValueError("scene budget does not leave room for resolution")
        return self


class PersonalitySource(StrictModel):
    traits: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    fears: tuple[str, ...] = ()
    flaws: tuple[str, ...] = ()


class BoundariesSource(StrictModel):
    cannot: tuple[str, ...] = ()


class ProtagonistSource(StrictModel):
    id: SafeId
    name: str = Field(min_length=1, max_length=80)
    personality: PersonalitySource
    background: str = Field(min_length=1)
    capabilities: tuple[SafeId, ...]
    boundaries: BoundariesSource = Field(default_factory=BoundariesSource)


class LocationSource(StrictModel):
    id: SafeId
    name: str = Field(min_length=1, max_length=100)
    tags: tuple[SafeId, ...] = ()


class FactionSource(StrictModel):
    id: SafeId
    name: str = Field(min_length=1, max_length=100)
    description: str = ""


class InitialSituationSource(StrictModel):
    location: SafeId
    present_characters: tuple[SafeId, ...] = ()
    known_facts: tuple[SafeId, ...] = ()
    time_label: str = "opening"


class WorldSource(StrictModel):
    premise: str = Field(min_length=1)
    immutable_rules: tuple[str, ...] = ()
    locations: tuple[LocationSource, ...]
    factions: tuple[FactionSource, ...] = ()
    initial_situation: InitialSituationSource


class VoiceSource(StrictModel):
    style: str = Field(min_length=1)
    forbidden: tuple[str, ...] = ()


class CharacterSource(StrictModel):
    id: SafeId
    name: str = Field(min_length=1, max_length=80)
    public_profile: str = Field(min_length=1)
    personality: PersonalitySource
    voice: VoiceSource
    drives: tuple[str, ...]
    knowledge: tuple[SafeId, ...] = ()
    secrets: tuple[SafeId, ...] = ()
    beliefs: dict[SafeId, Any] = Field(default_factory=dict)
    capabilities: tuple[SafeId, ...] = ()
    initial_relationship: dict[SafeId, int] = Field(default_factory=dict)
    boundaries: BoundariesSource = Field(default_factory=BoundariesSource)


class FixedFactSource(StrictModel):
    id: SafeId
    statement: str = Field(min_length=1)
    known_by: tuple[SafeId, ...] = ()
    visibility: Literal["hidden", "revealed"] = "hidden"


class LatentCandidateSource(StrictModel):
    value: str = Field(min_length=1, max_length=120)
    weight: float = Field(default=1.0, gt=0)
    requirements: tuple[str, ...] = ()


class LatentQuestionSource(StrictModel):
    id: SafeId
    question: str = Field(min_length=1)
    selection: Literal["lazy_commit"] = "lazy_commit"
    candidates: tuple[LatentCandidateSource, ...] = Field(min_length=2)
    commit_when: tuple[
        Literal["first_irreversible_evidence", "explicit_revelation"],
        ...,
    ]
    evidence_required: int = Field(default=1, ge=1, le=10)


class DerivedFactSource(StrictModel):
    id: SafeId
    condition: str = Field(min_length=1)


class FactsSource(StrictModel):
    fixed: tuple[FixedFactSource, ...] = ()
    latent_questions: tuple[LatentQuestionSource, ...] = ()
    derived: tuple[DerivedFactSource, ...] = ()


class GoalSource(StrictModel):
    id: SafeId
    owner: SafeId
    desire: str = Field(min_length=1)
    urgency: float = Field(ge=0, le=1)
    conflicts_with: tuple[SafeId, ...] = ()
    success_condition: str = Field(min_length=1)
    failure_condition: str = Field(min_length=1)


class EffectBoundsSource(StrictModel):
    relationship_axes: dict[SafeId, tuple[int, int]] = Field(default_factory=dict)
    goal_progress: tuple[float, float] = (-0.15, 0.25)
    can_commit_facts: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> EffectBoundsSource:
        for axis, bounds in self.relationship_axes.items():
            if bounds[0] > bounds[1]:
                raise ValueError(f"invalid bounds for relationship axis {axis}")
            if bounds[0] < -100 or bounds[1] > 100:
                raise ValueError(
                    f"relationship bounds for {axis} must stay within -100..100"
                )
        if self.goal_progress[0] > self.goal_progress[1]:
            raise ValueError("invalid goal_progress bounds")
        if self.goal_progress[0] < -1 or self.goal_progress[1] > 1:
            raise ValueError("goal_progress bounds must stay within -1..1")
        return self


class ActionExtensionSource(StrictModel):
    id: SafeId
    preconditions: tuple[str, ...] = ()
    effects: EffectBoundsSource = Field(default_factory=EffectBoundsSource)
    risk_tags: tuple[SafeId, ...] = ()


class InteractionRulesSource(StrictModel):
    enabled_standard: tuple[SafeId, ...]
    disabled: tuple[SafeId, ...] = ()
    extensions: tuple[ActionExtensionSource, ...] = ()


class ConditionGroupSource(StrictModel):
    all: tuple[str, ...] = ()
    any: tuple[str, ...] = ()
    none: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_condition(self) -> ConditionGroupSource:
        if not self.all and not self.any and not self.none:
            raise ValueError("ending eligibility must contain a condition")
        return self


class EndingSource(StrictModel):
    id: SafeId
    title: str = Field(min_length=1, max_length=120)
    type: SafeId
    priority: int = Field(ge=0, le=1000)
    eligibility: ConditionGroupSource
    required_outcomes: tuple[str, ...] = Field(min_length=1)
    forbidden_outcomes: tuple[str, ...] = ()
    closing_tone: str = Field(min_length=1)


class EvidenceHintsSource(StrictModel):
    fact_ids: tuple[SafeId, ...] = ()
    goal_ids: tuple[SafeId, ...] = ()


class CompletionRequirementSource(StrictModel):
    id: SafeId
    description: str = Field(min_length=1)
    evidence_hints: EvidenceHintsSource = Field(default_factory=EvidenceHintsSource)


class WorldSettingSource(StrictModel):
    premise: str = Field(min_length=1)
    immutable_rules: tuple[str, ...] = ()
    locations: tuple[LocationSource, ...] = Field(min_length=1)
    factions: tuple[FactionSource, ...] = ()
    # Authoritative forbidden_content source for v2.0 packs (read by _get_forbidden_content).
    forbidden_content: tuple[str, ...] = ()
    fact_rules: tuple[str, ...] = ()


class HistoryEventSource(StrictModel):
    summary: str = Field(min_length=1)
    participants: tuple[SafeId, ...] = ()
    remembered_differently_by: dict[SafeId, str] = Field(default_factory=dict)


class StoryHistorySource(StrictModel):
    summary: str = Field(min_length=1)
    events: tuple[HistoryEventSource, ...] = ()


class OpeningStateSource(StrictModel):
    location: SafeId
    present_characters: tuple[SafeId, ...] = ()
    known_facts: tuple[SafeId, ...] = ()
    time_label: str = "opening"
    starting_pressure: float = Field(default=0.1, ge=0, le=1)


class ScriptPackSource(StrictModel):
    """Discriminated union of v1.0 and v2.0 pack sources via schema_version.

    ``model_validate`` inspects ``schema_version`` and dispatches to
    :class:`ScriptPackSourceV1` or :class:`ScriptPackSourceV2`.  Because both
    subclasses inherit from this class, ``isinstance(x, ScriptPackSource)``
    works directly for either variant.
    """

    @model_validator(mode="wrap")
    @classmethod
    def _dispatch_by_version(cls, data, handler):
        # When validating a concrete subclass directly, defer to normal
        # field validation so the wrap validator does not recurse.
        if cls is not ScriptPackSource:
            return handler(data)
        if isinstance(data, (ScriptPackSourceV1, ScriptPackSourceV2)):
            return data
        if isinstance(data, dict):
            version = data.get("schema_version")
            if version == "1.0":
                return ScriptPackSourceV1.model_validate(data)
            if version == "2.0":
                return ScriptPackSourceV2.model_validate(data)
            raise ValueError(f"Unknown schema_version: {version}")
        raise ValueError(f"Cannot validate {type(data)} as ScriptPackSource")


class ScriptPackSourceV1(ScriptPackSource):
    schema_version: Literal["1.0"] = "1.0"
    identity: IdentitySource
    experience: ExperienceSource
    protagonist: ProtagonistSource
    world: WorldSource
    characters: tuple[CharacterSource, ...] = Field(min_length=1)
    facts: FactsSource
    goals: tuple[GoalSource, ...] = Field(min_length=1)
    interaction_rules: InteractionRulesSource
    endings: tuple[EndingSource, ...] = Field(min_length=4)
    assets: dict[str, Any] = Field(default_factory=dict)


class ScriptPackSourceV2(ScriptPackSource):
    schema_version: Literal["2.0"] = "2.0"
    identity: IdentitySource
    experience: ExperienceSource
    protagonist: ProtagonistSource
    world_setting: WorldSettingSource
    story_history: StoryHistorySource
    opening_state: OpeningStateSource
    characters: tuple[CharacterSource, ...] = Field(min_length=1)
    facts: FactsSource
    goals: tuple[GoalSource, ...] = Field(min_length=1)
    completion_requirements: tuple[CompletionRequirementSource, ...] = Field(min_length=1)
    interaction_rules: InteractionRulesSource
    assets: dict[str, Any] = Field(default_factory=dict)


class CompiledScriptPack(StrictModel):
    source: ScriptPackSource
    pack_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    conditions: dict[str, ConditionProgram]
    character_ids: frozenset[str]
    fact_ids: frozenset[str]
    goal_ids: frozenset[str]
    ending_ids: frozenset[str]
    action_ids: frozenset[str]
    completion_requirement_ids: frozenset[str] = frozenset()
