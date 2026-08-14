"""Debug: run pipeline once and print the exact blocks the guard flags."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.live.conftest import load_live_environment

load_live_environment()

from src.story.runtime.config import OpenCodeGoSettings
from src.story.runtime.contracts import PacingEnvelope
from src.story.runtime.director import SdkDirector
from src.story.runtime.guard import Guard
from src.story.runtime.model import build_model_bundle
from src.story.runtime.segment_writer import SdkSegmentWriter
from src.story.script_pack import compile_script_pack
from src.story.state import StoryPhase, initial_session_state


async def main() -> None:
    settings = OpenCodeGoSettings.from_env()
    bundle = build_model_bundle(settings)
    pack = compile_script_pack(Path("script_packs/cafe_mystery"))
    state = initial_session_state(pack, "debug-pipeline", session_seed=99)
    pacing = PacingEnvelope(
        phase=StoryPhase.OPENING,
        scene_count=0,
        min_scenes=pack.source.experience.min_scenes,
        max_scenes=pack.source.experience.max_scenes,
        reserved_resolution_scenes=pack.source.experience.reserved_resolution_scenes,
        remaining_budget=pack.source.experience.max_scenes,
        can_end=False,
        must_end=False,
        in_convergence=False,
        max_new_threads=2,
        quiet_scene_allowance=1,
    )
    director = SdkDirector(bundle.model)
    plan = await director.plan_segment(pack, state, pacing)
    print("PLAN terminal:", plan.terminal)
    print("PLAN scenes:", [(s.scene_id, s.terminal, s.present_character_ids) for s in plan.scenes])
    writer = SdkSegmentWriter(bundle.model)
    draft = await writer.write_segment(pack, state, plan)
    guard = Guard()
    result = guard.check_segment(pack, state, plan, draft)
    print("GUARD passed:", result.passed)
    if not result.passed:
        flagged = {v.block_index for v in result.violations}
        print("flagged block indices:", sorted(flagged))
        global_idx = 0
        for i, scene in enumerate(draft.scene_drafts):
            for j, block in enumerate(scene.blocks):
                marker = " <-- FLAGGED" if global_idx in flagged else ""
                print(f"scene[{i}].block[{global_idx}] {block.kind} {block.character_id}: {block.text!r}{marker}")
                global_idx += 1
    print("CHOICES:", [(c.option_id, c.label) for c in draft.choices])


asyncio.run(main())
