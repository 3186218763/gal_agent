"""Consolidated test fakes for runtime agents.

Used by test_runtime_service.py, test_streaming_api.py, test_v2_api.py,
and test_story_cli_live.py.
"""

from __future__ import annotations

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
        self._blocks = blocks if blocks is not None else [
            {"kind": "narration", "text": "The cafe hums quietly."},
            {"kind": "dialogue", "character_id": "alice", "text": "You came back."},
        ]
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
            WrittenChoice(option_id=item.option_id, label=item.intent[:80])
            for item in plan.choices
        ),
    )


def valid_ending_draft(ending) -> EndingDraft:
    return EndingDraft(
        ending_id=ending.id,
        title=ending.title,
        blocks=(NarrativeBlock(kind="narration", text=f"Ending: {ending.title}"),),
    )
