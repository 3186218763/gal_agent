"""Consolidated test fakes for runtime agents.

Used by test_v2_api.py, test_turns_api.py, test_turn_orchestrator.py,
and test_story_cli_live.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    EndingDraft,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.segment_contracts import (
    EndingProposal,
    GuardResult,
    GuardViolation,
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
)
from src.story.runtime.semantic_judge import JudgeFindings
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import NarrativeBlock, SessionState


class FakePlanner:
    """Fake PlannerPort that returns a deterministic decision plan."""

    async def plan_scene(self, pack: CompiledScriptPack, state: SessionState) -> ScenePlan:
        return valid_decision_plan()

    async def resolve_action(
        self, pack: CompiledScriptPack, state: SessionState, choice
    ) -> ActionResolution:
        return ActionResolution(action_id=choice.action_id, outcome="success")


class FakeWriter:
    """Fake WriterPort that returns a deterministic scene draft."""

    async def write_scene(
        self, pack: CompiledScriptPack, state: SessionState, plan: ScenePlan
    ) -> SceneDraft:
        return valid_scene_draft(plan)

    async def write_ending(self, pack, state, ending) -> EndingDraft:
        return EndingDraft(
            ending_id=ending.id,
            title=ending.title,
            blocks=(NarrativeBlock(kind="narration", text=f"Ending: {ending.title}"),),
        )


class FakeStreamingGenerator:
    """Fake StreamingGeneratorPort that yields predetermined blocks + complete."""

    def __init__(
        self,
        blocks: list[dict[str, Any]] | None = None,
        complete: dict[str, Any] | None = None,
    ) -> None:
        self._blocks = (
            blocks
            if blocks is not None
            else [
                {"kind": "narration", "text": "The cafe hums quietly."},
                {"kind": "dialogue", "character_id": "alice", "text": "You came back."},
            ]
        )
        self._complete = complete or {
            "scene_id": "scene_stream_1",
            "terminal": "decision",
            "decision_id": "dec_1",
            "choices": [
                {
                    "option_id": "ask",
                    "action_id": "ask",
                    "label": "Ask about the notebook",
                    "intent": "direct question",
                },
                {
                    "option_id": "observe",
                    "action_id": "observe",
                    "label": "Watch quietly",
                    "intent": "patient observation",
                },
            ],
        }

    async def generate_scene(
        self, pack: CompiledScriptPack, state: SessionState
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        for block in self._blocks:
            yield ("block", block)
        yield ("complete", self._complete)


# --- Shared plan/draft factories ---


def valid_decision_plan() -> ScenePlan:
    return ScenePlan(
        scene_id="scene_01",
        summary="Alice waits for the protagonist to choose.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="decision_01",
        choices=(
            ChoicePlan(option_id="ask", action_id="ask", intent="ask directly"),
            ChoicePlan(option_id="observe", action_id="observe", intent="watch carefully"),
        ),
    )


def valid_continue_plan() -> ScenePlan:
    return ScenePlan(
        scene_id="scene_continue_01",
        summary="Alice shows the protagonist around the cafe.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="continue",
    )


def valid_scene_draft(plan: ScenePlan) -> SceneDraft:
    return SceneDraft(
        scene_id=plan.scene_id,
        blocks=(NarrativeBlock(kind="narration", text="The cafe hums quietly."),),
        choices=tuple(
            WrittenChoice(option_id=item.option_id, label=item.intent[:80]) for item in plan.choices
        ),
    )


def valid_ending_draft(ending) -> EndingDraft:
    return EndingDraft(
        ending_id=ending.id,
        title=ending.title,
        blocks=(NarrativeBlock(kind="narration", text=f"Ending: {ending.title}"),),
    )


# ---------------------------------------------------------------------------
# Segment-engine fakes (Plan 2)
# ---------------------------------------------------------------------------


def budget_test_pack_dict() -> dict[str, Any]:
    """Minimal v1.0 pack dict with adjusted scene budgets for testing.

    Uses the existing v1 schema but with min/max scene budget suitable for
    multi-scene segment testing. For real v2.0 packs with completion_requirements,
    import ``minimal_pack_v2_dict()`` from Plan 1's ``story_factories.py``.

    For backward compatibility with v1.0 packs, use:
        getattr(pack.source, "completion_requirements", ())
    """
    from tests.story_factories import minimal_script_pack_dict

    raw = minimal_script_pack_dict()
    raw["experience"]["min_scenes"] = 4
    raw["experience"]["max_scenes"] = 12
    raw["experience"]["reserved_resolution_scenes"] = 2
    # Adjust fallback ending threshold to match reduced max_scenes.
    for ending in raw["endings"]:
        if ending["type"] == "fallback":
            ending["eligibility"]["all"] = ["session.scene_count >= 11"]
    return raw


class FakeDirector:
    """Returns a canned SegmentPlan. Produces decision segments until
    pacing.must_end, then produces an ending segment."""

    def __init__(self) -> None:
        self._call_count = 0

    async def plan_segment(
        self,
        pack: Any,
        state: Any,
        pacing: PacingEnvelope,
    ) -> SegmentPlan:
        self._call_count += 1
        segment_id = f"seg_{state.session_id}_{self._call_count}"

        if pacing.must_end:
            return SegmentPlan(
                segment_id=segment_id,
                scenes=(
                    ScenePlan(
                        scene_id=f"scene_{segment_id}_ending",
                        summary="The final scene",
                        location_id=state.world.location_id,
                        present_character_ids=state.world.present_character_ids,
                        terminal="ending",
                    ),
                ),
                terminal="ending",
                ending_proposal=EndingProposal(
                    title="An Ending",
                    tone="reflective",
                    terminal_state_summary="The story concludes.",
                ),
            )

        return SegmentPlan(
            segment_id=segment_id,
            scenes=(
                ScenePlan(
                    scene_id=f"scene_{segment_id}",
                    summary="A scene unfolds",
                    location_id=state.world.location_id,
                    present_character_ids=state.world.present_character_ids,
                    terminal="decision",
                    decision_id=f"dec_{segment_id}",
                    choices=(
                        ChoicePlan(
                            option_id=f"opt_{segment_id}_a", action_id="ask", intent="Ask directly"
                        ),
                        ChoicePlan(
                            option_id=f"opt_{segment_id}_b",
                            action_id="observe",
                            intent="Watch carefully",
                        ),
                    ),
                ),
            ),
            terminal="decision",
        )


class FakeSegmentWriter:
    """Returns canned scene drafts and endings matching a SegmentPlan."""

    async def write_segment(
        self,
        pack: Any,
        state: Any,
        plan: SegmentPlan,
    ) -> SegmentDraft:
        scene_drafts = tuple(
            SceneDraft(
                scene_id=scene.scene_id,
                blocks=(
                    NarrativeBlock(
                        kind="narration",
                        text=f"The story continues in {scene.scene_id}.",
                    ),
                ),
            )
            for scene in plan.scenes
        )

        choices: tuple[WrittenChoice, ...] = ()
        if plan.terminal == "decision":
            last_scene = plan.scenes[-1]
            choices = tuple(
                WrittenChoice(option_id=c.option_id, label=c.intent[:80])
                for c in last_scene.choices
            )

        ending = None
        if plan.terminal == "ending" and plan.ending_proposal is not None:
            ending_id = f"ending_{state.session_id}_{uuid.uuid4().hex[:8]}"
            ending = EndingDraft(
                ending_id=ending_id,
                title=plan.ending_proposal.title,
                blocks=(
                    NarrativeBlock(
                        kind="narration",
                        text=f"{plan.ending_proposal.title}. {plan.ending_proposal.terminal_state_summary}",
                    ),
                ),
                tone=plan.ending_proposal.tone,
                terminal_state_summary=plan.ending_proposal.terminal_state_summary,
            )

        return SegmentDraft(
            segment_id=plan.segment_id,
            scene_drafts=scene_drafts,
            choices=choices,
            ending=ending,
        )


class FakeGuard:
    """Always-pass guard for testing."""

    def check_segment(
        self,
        pack: Any,
        state: Any,
        plan: SegmentPlan,
        draft: SegmentDraft,
    ) -> GuardResult:
        return GuardResult(passed=True)


class FakeSemanticJudge:
    """Always-pass semantic judge; records every call for assertions."""

    def __init__(self, findings: JudgeFindings | None = None) -> None:
        self._findings = findings if findings is not None else JudgeFindings()
        self.calls: list[dict[str, Any]] = []

    async def judge_segment(
        self,
        pack: Any,
        state: Any,
        plan: SegmentPlan,
        draft: SegmentDraft,
        pending_choice=None,
    ) -> JudgeFindings:
        self.calls.append(
            {
                "pack": pack,
                "state": state,
                "plan": plan,
                "draft": draft,
                "pending_choice": pending_choice,
            }
        )
        return self._findings


class DeterministicGuard:
    """Production guard with deterministic checks (per cross-plan resolution section 11)."""

    def check_segment(
        self,
        pack: Any,
        state: Any,
        plan: SegmentPlan,
        draft: SegmentDraft,
    ) -> GuardResult:
        violations: list[GuardViolation] = []

        # Check segment/scene ID consistency between plan and draft
        plan_scene_ids = {s.scene_id for s in plan.scenes}
        draft_scene_ids = {s.scene_id for s in draft.scene_drafts}
        if plan_scene_ids != draft_scene_ids:
            violations.append(
                GuardViolation(
                    kind="contradiction",
                    detail=f"Scene ID mismatch: plan has {plan_scene_ids}, draft has {draft_scene_ids}",
                )
            )

        # Check all speakers in drafts exist in plan's present_character_ids
        all_present_ids: set[str] = set()
        for scene in plan.scenes:
            all_present_ids.update(scene.present_character_ids)
        for i, scene_draft in enumerate(draft.scene_drafts):
            for j, block in enumerate(scene_draft.blocks):
                if block.character_id and block.character_id not in all_present_ids:
                    violations.append(
                        GuardViolation(
                            kind="wrong_speaker",
                            block_index=i,
                            character_id=block.character_id,
                            detail=f"Character {block.character_id} not present in scene",
                        )
                    )

        # Check all choice IDs in draft match plan's choice IDs
        if plan.terminal == "decision" and plan.scenes:
            last_scene = plan.scenes[-1]
            plan_choice_ids = {c.option_id for c in last_scene.choices}
            draft_choice_ids = {c.option_id for c in draft.choices}
            if plan_choice_ids != draft_choice_ids:
                violations.append(
                    GuardViolation(
                        kind="contradiction",
                        detail=f"Choice ID mismatch: plan {plan_choice_ids}, draft {draft_choice_ids}",
                    )
                )

        # Check narration blocks have no character_id
        for i, scene_draft in enumerate(draft.scene_drafts):
            for j, block in enumerate(scene_draft.blocks):
                if block.kind == "narration" and block.character_id:
                    violations.append(
                        GuardViolation(
                            kind="wrong_speaker",
                            block_index=i,
                            detail="Narration block has character_id",
                        )
                    )

        # Check scene count does not exceed max_scenes
        if len(plan.scenes) > state.world.max_scenes:
            violations.append(
                GuardViolation(
                    kind="contradiction",
                    detail=f"Scene count {len(plan.scenes)} exceeds max_scenes {state.world.max_scenes}",
                )
            )

        return GuardResult(passed=len(violations) == 0, violations=tuple(violations))
