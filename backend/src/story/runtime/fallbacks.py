"""Deterministic fallbacks for Planner/Writer failures."""

from __future__ import annotations

from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    EndingDraft,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.script_pack.models import CompiledScriptPack, EndingSource
from src.story.state import NarrativeBlock, PresentedChoice, SessionState


def fallback_scene_plan(pack: CompiledScriptPack, state: SessionState) -> ScenePlan:
    actions = sorted(pack.action_ids & set(pack.source.protagonist.capabilities))
    decision = len(actions) >= 2
    return ScenePlan(
        scene_id=f"fallback_scene_{state.world.scene_count + 1}",
        summary="The protagonist pauses and chooses a safe next action.",
        location_id=state.world.location_id,
        present_character_ids=state.world.present_character_ids,
        terminal="decision" if decision else "continue",
        decision_id=f"fallback_decision_{state.revision + 1}" if decision else None,
        choices=tuple(
            ChoicePlan(option_id=f"fallback_{action}", action_id=action, intent=action)
            for action in actions[:2]
        ),
    )


def fallback_resolution(choice: PresentedChoice) -> ActionResolution:
    return ActionResolution(action_id=choice.action_id, outcome="partial")


def fallback_scene_draft(plan: ScenePlan) -> SceneDraft:
    return SceneDraft(
        scene_id=plan.scene_id,
        blocks=(NarrativeBlock(kind="narration", text="片刻沉默后，故事继续向前。"),),
        choices=tuple(
            WrittenChoice(option_id=item.option_id, label=item.intent[:80]) for item in plan.choices
        ),
    )


def fallback_ending_draft(ending: EndingSource) -> EndingDraft:
    text = " ".join((ending.title, *ending.required_outcomes))
    return EndingDraft(
        ending_id=ending.id,
        title=ending.title,
        blocks=(NarrativeBlock(kind="narration", text=text[:4000]),),
    )
