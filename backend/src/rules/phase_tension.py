# backend/src/rules/phase_tension.py
from __future__ import annotations
from typing import List, Optional, TYPE_CHECKING
from src.domain.enums import Phase

if TYPE_CHECKING:
    from src.domain.world_state import WorldState
    from src.domain.setting_pack import SettingPack

PHASE_ORDER = [Phase.SETUP, Phase.RISING, Phase.CLIMAX, Phase.FALLING]

SETUP_TO_RISING_STEPS = 3
RISING_TO_CLIMAX_STEPS = 10
CLIMAX_TO_FALLING_STEPS = 16

TAG_DELTA = {
    "confrontation": 2,
    "reveal": 1,
    "calm": -1,
}


def update_tension(
    current: int,
    suggested_delta: int,
    event_tags: List[str],
    phase: Phase,
) -> int:
    delta = max(-2, min(2, suggested_delta))
    for t in event_tags:
        delta += TAG_DELTA.get(t, 0)
    value = current + delta
    if phase == Phase.CLIMAX:
        value = max(value, 6)
    return max(1, min(10, value))


def clamp_phase_hint(current: Phase, hint: Optional[str]) -> Phase:
    if not hint:
        return current
    try:
        target = Phase(hint)
    except ValueError:
        return current
    ci = PHASE_ORDER.index(current)
    ti = PHASE_ORDER.index(target)
    if ti <= ci:
        return current
    return PHASE_ORDER[min(ci + 1, ti)]


def maybe_advance_phase(
    state: "WorldState",
    pack: Optional["SettingPack"] = None,
    major_choice: bool = False,
) -> Phase:
    phase = state.phase
    max_progress = max((g.progress for g in state.goal_progress.values()), default=0.0)
    any_completed = any(g.status.value == "completed" for g in state.goal_progress.values())

    if phase == Phase.SETUP and (
        state.steps >= SETUP_TO_RISING_STEPS or max_progress >= 0.2 or state.tension >= 5
    ):
        return Phase.RISING
    if phase == Phase.RISING and (
        state.steps >= RISING_TO_CLIMAX_STEPS or max_progress >= 0.6 or state.tension >= 8
    ):
        return Phase.CLIMAX
    if phase == Phase.CLIMAX and (
        state.steps >= CLIMAX_TO_FALLING_STEPS or major_choice or any_completed
    ):
        return Phase.FALLING
    return phase
