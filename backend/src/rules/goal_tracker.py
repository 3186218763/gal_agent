# backend/src/rules/goal_tracker.py
from __future__ import annotations

from typing import List

from src.domain.enums import GoalStatus
from src.domain.options import GoalEffect, PredictedConsequences
from src.domain.setting_pack import SettingPack
from src.domain.world_state import WorldState


def _clamp_rel(value: int) -> int:
    return max(0, min(100, value))


def apply_goal_effects(
    state: WorldState,
    pack: SettingPack,
    effects: List[GoalEffect],
) -> WorldState:
    """Apply goal progress deltas; return an updated WorldState copy."""
    new_state = state.model_copy(deep=True)
    valid_ids = {g.id for g in pack.goals}

    for effect in effects:
        if effect.goal_id not in new_state.goal_progress:
            if effect.goal_id not in valid_ids:
                continue
            # Unknown at runtime but in pack — skip if absent
            continue

        gr = new_state.goal_progress[effect.goal_id]
        p = min(1.0, max(0.0, gr.progress + effect.delta_progress))

        if effect.force_complete or p >= 1.0:
            gr.progress = 1.0
            gr.status = GoalStatus.COMPLETED
        else:
            gr.progress = p

    return new_state


def apply_consequences(
    state: WorldState,
    pack: SettingPack,
    consequences: PredictedConsequences,
) -> WorldState:
    """Apply flags, relationship deltas, and goal effects; return updated copy."""
    new_state = state.model_copy(deep=True)

    for key, value in consequences.flag_changes.items():
        new_state.flags[key] = value

    for char_id, deltas in consequences.relationship_deltas.items():
        if char_id not in new_state.relationships:
            continue
        rel = new_state.relationships[char_id]
        if "trust" in deltas:
            rel.trust = _clamp_rel(rel.trust + int(deltas["trust"]))
        if "romance" in deltas:
            rel.romance = _clamp_rel(rel.romance + int(deltas["romance"]))

    if consequences.tension_delta:
        new_state.tension = max(1, min(10, new_state.tension + consequences.tension_delta))

    return apply_goal_effects(new_state, pack, list(consequences.goal_effects))
