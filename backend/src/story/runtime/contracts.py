"""Strict runtime contracts and ports for Planner/Writer."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.story.script_pack.models import CompiledScriptPack, EndingSource
from src.story.state import NarrativeBlock, PresentedChoice, SceneCommitted, SessionState


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelContractError(RuntimeError):
    pass


class RuntimeGenerationUnavailable(RuntimeError):
    """The real model could not produce a valid, committable turn."""


class ChoicePlan(RuntimeModel):
    option_id: str
    action_id: str
    intent: str
    target_character_id: str | None = None


class FactCommitPlan(RuntimeModel):
    fact_id: str
    value: str
    reason: Literal["first_irreversible_evidence", "explicit_revelation"]
    reveal: bool = False
    learned_by: tuple[str, ...] = ()


class ScenePlan(RuntimeModel):
    scene_id: str
    summary: str
    location_id: str
    present_character_ids: tuple[str, ...]
    focus_goal_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
    fact_commits: tuple[FactCommitPlan, ...] = ()
    terminal: Literal["continue", "decision"]
    decision_id: str | None = None
    choices: tuple[ChoicePlan, ...] = ()

    @model_validator(mode="after")
    def validate_terminal(self) -> ScenePlan:
        if self.terminal == "decision":
            if self.decision_id is None or not 2 <= len(self.choices) <= 4:
                raise ValueError("decision scenes require decision_id and 2-4 choices")
        elif self.decision_id is not None or self.choices:
            raise ValueError("continue scenes cannot contain a decision")
        return self


class RelationshipDelta(RuntimeModel):
    character_id: str
    axis: str
    delta: int


class GoalDelta(RuntimeModel):
    goal_id: str
    delta: float


class LearnedFactPlan(RuntimeModel):
    character_id: str
    fact_ids: tuple[str, ...] = ()


class ActionResolution(RuntimeModel):
    action_id: str
    outcome: Literal["success", "partial", "resisted", "backfire"]
    relationship_deltas: tuple[RelationshipDelta, ...] = ()
    goal_deltas: tuple[GoalDelta, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()
    reveal_fact_ids: tuple[str, ...] = ()
    learned_facts: tuple[LearnedFactPlan, ...] = ()


class WrittenChoice(RuntimeModel):
    option_id: str
    label: str
    preview: str | None = None


class SceneDraft(RuntimeModel):
    scene_id: str
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)
    choices: tuple[WrittenChoice, ...] = ()


class EndingDraft(RuntimeModel):
    ending_id: str
    title: str
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)


class PlannerOutput(RuntimeModel):
    kind: Literal["scene", "resolution"]
    scene: ScenePlan | None = None
    resolution: ActionResolution | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> PlannerOutput:
        if self.kind == "scene" and self.scene is not None and self.resolution is None:
            return self
        if self.kind == "resolution" and self.resolution is not None and self.scene is None:
            return self
        raise ValueError("planner kind must match exactly one payload")


class WriterOutput(RuntimeModel):
    kind: Literal["scene", "ending"]
    scene: SceneDraft | None = None
    ending: EndingDraft | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> WriterOutput:
        if self.kind == "scene" and self.scene is not None and self.ending is None:
            return self
        if self.kind == "ending" and self.ending is not None and self.scene is None:
            return self
        raise ValueError("writer kind must match exactly one payload")


class PlannerPort(Protocol):
    async def plan_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> ScenePlan:
        raise NotImplementedError

    async def resolve_action(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        choice: PresentedChoice,
    ) -> ActionResolution:
        raise NotImplementedError


class WriterPort(Protocol):
    async def write_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: ScenePlan,
    ) -> SceneDraft:
        raise NotImplementedError

    async def write_ending(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        ending: EndingSource,
    ) -> EndingDraft:
        raise NotImplementedError


class RuntimeScene(RuntimeModel):
    session_id: str
    revision: int
    scene_id: str
    blocks: tuple[NarrativeBlock, ...]
    choices: tuple[PresentedChoice, ...] = ()
    ending_id: str | None = None
    ending_title: str | None = None

    @classmethod
    def from_committed(
        cls,
        state: SessionState,
        event: SceneCommitted,
    ) -> RuntimeScene:
        ending = state.ending if event.terminal == "ending" else None
        return cls(
            session_id=state.session_id,
            revision=state.revision,
            scene_id=event.scene_id,
            blocks=event.blocks,
            choices=event.choices,
            ending_id=ending.ending_id if ending is not None else None,
            ending_title=ending.title if ending is not None else None,
        )


class ActionResult(RuntimeModel):
    session_id: str
    revision: int
    action_id: str
    outcome: str


class RuntimeRevisionConflict(RuntimeError):
    pass


class DecisionRequired(RuntimeError):
    pass


class InvalidChoice(RuntimeError):
    pass


class RuntimeSessionEnded(RuntimeError):
    pass


class PackMismatch(RuntimeError):
    pass
