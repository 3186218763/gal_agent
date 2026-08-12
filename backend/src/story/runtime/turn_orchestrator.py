"""Turn orchestrator: single-turn command pipeline with SSE streaming.

The orchestrator is the sole entry point for a player turn.  It wires
together every segment-engine component in a deterministic pipeline:

    claim -> resolve choice -> pacing -> Director -> validate plan ->
    Writer -> validate draft -> Guard -> simulate -> (CompletionJudge) ->
    atomic commit -> SSE stream.

The agent proposes; the deterministic kernel validates and commits.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator
from typing import Any

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
from src.story.runtime.segment_contracts import (
    DirectorPort,
    GuardPort,
    GuardResult,
    SegmentWriterPort,
)
from src.story.runtime.simulator import simulate_resolution, simulate_segment
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
    SceneAcknowledged,
    SessionEnded,
    SessionState,
    SessionStatus,
    apply_events,
)
from src.story.state.events import StoryEvent
from src.story.storage import CommandInProgress, StoryEventStore


def _turn_fingerprint(
    expected_revision: int, choice_id: str | None = None
) -> str:
    payload = {
        "kind": "turn",
        "expected_revision": expected_revision,
        "choice_id": choice_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


class TurnOrchestrator:
    """Sole entry point for a player turn.

    Pipeline: resolve choice -> auto-ack -> derive pacing ->
    Director.plan_segment -> validate plan -> Writer.write_segment ->
    validate draft -> Guard.check_segment -> simulate segment events ->
    (if ending: CompletionJudge) -> atomic commit -> SSE stream.
    """

    def __init__(
        self,
        store: StoryEventStore,
        director: DirectorPort,
        writer: SegmentWriterPort,
        guard: GuardPort,
        completion_judge: CompletionJudge,
        planner: PlannerPort | None = None,
    ) -> None:
        self.store = store
        self.director = director
        self.writer = writer
        self.guard = guard
        self.completion_judge = completion_judge
        self.planner = planner

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
            claim = self.store.claim_command(
                session_id, idempotency_key, "turn", fingerprint
            )
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
                    f"session {session_id}: expected {expected_revision}, "
                    f"current {state.revision}"
                )
            if state.status == SessionStatus.ENDED:
                raise RuntimeSessionEnded(session_id)

            # --------------------------------------------------------------
            # Step 1: Resolve choice (if non-opening turn).
            # --------------------------------------------------------------
            pre_events: list[StoryEvent] = []

            if choice_id is not None:
                if self.planner is None:
                    raise RuntimeError(
                        "planner is required for choice resolution"
                    )
                if state.pending_decision is None:
                    raise InvalidChoice("no decision is pending")
                choice = next(
                    (c for c in state.pending_decision.choices if c.id == choice_id),
                    None,
                )
                if choice is None:
                    raise InvalidChoice(f"choice was not offered: {choice_id}")

                # Per cross-plan resolution Section 10: delegate to planner.
                try:
                    resolution = await self.planner.resolve_action(
                        pack, state, choice
                    )
                    resolution = validate_action_resolution(
                        pack, state, resolution,
                        expected_action_id=choice.action_id,
                    )
                except (ModelContractError, ProposalRejected) as exc:
                    raise RuntimeGenerationUnavailable(
                        "the model could not produce a valid action resolution"
                    ) from exc

                pre_events.extend(
                    simulate_resolution(state, choice, resolution, idempotency_key)
                )

                # Apply pre-events to advance local state.
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
            # Step 2: Auto-ack any pending scene from the previous turn.
            # --------------------------------------------------------------
            if state.pending_scene is not None:
                ack = SceneAcknowledged(scene_id=state.pending_scene.scene_id)
                pre_events.append(ack)
                ack_envelope = EventEnvelope(
                    session_id=session_id,
                    sequence=state.revision + 1,
                    event=ack,
                )
                state = apply_events(state, (ack_envelope,))

            # --------------------------------------------------------------
            # Step 3: Derive pacing.
            # --------------------------------------------------------------
            pacing = compute_pacing_envelope(state, pack)

            # --------------------------------------------------------------
            # Step 4: Director proposes segment plan.
            # --------------------------------------------------------------
            yield ("heartbeat", {})

            try:
                plan = await self.director.plan_segment(pack, state, pacing)
            except Exception as exc:
                raise RuntimeGenerationUnavailable(
                    "director failed to produce a segment plan"
                ) from exc

            try:
                plan = validate_segment_plan(pack, state, plan, pacing)
            except (ModelContractError, ProposalRejected) as exc:
                detail = getattr(exc, "errors", None) or str(exc)
                raise RuntimeGenerationUnavailable(
                    f"director produced an invalid segment plan: {detail}"
                ) from exc

            # Now that the director has produced a plan, we know the
            # segment_id and can emit the segment_started event.
            yield (
                "segment_started",
                {
                    "segment_id": plan.segment_id,
                    "expected_revision": expected_revision,
                },
            )

            # --------------------------------------------------------------
            # Step 5: Writer produces draft.
            # --------------------------------------------------------------
            yield ("heartbeat", {})

            try:
                draft = await self.writer.write_segment(pack, state, plan)
            except Exception as exc:
                raise RuntimeGenerationUnavailable(
                    "writer failed to produce a segment draft"
                ) from exc

            try:
                draft = validate_segment_draft(plan, draft)
            except (ModelContractError, ProposalRejected) as exc:
                raise RuntimeGenerationUnavailable(
                    "writer produced an invalid segment draft"
                ) from exc

            # --------------------------------------------------------------
            # Step 6: Guard checks the segment.
            # --------------------------------------------------------------
            guard_result: GuardResult = self.guard.check_segment(
                pack, state, plan, draft
            )
            if not guard_result.passed:
                raise RuntimeGenerationUnavailable(
                    "guard rejected segment"
                )

            # --------------------------------------------------------------
            # Step 7: Stream provisional blocks to the client.
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
            # Step 8: Simulate segment events (deterministic kernel).
            # --------------------------------------------------------------
            seg_events = simulate_segment(pack, state, plan, draft)

            # --------------------------------------------------------------
            # Step 9: If ending, run completion judge and build ending events.
            # --------------------------------------------------------------
            completion_result = None
            if plan.terminal == "ending":
                # Simulate the final state for the judge.
                all_events_so_far: list[StoryEvent] = list(pre_events) + list(
                    seg_events
                )
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

                # Gather completion requirements from v2 pack or default empty.
                reqs = getattr(
                    pack.source, "completion_requirements", ()
                ) or ()
                completion_result = self.completion_judge.evaluate(
                    reqs, final_state, judge_envelopes,
                )

            # Build the full event list for atomic commit.
            all_story_events: list[StoryEvent] = list(pre_events) + list(
                seg_events
            )

            if plan.terminal == "ending" and completion_result is not None:
                # Per cross-plan resolution Section 8: convert runtime
                # CompletionAssessment to state CompletionAssessmentRecord.
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
                ending_event = next(
                    e for e in seg_events if isinstance(e, EndingGenerated)
                )
                all_story_events.append(
                    SessionEnded(ending_id=ending_event.ending_id)
                )

            # --------------------------------------------------------------
            # Step 10: Atomic commit.
            # --------------------------------------------------------------
            def result_factory(
                updated: SessionState, envelopes
            ) -> str:
                ready_data: dict[str, Any] = {
                    "segment_id": plan.segment_id,
                    "revision": updated.revision,
                    "terminal": plan.terminal,
                    "blocks": streamed_blocks,
                    "choices": None,
                    "ending": None,
                }

                if plan.terminal == "decision":
                    # Mirror the simulator's DecisionPresented mapping so the
                    # SSE payload agrees with the committed event: use plan
                    # scene choices when the last scene carries them, else
                    # fall back to the authoritative segment draft choices.
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
                elif (
                    plan.terminal == "ending" and draft.ending is not None
                ):
                    ready_data["ending"] = {
                        "ending_id": draft.ending.ending_id,
                        "title": draft.ending.title,
                        "tone": draft.ending.tone,
                        "terminal_state_summary": (
                            draft.ending.terminal_state_summary
                        ),
                    }
                    if updated.completion is not None:
                        ready_data["cleared"] = updated.completion.cleared
                        ready_data["assessments"] = [
                            a.model_dump(mode="json")
                            for a in updated.completion.assessments
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
            # Step 11: Stream the final segment_ready event.
            # --------------------------------------------------------------
            result_data = json.loads(result_json)
            ready_data = result_data["segment_ready"]
            ready_data["revision"] = updated_state.revision
            yield ("segment_ready", ready_data)

        except Exception:
            self.store.release_command(
                session_id, idempotency_key, "turn", fingerprint
            )
            raise
