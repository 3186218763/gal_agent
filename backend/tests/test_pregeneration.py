"""Tests for PreGenerationManager — in-memory session-level background pre-generation."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.pregeneration import PreGenerationManager
from src.story.runtime.segment_contracts import (
    GuardResult,
    SegmentDraft,
    SegmentPlan,
)
from src.story.runtime.unified_segment import UnifiedSegmentOutput
from src.story.script_pack.compiler import compile_source
from src.story.state import NarrativeBlock, PresentedChoice
from tests.story_factories import minimal_script_pack_dict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pack():
    raw = minimal_script_pack_dict()
    raw["experience"]["min_scenes"] = 4
    raw["experience"]["max_scenes"] = 12
    raw["experience"]["reserved_resolution_scenes"] = 2
    for ending in raw["endings"]:
        if ending["type"] == "fallback":
            ending["eligibility"]["all"] = ["session.scene_count >= 11"]
    return compile_source(raw)


def _make_choice(choice_id: str = "ask") -> PresentedChoice:
    return PresentedChoice(
        id=choice_id,
        action_id="ask",
        label="Ask about it",
        intent="ask directly",
    )


def _make_state_with_decision(pack, session_id: str = "s1", seed: int = 42):
    """Create an initial session state that has a pending decision.

    This simulates the state after a decision segment is committed —
    the orchestrator triggers pre-generation from this state.
    """
    from src.story.state import initial_session_state
    from src.story.state.models import (
        PendingDecisionReference,
        PendingSceneReference,
    )

    state = initial_session_state(pack, session_id, session_seed=seed)
    choices = (
        PresentedChoice(
            id="ask",
            action_id="ask",
            label="Ask about it",
            intent="ask directly",
        ),
        PresentedChoice(
            id="observe",
            action_id="observe",
            label="Watch quietly",
            intent="watch carefully",
        ),
        PresentedChoice(
            id="support",
            action_id="support",
            label="Show support",
            intent="offer comfort",
        ),
    )
    return state.model_copy(
        update={
            "pending_scene": PendingSceneReference(
                scene_id="scene_prev",
                revision=1,
                terminal="decision",
                blocks=(NarrativeBlock(kind="narration", text="A scene."),),
            ),
            "pending_decision": PendingDecisionReference(
                decision_id="dec_prev",
                scene_id="scene_prev",
                revision=1,
                choices=choices,
            ),
        }
    )


class FakeGuard:
    def check_segment(self, *args: Any) -> GuardResult:
        return GuardResult(passed=True)


def _fake_unified_output(segment_id: str = "seg_pregen") -> UnifiedSegmentOutput:
    plan = SegmentPlan(
        segment_id=segment_id,
        scenes=(
            ScenePlan(
                scene_id=f"{segment_id}_s1",
                summary="A scene.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id=f"dec_{segment_id}",
                choices=(
                    ChoicePlan(option_id="opt_a", action_id="ask", intent="Ask"),
                    ChoicePlan(option_id="opt_b", action_id="observe", intent="Observe"),
                ),
            ),
        ),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id=segment_id,
        scene_drafts=(
            SceneDraft(
                scene_id=f"{segment_id}_s1",
                blocks=(NarrativeBlock(kind="narration", text="Something happens."),),
            ),
        ),
        choices=(
            WrittenChoice(option_id="opt_a", label="Ask"),
            WrittenChoice(option_id="opt_b", label="Observe"),
        ),
    )
    return UnifiedSegmentOutput(segment_plan=plan, segment_draft=draft)


def _run_pregen(mgr: PreGenerationManager, session_id: str, state, choices, pack):
    """Run pregenerate_choices and wait for all inner tasks to complete."""

    async def _run():
        await mgr.pregenerate_choices(session_id, state, choices, pack)
        # Wait for all inner tasks.
        for key in list(mgr._tasks):
            task = mgr._tasks.get(key)
            if task is not None:
                await task

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPreGenerationManager:
    def test_try_get_returns_none_when_empty(self):
        mgr = PreGenerationManager(
            planner=AsyncMock(),
            unified_agent=AsyncMock(),
            guard=FakeGuard(),
        )
        assert mgr.try_get("s1", "ask") is None

    def test_pregenerate_then_try_get(self):
        """After pregenerate_choices completes, try_get returns the CachedPregen."""
        pack = _make_pack()
        state = _make_state_with_decision(pack)

        # Build a state that has a pending decision with the choice.
        # We need to manually set pending_decision.
        choices = [_make_choice("ask"), _make_choice("observe")]

        # Mock planner returns a simple resolution.
        mock_planner = AsyncMock()
        mock_planner.resolve_action = AsyncMock(
            return_value=ActionResolution(action_id="ask", outcome="success")
        )

        mock_agent = AsyncMock()
        mock_agent.generate = AsyncMock(return_value=_fake_unified_output())

        mgr = PreGenerationManager(
            planner=mock_planner,
            unified_agent=mock_agent,
            guard=FakeGuard(),
        )

        _run_pregen(mgr, "s1", state, choices, pack)

        result = mgr.try_get("s1", "ask")
        assert result is not None
        assert result.choice_id == "ask"
        assert result.segment_plan.segment_id == "seg_pregen"

        # try_get pops — second call returns None.
        assert mgr.try_get("s1", "ask") is None

    def test_pregenerate_skips_already_cached(self):
        """If a choice is already cached, pregenerate_choices skips it."""
        pack = _make_pack()
        state = _make_state_with_decision(pack)
        choices = [_make_choice("ask")]

        mock_planner = AsyncMock()
        mock_planner.resolve_action = AsyncMock(
            return_value=ActionResolution(action_id="ask", outcome="success")
        )
        mock_agent = AsyncMock()
        mock_agent.generate = AsyncMock(return_value=_fake_unified_output())

        mgr = PreGenerationManager(mock_planner, mock_agent, FakeGuard())

        # First call generates.
        _run_pregen(mgr, "s1", state, choices, pack)
        assert mock_planner.resolve_action.call_count == 1

        # Reset mock to detect second call.
        mock_planner.resolve_action.reset_mock()

        # Pop the cache entry so it's "consumed".
        mgr.try_get("s1", "ask")

        # Re-populate after pop.
        mock_planner.resolve_call_count = 0
        _run_pregen(mgr, "s1", state, choices, pack)
        assert mock_planner.resolve_action.call_count == 1

    def test_pregenerate_failure_does_not_create_cache(self):
        """If the agent raises, no cache entry is created and no exception propagates."""
        pack = _make_pack()
        state = _make_state_with_decision(pack)
        choices = [_make_choice("ask")]

        mock_planner = AsyncMock()
        mock_planner.resolve_action = AsyncMock(
            return_value=ActionResolution(action_id="ask", outcome="success")
        )
        mock_agent = AsyncMock()
        mock_agent.generate = AsyncMock(side_effect=RuntimeError("LLM failed"))

        mgr = PreGenerationManager(mock_planner, mock_agent, FakeGuard())

        # Should not raise.
        _run_pregen(mgr, "s1", state, choices, pack)
        assert mgr.try_get("s1", "ask") is None

    def test_cleanup_session_removes_cache(self):
        """cleanup_session removes all cache entries for a session."""
        pack = _make_pack()
        state = _make_state_with_decision(pack)
        choices = [_make_choice("ask"), _make_choice("observe")]

        mock_planner = AsyncMock()
        mock_planner.resolve_action = AsyncMock(
            return_value=ActionResolution(action_id="ask", outcome="success")
        )
        mock_agent = AsyncMock()
        mock_agent.generate = AsyncMock(return_value=_fake_unified_output())

        mgr = PreGenerationManager(mock_planner, mock_agent, FakeGuard())
        _run_pregen(mgr, "s1", state, choices, pack)

        assert mgr.try_get("s1", "ask") is not None
        # Put it back.
        _run_pregen(mgr, "s1", state, [_make_choice("ask")], pack)

        mgr.cleanup_session("s1")
        assert mgr.try_get("s1", "ask") is None
        assert mgr.try_get("s1", "observe") is None

    def test_await_in_progress_returns_none_when_no_task(self):
        mgr = PreGenerationManager(
            planner=AsyncMock(),
            unified_agent=AsyncMock(),
            guard=FakeGuard(),
        )
        result = asyncio.run(mgr.await_in_progress("s1", "ask"))
        assert result is None

    def test_await_in_progress_waits_for_task(self):
        """await_in_progress waits for a running task and returns result."""
        pack = _make_pack()
        state = _make_state_with_decision(pack)
        choices = [_make_choice("ask")]

        mock_planner = AsyncMock()
        mock_planner.resolve_action = AsyncMock(
            return_value=ActionResolution(action_id="ask", outcome="success")
        )
        mock_agent = AsyncMock()
        mock_agent.generate = AsyncMock(return_value=_fake_unified_output())

        mgr = PreGenerationManager(mock_planner, mock_agent, FakeGuard())

        async def main():
            # Start pre-generation without awaiting.
            task = asyncio.create_task(mgr.pregenerate_choices("s1", state, choices, pack))
            # Give it a moment to register the inner tasks.
            await asyncio.sleep(0.05)
            # Now await_in_progress should wait for completion.
            result = await mgr.await_in_progress("s1", "ask", timeout=10.0)
            await task
            assert result is not None
            assert result.choice_id == "ask"

        asyncio.run(main())

    def test_multiple_choices_pre_generated(self):
        """All choices are pre-generated and available in cache."""
        pack = _make_pack()
        state = _make_state_with_decision(pack)
        choices = [_make_choice("ask"), _make_choice("observe"), _make_choice("support")]

        mock_planner = AsyncMock()
        mock_planner.resolve_action = AsyncMock(
            return_value=ActionResolution(action_id="ask", outcome="success")
        )
        mock_agent = AsyncMock()
        mock_agent.generate = AsyncMock(return_value=_fake_unified_output())

        mgr = PreGenerationManager(mock_planner, mock_agent, FakeGuard())
        _run_pregen(mgr, "s1", state, choices, pack)

        for c in choices:
            assert mgr.try_get("s1", c.id) is not None
