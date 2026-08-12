"""Deterministic pacing envelope and ending policy computation."""

from __future__ import annotations

from src.story.runtime.segment_contracts import PacingEnvelope
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState

# Default open-thread budget before convergence window.
DEFAULT_MAX_OPEN_THREADS = 3


def compute_pacing_envelope(
    state: SessionState,
    pack: CompiledScriptPack,
) -> PacingEnvelope:
    scene_count = state.world.scene_count
    min_scenes = pack.source.experience.min_scenes
    max_scenes = state.world.max_scenes
    reserved = state.world.reserved_resolution_scenes
    remaining = max_scenes - scene_count
    convergence_start = max_scenes - reserved

    in_convergence = scene_count >= convergence_start

    return PacingEnvelope(
        phase=state.world.phase,
        scene_count=scene_count,
        min_scenes=min_scenes,
        max_scenes=max_scenes,
        reserved_resolution_scenes=reserved,
        remaining_budget=remaining,
        can_end=scene_count >= min_scenes,
        must_end=scene_count >= max_scenes,
        in_convergence=in_convergence,
        max_new_threads=0 if in_convergence else DEFAULT_MAX_OPEN_THREADS,
        quiet_scene_allowance=max(0, min(2, remaining // 4)),
    )
