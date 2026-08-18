"""Tests for API startup opening-cache warmup.

Verifies that ``_warmup_opening_caches`` generates missing openings and
skips existing ones, and that the lifespan integrates with FastAPI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.story.api import AppDependencies, ScriptPackRegistry, _warmup_opening_caches
from src.story.runtime.contracts import ChoicePlan, WrittenChoice
from src.story.runtime.guard import Guard
from src.story.runtime.pack_cache import CachedOpening, PackCache
from src.story.runtime.segment_contracts import (
    PacingEnvelope,
    SceneDraft,
    ScenePlan,
    SegmentDraft,
    SegmentPlan,
)
from src.story.state import NarrativeBlock, StoryPhase
from src.story.state.events import PhaseAdvanced
from src.story.storage import StoryEventStore

# ---------------------------------------------------------------------------
# Helpers — a fake opening agent that returns a valid plan+draft
# ---------------------------------------------------------------------------


class _FakeOpeningAgent:
    """Records calls and returns a minimal valid segment output."""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, pack: Any, state: Any, pacing: Any) -> Any:
        from src.story.runtime.unified_segment import UnifiedSegmentOutput

        self.call_count += 1
        plan = SegmentPlan(
            segment_id=f"seg_warmup_{self.call_count}",
            scenes=(
                ScenePlan(
                    scene_id="scene_warmup",
                    summary="warmup scene",
                    location_id="classroom_2b",
                    present_character_ids=("hiyori",),
                    terminal="decision",
                    decision_id="dec_warmup",
                    choices=(
                        ChoicePlan(option_id="ask", action_id="ask", intent="ask"),
                        ChoicePlan(option_id="observe", action_id="observe", intent="observe"),
                    ),
                ),
            ),
            terminal="decision",
        )
        floor = pacing.target_block_range[0] if pacing.target_block_range else 1
        draft = SegmentDraft(
            segment_id=plan.segment_id,
            scene_drafts=(
                SceneDraft(
                    scene_id="scene_warmup",
                    blocks=tuple(
                        NarrativeBlock(kind="narration", text=f"Warmup beat {i}.")
                        for i in range(max(1, floor))
                    ),
                ),
            ),
            choices=(
                WrittenChoice(option_id="ask", label="Ask"),
                WrittenChoice(option_id="observe", label="Observe"),
            ),
        )
        return UnifiedSegmentOutput(segment_plan=plan, segment_draft=draft)


def _make_deps(
    tmp_path: Path,
    *,
    pack_cache: PackCache | None = None,
    agent_factory: Any | None = None,
) -> AppDependencies:
    registry = ScriptPackRegistry(Path(__file__).parent / ".." / "script_packs")
    store = StoryEventStore(tmp_path / "warmup.db")
    return AppDependencies(
        store=store,
        registry=registry,
        guard=Guard(),
        pack_cache=pack_cache or PackCache(tmp_path / "cache"),
        unified_agent_factory=agent_factory,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWarmupOpeningCaches:
    @pytest.mark.asyncio
    async def test_generates_missing_opening(self, tmp_path: Path):
        """When cache is empty, warmup generates the opening."""
        agent = _FakeOpeningAgent()
        deps = _make_deps(tmp_path, agent_factory=lambda: agent)

        await _warmup_opening_caches(deps)

        pack = deps.registry.get("yokai_after_school")
        assert deps.pack_cache is not None
        assert deps.pack_cache.has_opening(pack.pack_hash)
        assert agent.call_count == 1

    @pytest.mark.asyncio
    async def test_skips_existing_opening(self, tmp_path: Path):
        """When cache already has the opening, warmup does not regenerate."""
        agent = _FakeOpeningAgent()
        deps = _make_deps(tmp_path, agent_factory=lambda: agent)

        # Pre-populate the cache
        pack = deps.registry.get("yokai_after_school")
        pre_existing = CachedOpening(
            segment_plan=SegmentPlan(
                segment_id="pre",
                scenes=(
                    ScenePlan(
                        scene_id="pre_scene",
                        summary="pre",
                        location_id="classroom_2b",
                        present_character_ids=("hiyori",),
                        terminal="decision",
                        decision_id="dec_pre",
                        choices=(
                            ChoicePlan(option_id="ask", action_id="ask", intent="ask"),
                            ChoicePlan(option_id="observe", action_id="observe", intent="observe"),
                        ),
                    ),
                ),
                terminal="decision",
            ),
            segment_draft=SegmentDraft(
                segment_id="pre",
                scene_drafts=(
                    SceneDraft(
                        scene_id="pre_scene",
                        blocks=(NarrativeBlock(kind="narration", text="pre"),),
                    ),
                ),
            ),
            seg_events=(PhaseAdvanced(phase=StoryPhase.EXPLORATION),),
            pacing=PacingEnvelope(
                phase=StoryPhase.OPENING,
                scene_count=0,
                min_scenes=4,
                max_scenes=12,
                reserved_resolution_scenes=2,
                remaining_budget=12,
                can_end=False,
                must_end=False,
                in_convergence=False,
                max_new_threads=3,
                quiet_scene_allowance=2,
                target_block_range=(30, 60),
            ),
        )
        deps.pack_cache.save_opening(pack.pack_hash, pre_existing)

        await _warmup_opening_caches(deps)

        assert agent.call_count == 0

    @pytest.mark.asyncio
    async def test_skips_when_no_agent_factory(self, tmp_path: Path):
        """When no agent factory is configured, warmup is a no-op."""
        deps = _make_deps(tmp_path, agent_factory=None)
        await _warmup_opening_caches(deps)

        pack = deps.registry.get("yokai_after_school")
        assert not deps.pack_cache.has_opening(pack.pack_hash)

    @pytest.mark.asyncio
    async def test_failure_does_not_crash(self, tmp_path: Path):
        """A model failure during warmup is caught and logged."""

        class _FailingAgent:
            async def generate(self, *args):
                raise RuntimeError("model unavailable")

        deps = _make_deps(tmp_path, agent_factory=lambda: _FailingAgent())
        # Should not raise
        await _warmup_opening_caches(deps)

        pack = deps.registry.get("yokai_after_school")
        assert not deps.pack_cache.has_opening(pack.pack_hash)


class TestLifespanIntegration:
    def test_lifespan_warms_cache_without_blocking(self, tmp_path: Path):
        """The server starts immediately; warmup runs in the background."""
        from starlette.testclient import TestClient

        from src.story.api import create_app

        agent = _FakeOpeningAgent()
        deps = _make_deps(tmp_path, agent_factory=lambda: agent)
        app = create_app(deps)

        with TestClient(app) as client:
            # Server is already serving — health check works immediately
            resp = client.get("/health")
            assert resp.status_code == 200

        # After shutdown, the background warmup task has completed
        pack = deps.registry.get("yokai_after_school")
        assert deps.pack_cache.has_opening(pack.pack_hash)
