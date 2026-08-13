"""Deterministic ending eligibility and phase progression."""

from __future__ import annotations

from typing import Any

from src.story.runtime.context import build_condition_context
from src.story.script_pack.models import (
    CompiledScriptPack,
    EndingSource,
    ScriptPackSourceV2,
)
from src.story.state import SessionState, StoryPhase

PHASES = (
    StoryPhase.OPENING,
    StoryPhase.EXPLORATION,
    StoryPhase.ESCALATION,
    StoryPhase.CRISIS,
    StoryPhase.RESOLUTION,
)


def _group(
    pack: CompiledScriptPack,
    ending_id: str,
    name: str,
    count: int,
    context: dict[str, Any],
) -> list[bool]:
    return [
        pack.conditions[f"ending.{ending_id}.{name}.{index}"].evaluate(context)
        for index in range(count)
    ]


def select_ending(pack: CompiledScriptPack, state: SessionState) -> EndingSource | None:
    if isinstance(pack.source, ScriptPackSourceV2):
        raise TypeError("v2.0 packs do not support fixed endings; use the segment engine (Plan 2)")
    context = build_condition_context(state)
    at_max = state.world.scene_count >= state.world.max_scenes
    for ending in sorted(pack.source.endings, key=lambda item: item.priority, reverse=True):
        if ending.type == "fallback" and not at_max:
            continue
        if (
            ending.type != "fallback"
            and state.world.scene_count < pack.source.experience.min_scenes
        ):
            continue
        all_values = _group(pack, ending.id, "all", len(ending.eligibility.all), context)
        any_values = _group(pack, ending.id, "any", len(ending.eligibility.any), context)
        none_values = _group(pack, ending.id, "none", len(ending.eligibility.none), context)
        if all(all_values) and (not any_values or any(any_values)) and not any(none_values):
            return ending
    return None


def next_phase(
    state: SessionState,
    *,
    projected_count: int | None = None,
) -> StoryPhase | None:
    usable = max(1, state.world.max_scenes - state.world.reserved_resolution_scenes)
    count = projected_count if projected_count is not None else state.world.scene_count + 1
    ratio = min(1.0, count / usable)
    if ratio >= 0.70:
        target = StoryPhase.CRISIS
    elif ratio >= 0.45:
        target = StoryPhase.ESCALATION
    elif ratio >= 0.20:
        target = StoryPhase.EXPLORATION
    else:
        target = StoryPhase.OPENING
    current_index = PHASES.index(state.world.phase)
    target_index = PHASES.index(target)
    return PHASES[current_index + 1] if target_index > current_index else None
