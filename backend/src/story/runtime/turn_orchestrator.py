"""Turn orchestrator: single-turn command pipeline with SSE streaming.

The orchestrator is the sole entry point for a player turn.  It wires
together every segment-engine component in a deterministic pipeline:

    claim -> resolve choice -> pacing -> Director -> validate plan ->
    Writer -> validate draft -> Guard -> simulate -> (CompletionJudge) ->
    atomic commit -> SSE stream.

When a pre-generated cache entry is available (Pack Cache for the opening
or Session Cache for a choice), the orchestrator skips all LLM calls and
streams pre-computed blocks instantly.  The agent proposes; the
deterministic kernel validates and commits.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator

# Forward declaration to avoid circular import at module scope.
from typing import TYPE_CHECKING, Any

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.contracts import (
    DecisionRequired,
    InvalidChoice,
    ModelContractError,
    PlannerPort,
    RuntimeGenerationUnavailable,
    RuntimeRevisionConflict,
    RuntimeSessionEnded,
)
from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.pack_cache import PackCache
from src.story.runtime.segment_contracts import (
    DirectorPort,
    GuardPort,
    GuardResult,
    SegmentWriterPort,
)
from src.story.runtime.simulator import simulate_resolution, simulate_segment
from src.story.runtime.unified_segment import UnifiedSegmentPort
from src.story.runtime.validator import (
    ProposalRejected,
    validate_action_resolution,
    validate_segment_draft,
    validate_segment_plan,
)
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import (
    CompletionAssessmentRecord,
    CompletionEvaluated,
    EndingGenerated,
    EventEnvelope,
    PresentedChoice,
    SceneAcknowledged,
    SessionEnded,
    SessionState,
    SessionStatus,
    apply_events,
)
from src.story.state.events import StoryEvent
from src.story.storage import CommandInProgress, StoryEventStore

if TYPE_CHECKING:
    from src.story.runtime.pregeneration import PreGenerationManager


def _turn_fingerprint(expected_revision: int, choice_id: str | None = None) -> str:
    payload = {
        "kind": "turn",
        "expected_revision": expected_revision,
        "choice_id": choice_id,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class TurnOrchestrator:
    """Sole entry point for a player turn.

    Pipeline: resolve choice -> auto-ack -> derive pacing ->
    Director.plan_segment -> validate plan -> Writer.write_segment ->
    validate draft -> Guard.check_segment -> simulate segment events ->
    (if ending: CompletionJudge) -> atomic commit -> SSE stream.

    When ``pack_cache`` or ``pregen_manager`` are provided, the orchestrator
    checks for cached segments before calling any agent.  A cache hit skips
    pacing, generation, validation, guard, and simulation.
    """

    def __init__(
        self,
        store: StoryEventStore,
        director: DirectorPort,
        writer: SegmentWriterPort,
        guard: GuardPort,
        completion_judge: CompletionJudge,
        planner: PlannerPort | None = None,
        unified_agent: UnifiedSegmentPort | None = None,
        pack_cache: PackCache | None = None,
        pregen_manager: PreGenerationManager | None = None,
    ) -> None:
        self.store = store
        self.director = director
        self.writer = writer
        self.guard = guard
        self.completion_judge = completion_judge
        self.planner = planner
        self.unified_agent = unified_agent
        self.pack_cache = pack_cache
        self.pregen_manager = pregen_manager

    async def execute_turn(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        choice_id: str | None,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        fingerprint = _turn_fingerprint(expected_revision, choice_id)

        # ------------------------------------------------------------------
        # Claim the command (idempotency + concurrency control).
        # ------------------------------------------------------------------

        try:
            claim = self.store.claim_command(session_id, idempotency_key, "turn", fingerprint)
        except CommandInProgress:
            # Per cross-plan resolution Section 12: emit retry_after.
            yield (
                "retry_after",
                {
                    "retry_after_seconds": 5,
                    "message": "Command is already being processed",
                },
            )
            return

        # Replay: command was already completed — return cached result.
        if claim.replay_json is not None:
            replay = json.loads(claim.replay_json)
            yield (
                "segment_started",
                {
                    "segment_id": replay["segment_id"],
                    "expected_revision": expected_revision,
                },
            )
            for block in replay.get("blocks", []):
                yield ("block", block)
            ready = replay["segment_ready"]
            yield ("segment_ready", ready)
            return

        # ------------------------------------------------------------------
        # Main pipeline (everything is wrapped in try/except so that any
        # failure releases the command lease).
        # ------------------------------------------------------------------

        try:
            state = self.store.load_session(session_id)
            if state.revision != expected_revision:
                raise RuntimeRevisionConflict(
                    f"session {session_id}: expected {expected_revision}, current {state.revision}"
                )
            if state.status == SessionStatus.ENDED:
                raise RuntimeSessionEnded(session_id)

            pre_events: list[StoryEvent] = []
            seg_events: list[StoryEvent] = []

            # --------------------------------------------------------------
            # Check caches before any LLM work.
            # --------------------------------------------------------------
            cached_hit = False

            if choice_id is not None:
                # ── Choice turn: try session cache → pack cache → in-progress ──
                pregen = None
                if self.pregen_manager is not None:
                    pregen = self.pregen_manager.try_get(session_id, choice_id)
                if pregen is None and self.pack_cache is not None:
                    pregen = self.pack_cache.load_pregen(pack.pack_hash, choice_id)
                if pregen is None and self.pregen_manager is not None:
                    pregen = await self.pregen_manager.await_in_progress(session_id, choice_id)

                if pregen is not None:
                    plan = pregen.segment_plan
                    draft = pregen.segment_draft
                    pre_events = list(pregen.pre_events)
                    seg_events = list(pregen.seg_events)
                    cached_hit = True
                else:
                    # ── Normal choice resolution ──
                    if self.planner is None:
                        raise RuntimeError("planner is required for choice resolution")
                    if state.pending_decision is None:
                        raise InvalidChoice("no decision is pending")
                    choice = next(
                        (c for c in state.pending_decision.choices if c.id == choice_id),
                        None,
                    )
                    if choice is None:
                        raise InvalidChoice(f"choice was not offered: {choice_id}")

                    try:
                        resolution = await self.planner.resolve_action(pack, state, choice)
                        resolution = validate_action_resolution(
                            pack,
                            state,
                            resolution,
                            expected_action_id=choice.action_id,
                        )
                    except (ModelContractError, ProposalRejected) as exc:
                        raise RuntimeGenerationUnavailable(
                            "the model could not produce a valid action resolution"
                        ) from exc

                    pre_events.extend(
                        simulate_resolution(state, choice, resolution, idempotency_key)
                    )

                    pre_envelopes = tuple(
                        EventEnvelope(
                            session_id=session_id,
                            sequence=state.revision + i,
                            event=e,
                        )
                        for i, e in enumerate(pre_events, start=1)
                    )
                    state = apply_events(state, pre_envelopes)

            elif state.pending_decision is not None:
                raise DecisionRequired(state.pending_decision.decision_id)

            # --------------------------------------------------------------
            # Opening cache check (non-choice turns only).
            # --------------------------------------------------------------
            if not cached_hit and choice_id is None:
                cached_opening = None
                if self.pack_cache is not None:
                    cached_opening = self.pack_cache.load_opening(pack.pack_hash)
                if cached_opening is not None:
                    plan = cached_opening.segment_plan
                    draft = cached_opening.segment_draft
                    seg_events = list(cached_opening.seg_events)
                    cached_hit = True

            # --------------------------------------------------------------
            # Normal generation pipeline (only when no cache hit).
            # --------------------------------------------------------------
            if not cached_hit:
                # Step 2: Auto-ack any pending scene from the previous turn.
                if state.pending_scene is not None:
                    ack = SceneAcknowledged(scene_id=state.pending_scene.scene_id)
                    pre_events.append(ack)
                    ack_envelope = EventEnvelope(
                        session_id=session_id,
                        sequence=state.revision + 1,
                        event=ack,
                    )
                    state = apply_events(state, (ack_envelope,))

                # Step 3: Derive pacing.
                pacing = compute_pacing_envelope(state, pack)

                # Steps 4+5: Generate segment plan + draft.
                yield ("heartbeat", {})

                try:
                    if self.unified_agent is not None:
                        try:
                            result = await self.unified_agent.generate(pack, state, pacing)
                        except Exception as exc:
                            raise RuntimeGenerationUnavailable(
                                "unified segment agent failed to generate"
                            ) from exc

                        plan = result.segment_plan
                        draft = result.segment_draft

                        try:
                            plan = validate_segment_plan(pack, state, plan, pacing)
                        except (
                            ModelContractError,
                            ProposalRejected,
                        ) as exc:
                            detail = getattr(exc, "errors", None) or str(exc)
                            raise RuntimeGenerationUnavailable(
                                f"unified agent produced an invalid segment plan: {detail}"
                            ) from exc

                        try:
                            draft = validate_segment_draft(plan, draft)
                        except (
                            ModelContractError,
                            ProposalRejected,
                        ) as exc:
                            detail = getattr(exc, "errors", None) or str(exc)
                            raise RuntimeGenerationUnavailable(
                                f"unified agent produced an invalid segment draft: {detail}"
                            ) from exc
                    else:
                        try:
                            plan = await self.director.plan_segment(pack, state, pacing)
                        except Exception as exc:
                            raise RuntimeGenerationUnavailable(
                                "director failed to produce a segment plan"
                            ) from exc

                        try:
                            plan = validate_segment_plan(pack, state, plan, pacing)
                        except (
                            ModelContractError,
                            ProposalRejected,
                        ) as exc:
                            detail = getattr(exc, "errors", None) or str(exc)
                            raise RuntimeGenerationUnavailable(
                                f"director produced an invalid segment plan: {detail}"
                            ) from exc

                        yield (
                            "segment_started",
                            {
                                "segment_id": plan.segment_id,
                                "expected_revision": expected_revision,
                            },
                        )

                        yield ("heartbeat", {})

                        try:
                            draft = await self.writer.write_segment(pack, state, plan)
                        except Exception as exc:
                            raise RuntimeGenerationUnavailable(
                                "writer failed to produce a segment draft"
                            ) from exc

                        try:
                            draft = validate_segment_draft(plan, draft)
                        except (
                            ModelContractError,
                            ProposalRejected,
                        ) as exc:
                            raise RuntimeGenerationUnavailable(
                                "writer produced an invalid segment draft"
                            ) from exc
                except RuntimeGenerationUnavailable:
                    from src.story.runtime.fallback import (
                        generate_fallback_segment,
                    )

                    plan, draft = generate_fallback_segment(pack, state)

            # --------------------------------------------------------------
            # Emit segment_started with the final segment_id.
            # (may differ from the legacy path's early emission if fallback
            # was used; cache hits get exactly one here.)
            # --------------------------------------------------------------
            yield (
                "segment_started",
                {
                    "segment_id": plan.segment_id,
                    "expected_revision": expected_revision,
                },
            )

            # --------------------------------------------------------------
            # Guard (only for non-cached segments).
            # --------------------------------------------------------------
            if not cached_hit:
                guard_result: GuardResult = self.guard.check_segment(pack, state, plan, draft)
                if not guard_result.passed:
                    raise RuntimeGenerationUnavailable("guard rejected segment")

            # --------------------------------------------------------------
            # Stream provisional blocks to the client.
            # --------------------------------------------------------------
            block_index = 0
            streamed_blocks: list[dict[str, Any]] = []
            for scene_draft in draft.scene_drafts:
                for block in scene_draft.blocks:
                    block_data = {
                        "segment_id": plan.segment_id,
                        "index": block_index,
                        "kind": block.kind,
                        "text": block.text,
                        "character_id": block.character_id,
                    }
                    streamed_blocks.append(block_data)
                    yield ("block", block_data)
                    block_index += 1

            # --------------------------------------------------------------
            # Simulate segment events (skip if cached — already computed).
            # --------------------------------------------------------------
            if not cached_hit:
                seg_events = list(simulate_segment(pack, state, plan, draft))

            # --------------------------------------------------------------
            # Completion judge (if ending).
            # --------------------------------------------------------------
            completion_result = None
            if plan.terminal == "ending":
                all_events_so_far: list[StoryEvent] = list(pre_events) + list(seg_events)
                judge_envelopes = tuple(
                    EventEnvelope(
                        session_id=session_id,
                        sequence=expected_revision + i,
                        event=e,
                    )
                    for i, e in enumerate(all_events_so_far, start=1)
                )
                fresh_state = self.store.load_session(session_id)
                final_state = apply_events(fresh_state, judge_envelopes)

                reqs = getattr(pack.source, "completion_requirements", ()) or ()
                persisted_history = self.store.load_events(session_id)
                completion_result = self.completion_judge.evaluate(
                    reqs,
                    final_state,
                    persisted_history + judge_envelopes,
                )

            # Build the full event list for atomic commit.
            all_story_events: list[StoryEvent] = list(pre_events) + list(seg_events)

            if plan.terminal == "ending" and completion_result is not None:
                assessment_records = tuple(
                    CompletionAssessmentRecord(
                        requirement_id=a.requirement_id,
                        satisfied=a.satisfied,
                        cited_event_ids=a.cited_event_ids,
                        rationale=a.rationale,
                    )
                    for a in completion_result.assessments
                )
                all_story_events.append(
                    CompletionEvaluated(
                        cleared=completion_result.cleared,
                        assessments=assessment_records,
                    )
                )
                ending_event = next(e for e in seg_events if isinstance(e, EndingGenerated))
                all_story_events.append(SessionEnded(ending_id=ending_event.ending_id))

            # --------------------------------------------------------------
            # Atomic commit.
            # --------------------------------------------------------------
            def result_factory(updated: SessionState, envelopes) -> str:
                ready_data: dict[str, Any] = {
                    "segment_id": plan.segment_id,
                    "revision": updated.revision,
                    "terminal": plan.terminal,
                    "blocks": streamed_blocks,
                    "choices": None,
                    "ending": None,
                }

                if plan.terminal == "decision":
                    written_map = {wc.option_id: wc for wc in draft.choices}
                    last_scene = plan.scenes[-1]
                    if last_scene.choices:
                        ready_data["choices"] = [
                            {
                                "id": c.option_id,
                                "action_id": c.action_id,
                                "label": written_map[c.option_id].label,
                                "intent": c.intent,
                                "target_character_id": c.target_character_id,
                                "preview": written_map[c.option_id].preview,
                            }
                            for c in last_scene.choices
                        ]
                    else:
                        ready_data["choices"] = [
                            {
                                "id": wc.option_id,
                                "action_id": wc.option_id,
                                "label": wc.label,
                                "intent": wc.label,
                                "target_character_id": None,
                                "preview": wc.preview,
                            }
                            for wc in draft.choices
                        ]
                elif plan.terminal == "ending" and draft.ending is not None:
                    ready_data["ending"] = {
                        "ending_id": draft.ending.ending_id,
                        "title": draft.ending.title,
                        "tone": draft.ending.tone,
                        "terminal_state_summary": (draft.ending.terminal_state_summary),
                    }
                    if updated.completion is not None:
                        ready_data["cleared"] = updated.completion.cleared
                        ready_data["assessments"] = [
                            a.model_dump(mode="json") for a in updated.completion.assessments
                        ]

                return json.dumps(
                    {
                        "segment_id": plan.segment_id,
                        "blocks": streamed_blocks,
                        "segment_ready": ready_data,
                    }
                )

            updated_state, _, result_json = self.store.commit_command(
                session_id,
                idempotency_key,
                "turn",
                fingerprint,
                expected_revision,
                all_story_events,
                result_factory,
            )

            # --------------------------------------------------------------
            # Stream the final segment_ready event.
            # --------------------------------------------------------------
            result_data = json.loads(result_json)
            ready_data = result_data["segment_ready"]
            ready_data["revision"] = updated_state.revision
            yield ("segment_ready", ready_data)

            # --------------------------------------------------------------
            # Trigger background pre-generation for next choices.
            # --------------------------------------------------------------
            if (
                plan.terminal == "decision"
                and self.pregen_manager is not None
                and ready_data.get("choices")
            ):
                updated_state_for_pregen = self.store.load_session(session_id)
                presented_choices = [
                    PresentedChoice(
                        id=c["id"],
                        action_id=c["action_id"],
                        label=c["label"],
                        intent=c.get("intent", c["label"]),
                        target_character_id=c.get("target_character_id"),
                        preview=c.get("preview"),
                    )
                    for c in ready_data["choices"]
                ]
                asyncio.create_task(
                    self.pregen_manager.pregenerate_choices(
                        session_id,
                        updated_state_for_pregen,
                        presented_choices,
                        pack,
                    )
                )

        except Exception:
            self.store.release_command(session_id, idempotency_key, "turn", fingerprint)
            raise
