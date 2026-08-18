"""Live capability test for the EveryGPT Chat Completions runtime (Gemini).

Skipped unless RUN_LIVE_ZEN_TEST=1. Requires GAL_LLM_PROVIDER=everygpt
and EVERYGPT_API_KEY (or OPENAI_API_KEY alias). Calls the real
gemini-3.7-flash model via the OpenAI-compatible Chat Completions API.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.story.runtime.config import LLMSettings
from src.story.runtime.contracts import (
    PacingEnvelope,
)
from src.story.runtime.director import LLMDirector
from src.story.runtime.guard import Guard
from src.story.runtime.model import LLMClient
from src.story.runtime.segment_writer import LLMSegmentWriter
from src.story.script_pack import compile_script_pack
from src.story.state import StoryPhase, initial_session_state

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_gemini_chat_completions_runs_segment_roundtrip():
    """Director -> Writer -> Guard round-trip with the real Gemini model."""
    if os.getenv("RUN_LIVE_ZEN_TEST") != "1":
        pytest.skip("set RUN_LIVE_ZEN_TEST=1 to run provider tests")
    settings = LLMSettings.from_env()
    assert settings.api == "chat_completions"
    client = LLMClient(settings)
    assert client.api == "chat_completions"
    assert client.model == "gemini-3.7-flash"

    pack = compile_script_pack(Path("script_packs/yokai_after_school"))
    state = initial_session_state(pack, "live-everygpt", 21)
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

    director = LLMDirector(client)
    plan = await director.plan_segment(pack, state, pacing)
    assert plan.terminal in {"decision", "ending"}

    writer = LLMSegmentWriter(client)
    draft = await writer.write_segment(pack, state, plan)
    assert draft.segment_id == plan.segment_id

    guard_result = Guard().check_segment(pack, state, plan, draft)
    assert guard_result.passed
