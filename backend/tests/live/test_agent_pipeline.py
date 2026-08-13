"""Live capability test: full segment pipeline Director -> Writer -> Guard.

Skipped unless RUN_LIVE_ZEN_TEST=1. Requires GAL_LLM_PROVIDER=opencode_go
and OPENCODE_GO_API_KEY. Calls the real DeepSeek model via Responses API.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.story.runtime.config import OpenCodeGoSettings
from src.story.runtime.contracts import (
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
)
from src.story.runtime.director import SdkDirector
from src.story.runtime.guard import Guard
from src.story.runtime.model import build_model_bundle
from src.story.runtime.segment_contracts import GuardResult
from src.story.runtime.segment_writer import SdkSegmentWriter
from src.story.script_pack import compile_script_pack
from src.story.state import StoryPhase, initial_session_state

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_segment_pipeline_director_writer_guard_roundtrip():
    """Full Director -> Writer -> Guard round-trip with real model."""
    if os.getenv("RUN_LIVE_ZEN_TEST") != "1":
        pytest.skip("set RUN_LIVE_ZEN_TEST=1 to run provider tests")

    settings = OpenCodeGoSettings.from_env()
    assert settings.api == "responses"
    bundle = build_model_bundle(settings)

    pack = compile_script_pack(Path("script_packs/cafe_mystery"))
    state = initial_session_state(pack, "live-pipeline-test", session_seed=99)

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
        target_block_range=(8, 25),
    )

    # 1. Director produces a SegmentPlan
    director = SdkDirector(bundle.model)
    plan = await director.plan_segment(pack, state, pacing)
    assert isinstance(plan, SegmentPlan)
    assert len(plan.scenes) >= 1
    assert plan.terminal in ("decision", "ending")

    # Verify structural validity of the plan
    last_scene = plan.scenes[-1]
    if plan.terminal == "decision":
        assert last_scene.terminal == "decision"
        assert 2 <= len(last_scene.choices) <= 4
    elif plan.terminal == "ending":
        assert plan.ending_proposal is not None
        assert plan.ending_proposal.title

    # Verify all scene locations exist in the pack
    source = pack.source
    locations = (
        source.world_setting.locations
        if hasattr(source, "world_setting")
        else source.world.locations
    )
    location_ids = {loc.id for loc in locations}
    for scene in plan.scenes:
        assert (
            scene.location_id in location_ids
        ), f"Director proposed unknown location: {scene.location_id}"

    # Verify all present characters exist
    for scene in plan.scenes:
        for char_id in scene.present_character_ids:
            assert char_id in pack.character_ids, f"Director proposed unknown character: {char_id}"

    # 2. Writer produces a SegmentDraft
    writer = SdkSegmentWriter(bundle.model)
    draft = await writer.write_segment(pack, state, plan)
    assert isinstance(draft, SegmentDraft)
    assert draft.segment_id == plan.segment_id
    assert len(draft.scene_drafts) == len(plan.scenes)

    # Verify each scene draft has non-empty blocks
    for scene_draft in draft.scene_drafts:
        assert len(scene_draft.blocks) >= 1
        for block in scene_draft.blocks:
            assert block.text.strip()

    # 3. Guard validates the segment
    guard = Guard()
    result = guard.check_segment(pack, state, plan, draft)
    assert isinstance(result, GuardResult)

    if not result.passed:
        # If guard found violations, they should be typed and detailed
        for v in result.violations:
            assert v.kind in (
                "knowledge_leak",
                "contradiction",
                "unauthorized_fact",
                "wrong_speaker",
                "unsupported_certainty",
            )
            assert v.detail
        # Print violations for debugging (not sensitive data)
        violation_summary = "; ".join(
            f"{v.kind}@block{v.block_index}: {v.detail}" for v in result.violations
        )
        pytest.fail(f"Guard rejected live segment: {violation_summary}")

    # 4. Verify choice consistency for decision segments
    if plan.terminal == "decision":
        planned_ids = {c.option_id for c in plan.scenes[-1].choices}
        draft_ids = {c.option_id for c in draft.choices}
        assert (
            draft_ids == planned_ids
        ), f"Writer choice IDs don't match plan: {draft_ids} vs {planned_ids}"
        # Labels must be unique
        labels = [c.label.strip().casefold() for c in draft.choices]
        assert len(labels) == len(set(labels)), "Choice labels must be unique"

    # 5. Verify ending for ending segments
    if plan.terminal == "ending":
        assert draft.ending is not None
        assert draft.ending.title == plan.ending_proposal.title
        assert len(draft.ending.blocks) >= 1

    # 6. Guard must not have mutated state
    assert state.revision == 0  # state was not changed
