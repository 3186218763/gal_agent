"""Contracts for segment-based runtime: plans, drafts, agent ports, guard, completion."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from src.story.runtime.contracts import (
    EndingDraft,
    FactCommitPlan,
    RuntimeModel,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import PresentedChoice, SessionState, StoryPhase

# ---------------------------------------------------------------------------
# Canon Ledger updates (writer-side narrative memory registration)
# ---------------------------------------------------------------------------


class EntityAttributeUpdate(RuntimeModel):
    """Register one canonical entity attribute established by this prose.

    Example: entity_id="notebook", attribute="cover", value="黑色硬皮".
    A conflicting value for an already-committed attribute is a continuity
    error the validator rejects with the old value quoted.
    """

    kind: Literal["entity_attribute"] = "entity_attribute"
    entity_id: str = Field(min_length=1, max_length=120)
    entity_name: str = Field(min_length=1, max_length=120)
    attribute: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=200)


class PromiseMarkUpdate(RuntimeModel):
    """The prose put a promise on record (a Chekhov's gun to pay off later)."""

    kind: Literal["promise_mark"] = "promise_mark"
    promise_id: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1, max_length=400)


class PromiseSettleUpdate(RuntimeModel):
    """A previously marked promise is paid off (or explicitly released)."""

    kind: Literal["promise_settle"] = "promise_settle"
    promise_id: str = Field(min_length=1, max_length=120)
    outcome: Literal["paid", "released"]


class MotifUpdate(RuntimeModel):
    """Register a distinctive motif/gesture/image this segment performed."""

    kind: Literal["motif"] = "motif"
    motif_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)


LedgerUpdate = Annotated[
    EntityAttributeUpdate | PromiseMarkUpdate | PromiseSettleUpdate | MotifUpdate,
    Field(discriminator="kind"),
]

# ---------------------------------------------------------------------------
# Ending proposal (Director's plan for a dynamic ending)
# ---------------------------------------------------------------------------


class EndingProposal(RuntimeModel):
    title: str = Field(min_length=1, max_length=120)
    tone: str = Field(min_length=1, max_length=80)
    terminal_state_summary: str = Field(min_length=1, max_length=600)


# ---------------------------------------------------------------------------
# Thread operations (Director's thread lifecycle proposals)
# ---------------------------------------------------------------------------


class ThreadOperation(RuntimeModel):
    kind: Literal["open", "advance", "close"]
    thread_id: str | None = None
    thread_type: str = ""
    involved_character_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
    urgency: float | None = None
    close_status: Literal["resolved", "abandoned"] | None = None


# ---------------------------------------------------------------------------
# Segment plan and draft
# ---------------------------------------------------------------------------


class SegmentPlan(RuntimeModel):
    segment_id: str
    scenes: tuple[ScenePlan, ...]
    terminal: Literal["decision", "ending"]
    ending_proposal: EndingProposal | None = None
    thread_ops: tuple[ThreadOperation, ...] = ()
    new_facts: tuple[FactCommitPlan, ...] = ()
    phase_after: StoryPhase | None = None
    # Outstanding obligations this segment's prose visibly settles.  Ids
    # must be open in the event-sourced ledger; validated before simulate.
    resolved_obligation_ids: tuple[str, ...] = ()
    # Beat Map beats this segment performs.  Each must exist in the pack and
    # not be completed yet; the simulator marks them BeatCompleted.
    beat_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_segment(self) -> SegmentPlan:
        if len(self.scenes) < 1:
            raise ValueError("segment must have at least 1 scene")
        for i, scene in enumerate(self.scenes[:-1]):
            if scene.terminal != "continue":
                raise ValueError(f"non-last scene at index {i} must have terminal='continue'")
        if self.terminal == "ending" and self.ending_proposal is None:
            raise ValueError("ending_proposal is required when terminal is 'ending'")
        return self


class SegmentDraft(RuntimeModel):
    segment_id: str
    scene_drafts: tuple[SceneDraft, ...] = Field(min_length=1)
    choices: tuple[WrittenChoice, ...] = ()
    ending: EndingDraft | None = None
    # Canon Ledger registration for narrative details this prose establishes
    # (entity attributes, prose promises, motifs).  Validated against the
    # committed ledger before simulate; conflicts fail with the old value.
    ledger_updates: tuple[LedgerUpdate, ...] = ()


# ---------------------------------------------------------------------------
# Pacing envelope (deterministic budget computation)
# ---------------------------------------------------------------------------


class PacingEnvelope(RuntimeModel):
    phase: StoryPhase
    scene_count: int
    min_scenes: int
    max_scenes: int
    reserved_resolution_scenes: int
    remaining_budget: int
    can_end: bool
    must_end: bool
    in_convergence: bool
    max_new_threads: int
    quiet_scene_allowance: int
    target_block_range: tuple[int, int]


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


class GuardViolation(RuntimeModel):
    kind: Literal[
        "knowledge_leak",
        "contradiction",
        "unauthorized_fact",
        "wrong_speaker",
        "unsupported_certainty",
    ]
    block_index: int | None = None
    character_id: str | None = None
    detail: str


class GuardResult(RuntimeModel):
    passed: bool
    violations: tuple[GuardViolation, ...] = ()


# ---------------------------------------------------------------------------
# Completion judgment
# ---------------------------------------------------------------------------


class CompletionAssessment(RuntimeModel):
    requirement_id: str
    satisfied: bool
    cited_event_ids: tuple[str, ...] = ()
    rationale: str


class CompletionResult(RuntimeModel):
    assessments: tuple[CompletionAssessment, ...]
    cleared: bool


# ---------------------------------------------------------------------------
# Agent ports (Plan 3 provides real implementations; Plan 2 uses fakes)
# ---------------------------------------------------------------------------


class DirectorPort(Protocol):
    async def plan_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        pacing: PacingEnvelope,
    ) -> SegmentPlan: ...


class SegmentWriterPort(Protocol):
    async def write_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        *,
        pending_choice: PresentedChoice | None = None,
    ) -> SegmentDraft: ...


class GuardPort(Protocol):
    def check_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        draft: SegmentDraft,
    ) -> GuardResult: ...
