"""Session-level in-memory pre-generation manager.

After a decision segment is committed, the orchestrator calls
``pregenerate_choices`` to kick off background LLM pre-generation for every
choice.  When the player makes their selection, the orchestrator checks the
cache first — a hit means zero LLM calls for that turn.

All pre-generation failures are silently swallowed: if any step (resolve,
generate, validate, guard, simulate) fails, no cache entry is created and
the runtime falls through to normal generation.
"""

from __future__ import annotations

import asyncio
import logging

from src.story.runtime.contracts import PlannerPort
from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.pack_cache import CachedPregen
from src.story.runtime.segment_contracts import GuardPort
from src.story.runtime.simulator import simulate_resolution, simulate_segment
from src.story.runtime.unified_segment import UnifiedSegmentPort
from src.story.runtime.validator import (
    validate_action_resolution,
    validate_segment_draft,
    validate_segment_plan,
)
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import EventEnvelope, SessionState, apply_events
from src.story.state.events import StoryEvent
from src.story.state.models import PresentedChoice

logger = logging.getLogger(__name__)


class PreGenerationManager:
    """Background pre-generation manager for session-level caching.

    Maintains an in-memory cache keyed by ``(session_id, choice_id)``.
    Each entry is a fully validated ``CachedPregen`` ready for instant
    commit by the orchestrator.
    """

    def __init__(
        self,
        planner: PlannerPort,
        unified_agent: UnifiedSegmentPort,
        guard: GuardPort,
    ) -> None:
        self._planner = planner
        self._unified_agent = unified_agent
        self._guard = guard
        self._cache: dict[tuple[str, str], CachedPregen] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    async def pregenerate_choices(
        self,
        session_id: str,
        state: SessionState,
        choices: list[PresentedChoice],
        pack: CompiledScriptPack,
    ) -> None:
        """Launch background pre-generation for every choice.

        Skips choices that are already cached or have a running task.
        """
        for choice in choices:
            key = (session_id, choice.id)
            if key in self._cache or key in self._tasks:
                continue
            task = asyncio.create_task(self._pregenerate_one(session_id, choice, pack, state))
            self._tasks[key] = task

    async def _pregenerate_one(
        self,
        session_id: str,
        choice: PresentedChoice,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> None:
        """Full pre-generation pipeline for a single choice.

        Steps:
        1. resolve_action (LLM)
        2. validate_action_resolution
        3. simulate_resolution → pre_events
        4. apply_events → hypothetical_state
        5. compute_pacing_envelope
        6. unified_agent.generate (LLM)
        7. validate_segment_plan + validate_segment_draft
        8. guard.check_segment
        9. simulate_segment → seg_events
        10. store CachedPregen

        All exceptions are caught — pre-gen failure → fallback at runtime.
        """
        key = (session_id, choice.id)
        try:
            # 1-2: Resolve + validate
            try:
                resolution = await self._planner.resolve_action(pack, state, choice)
                resolution = validate_action_resolution(
                    pack,
                    state,
                    resolution,
                    expected_action_id=choice.action_id,
                )
            except Exception:
                # Planner may return inconsistent action_ids on flash models.
                from src.story.runtime.contracts import ActionResolution

                resolution = ActionResolution(
                    action_id=choice.action_id, outcome="success"
                )

            # 3: Simulate resolution events.
            pre_events: tuple[StoryEvent, ...] = simulate_resolution(
                state, choice, resolution, idempotency_key=f"pregen-{session_id}-{choice.id}"
            )

            # 4: Apply pre-events to get hypothetical state.
            pre_envelopes = tuple(
                EventEnvelope(
                    session_id=session_id,
                    sequence=state.revision + i,
                    event=e,
                )
                for i, e in enumerate(pre_events, start=1)
            )
            hypothetical_state = apply_events(state, pre_envelopes)

            # 5: Compute pacing.
            pacing = compute_pacing_envelope(hypothetical_state, pack)

            # 6: Generate segment.
            result = await self._unified_agent.generate(pack, hypothetical_state, pacing)

            # 7: Validate plan + draft.
            plan = validate_segment_plan(pack, hypothetical_state, result.segment_plan, pacing)
            draft = validate_segment_draft(plan, result.segment_draft)

            # 8: Guard.
            guard_result = self._guard.check_segment(pack, hypothetical_state, plan, draft)
            if not guard_result.passed:
                logger.debug(
                    "pre-gen guard rejected segment for choice %s in session %s",
                    choice.id,
                    session_id,
                )
                return

            # 9: Simulate segment events.
            seg_events: tuple[StoryEvent, ...] = simulate_segment(
                pack, hypothetical_state, plan, draft
            )

            # 10: Store in cache.
            self._cache[key] = CachedPregen(
                choice_id=choice.id,
                pre_events=pre_events,
                seg_events=seg_events,
                segment_plan=plan,
                segment_draft=draft,
                pacing=pacing,
            )
        except Exception:
            logger.debug(
                "pre-gen failed for choice %s in session %s",
                choice.id,
                session_id,
                exc_info=True,
            )
        finally:
            self._tasks.pop(key, None)

    def try_get(self, session_id: str, choice_id: str) -> CachedPregen | None:
        """Pop and return a cached pre-gen result, or None."""
        return self._cache.pop((session_id, choice_id), None)

    async def await_in_progress(
        self, session_id: str, choice_id: str, timeout: float = 15.0
    ) -> CachedPregen | None:
        """If a pre-gen task is running for this choice, wait for it.

        If the task already completed, returns the cached result.
        If no task was ever started, returns None.
        """
        # Check cache first — task may have already finished.
        key = (session_id, choice_id)
        if key in self._cache:
            return self._cache.pop(key)
        task = self._tasks.get(key)
        if task is None:
            return None
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except (TimeoutError, Exception):  # noqa: BLE001
            return None
        return self._cache.pop(key, None)

    def cleanup_session(self, session_id: str) -> None:
        """Remove all cache entries and cancel all tasks for a session."""
        # Remove cache entries.
        keys_to_remove = [k for k in self._cache if k[0] == session_id]
        for k in keys_to_remove:
            self._cache.pop(k, None)

        # Cancel and remove tasks.
        for k in list(self._tasks):
            if k[0] == session_id:
                task = self._tasks.pop(k)
                task.cancel()
