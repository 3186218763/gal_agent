"""Contracts for segment-based runtime: plans, drafts, agent ports, guard, completion."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field

from src.story.runtime.contracts import (
    EndingDraft,
    FactCommitPlan,
    RuntimeModel,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState, StoryPhase

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
    scenes: tuple[ScenePlan, ...] = Field(min_length=1)
    terminal: Literal["decision", "ending"]
    ending_proposal: EndingProposal | None = None
    thread_ops: tuple[ThreadOperation, ...] = ()
    new_facts: tuple[FactCommitPlan, ...] = ()
    phase_after: StoryPhase | None = None


class SegmentDraft(RuntimeModel):
    segment_id: str
    scene_drafts: tuple[SceneDraft, ...] = Field(min_length=1)
    choices: tuple[WrittenChoice, ...] = ()
    ending: EndingDraft | None = None


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
    ) -> SegmentPlan:
        ...


class SegmentWriterPort(Protocol):
    async def write_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
    ) -> SegmentDraft:
        ...


class GuardPort(Protocol):
    def check_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        draft: SegmentDraft,
    ) -> GuardResult:
        ...
