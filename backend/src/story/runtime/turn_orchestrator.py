"""Authoritative Playthrough command flow.

Player Choice Meaning is committed before any model-dependent work.  The
resulting Pending Consequence is then resolved by a stable command derived
from the committed choice event.  Only a fully validated and atomically
committed segment is emitted to the player.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from typing import Any

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.contracts import (
    DecisionRequired,
    InvalidChoice,
    ModelContractError,
    PackMismatch,
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
    SegmentDraft,
    SegmentPlan,
    SegmentWriterPort,
)
from src.story.runtime.semantic_judge import SemanticJudgePort
from src.story.runtime.simulator import (
    choice_selection_event,
    simulate_consequence,
    simulate_segment,
)
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
    CostIncurred,
    EndingGenerated,
    EventEnvelope,
    ObligationCreated,
    PresentedChoice,
    RelationshipChanged,
    RelationshipEventRecorded,
    SceneAcknowledged,
    SceneCommitted,
    SessionEnded,
    SessionState,
    SessionStatus,
    apply_events,
    derive_causal_traces,
    derive_relationship_turning_points,
)
from src.story.state.events import StoryEvent
from src.story.storage import CommandInProgress, StoryEventStore

logger = logging.getLogger(__name__)


def _fingerprint(kind: str, **values: object) -> str:
    payload = {"kind": kind, **values}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _selection_command_id(idempotency_key: str) -> str:
    return f"select_choice:{idempotency_key}"


def _opening_command_id(idempotency_key: str) -> str:
    return f"generate_opening:{idempotency_key}"


def _consequence_command_id(choice_event_id: str) -> str:
    return f"resolve_consequence:{choice_event_id}"


def _retry_after() -> tuple[str, dict[str, Any]]:
    return (
        "retry_after",
        {
            "retry_after_seconds": 5,
            "message": "Command is already being processed",
        },
    )


# SSE progress plumbing: long model calls run inside nested helpers while the
# outer generator owns the response stream, so helpers forward events through
# an ``emit`` callback instead of yielding directly.
_HEARTBEAT_INTERVAL_SECONDS = 15.0

ProgressEmit = Callable[[tuple[str, Any]], Awaitable[None]]


async def _noop_emit(event: tuple[str, Any]) -> None:
    """Sink used when no SSE stream is attached (offline callers)."""


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _progress_event(stage: str, started: float) -> tuple[str, dict[str, Any]]:
    return ("progress", {"stage": stage, "elapsed_ms": _elapsed_ms(started)})


class TurnOrchestrator:
    """Deep module for opening, selecting, and resolving a Playthrough.

    ``pack_cache`` may seed an opening generation, but unsafe choice cache
    entries are deliberately not consumed.  Any future cache adapter must
    pass through this module's normal validation and persistence path.
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
        semantic_judge: SemanticJudgePort | None = None,
    ) -> None:
        self.store = store
        self.director = director
        self.writer = writer
        self.guard = guard
        self.completion_judge = completion_judge
        self.planner = planner
        self.unified_agent = unified_agent
        self.pack_cache = pack_cache
        self.semantic_judge = semantic_judge

    async def execute_turn(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        choice_id: str | None,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Stream one turn, forwarding progress events as they are produced.

        The command flow runs in ``_turn_worker`` so ``progress`` and
        ``heartbeat`` events reach the stream while model calls are in flight.
        """
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        worker = asyncio.create_task(
            self._turn_worker(
                pack, session_id, expected_revision, idempotency_key, choice_id, queue
            )
        )
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
            worker.result()
        finally:
            if not worker.done():
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker

    async def _turn_worker(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        choice_id: str | None,
        queue: asyncio.Queue[tuple[str, Any] | None],
    ) -> None:
        started = time.monotonic()
        emit: ProgressEmit = queue.put
        try:
            await self._run_turn(
                pack,
                session_id,
                expected_revision,
                idempotency_key,
                choice_id,
                emit,
                started,
            )
        finally:
            queue.put_nowait(None)

    async def _run_turn(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        choice_id: str | None,
        emit: ProgressEmit,
        started: float,
    ) -> None:
        if choice_id is not None:
            selection = self._commit_choice(
                pack,
                session_id,
                expected_revision,
                idempotency_key,
                choice_id,
            )
            if selection is None:
                await emit(_retry_after())
                return
            choice_event_id = selection["choice_event_id"]
        else:
            state = self._load_compatible_session(pack, session_id)
            if state.pending_consequence is not None:
                if state.revision != expected_revision:
                    raise RuntimeRevisionConflict(
                        f"session {session_id}: expected {expected_revision}, "
                        f"current {state.revision}"
                    )
                choice_event_id = state.pending_consequence.choice_event_id
            elif expected_revision == 0:
                await self._execute_opening(
                    pack,
                    session_id,
                    expected_revision,
                    idempotency_key,
                    emit,
                    started,
                )
                return
            elif state.revision != expected_revision:
                choice_event_id = self._choice_event_at_revision(
                    session_id,
                    expected_revision,
                )
                if choice_event_id is None:
                    raise RuntimeRevisionConflict(
                        f"session {session_id}: expected {expected_revision}, "
                        f"current {state.revision}"
                    )
            else:
                await self._execute_opening(
                    pack,
                    session_id,
                    expected_revision,
                    idempotency_key,
                    emit,
                    started,
                )
                return

        consequence_command_id = _consequence_command_id(choice_event_id)
        consequence_fingerprint = _fingerprint(
            "resolve_consequence",
            choice_event_id=choice_event_id,
            pack_hash=pack.pack_hash,
        )
        try:
            claim = self.store.claim_command(
                session_id,
                consequence_command_id,
                "resolve_consequence",
                consequence_fingerprint,
            )
        except CommandInProgress:
            await emit(_retry_after())
            return

        if claim.replay_json is not None:
            for event in self._committed_segment_events(json.loads(claim.replay_json)):
                await emit(event)
            return

        try:
            state = self._load_compatible_session(pack, session_id)
            pending = state.pending_consequence
            if pending is None or pending.choice_event_id != choice_event_id:
                raise RuntimeRevisionConflict("the pending consequence has already changed")
            if state.status == SessionStatus.ENDED:
                raise RuntimeSessionEnded(session_id)

            result = await self._resolve_and_commit_consequence(
                pack,
                state,
                consequence_command_id,
                consequence_fingerprint,
                emit,
                started,
            )
        except Exception:
            self.store.release_command(
                session_id,
                consequence_command_id,
                "resolve_consequence",
                consequence_fingerprint,
            )
            raise

        for event in self._committed_segment_events(result):
            await emit(event)

    def _commit_choice(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        choice_id: str,
    ) -> dict[str, Any] | None:
        command_id = _selection_command_id(idempotency_key)
        fingerprint = _fingerprint(
            "select_choice",
            expected_revision=expected_revision,
            choice_id=choice_id,
            pack_hash=pack.pack_hash,
        )
        try:
            claim = self.store.claim_command(
                session_id,
                command_id,
                "select_choice",
                fingerprint,
            )
        except CommandInProgress:
            return None

        if claim.replay_json is not None:
            return json.loads(claim.replay_json)

        try:
            state = self._load_compatible_session(pack, session_id)
            if state.revision != expected_revision:
                raise RuntimeRevisionConflict(
                    f"session {session_id}: expected {expected_revision}, current {state.revision}"
                )
            if state.status == SessionStatus.ENDED:
                raise RuntimeSessionEnded(session_id)
            if state.pending_decision is None:
                raise InvalidChoice("no decision is pending")
            choice = next(
                (item for item in state.pending_decision.choices if item.id == choice_id),
                None,
            )
            if choice is None:
                raise InvalidChoice(f"choice was not offered: {choice_id}")

            event = choice_selection_event(state, choice, idempotency_key)

            def selection_result(
                updated: SessionState, envelopes: tuple[EventEnvelope, ...]
            ) -> str:
                return json.dumps(
                    {
                        "choice_event_id": envelopes[0].event_id,
                        "revision": updated.revision,
                    }
                )

            _, _, result_json = self.store.commit_command(
                session_id,
                command_id,
                "select_choice",
                fingerprint,
                expected_revision,
                (event,),
                selection_result,
            )
            return json.loads(result_json)
        except Exception:
            self.store.release_command(
                session_id,
                command_id,
                "select_choice",
                fingerprint,
            )
            raise

    async def _execute_opening(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        emit: ProgressEmit,
        started: float,
    ) -> None:
        command_id = _opening_command_id(idempotency_key)
        fingerprint = _fingerprint(
            "generate_opening",
            expected_revision=expected_revision,
            pack_hash=pack.pack_hash,
        )
        try:
            claim = self.store.claim_command(
                session_id,
                command_id,
                "generate_opening",
                fingerprint,
            )
        except CommandInProgress:
            await emit(_retry_after())
            return

        if claim.replay_json is not None:
            for event in self._committed_segment_events(json.loads(claim.replay_json)):
                await emit(event)
            return

        try:
            state = self._load_compatible_session(pack, session_id)
            if state.revision != expected_revision:
                raise RuntimeRevisionConflict(
                    f"session {session_id}: expected {expected_revision}, current {state.revision}"
                )
            if state.status == SessionStatus.ENDED:
                raise RuntimeSessionEnded(session_id)
            if state.pending_decision is not None:
                raise DecisionRequired(state.pending_decision.decision_id)

            result = await self._generate_and_commit_segment(
                pack,
                state,
                (),
                command_id,
                "generate_opening",
                fingerprint,
                emit,
                started,
            )
        except Exception:
            self.store.release_command(
                session_id,
                command_id,
                "generate_opening",
                fingerprint,
            )
            raise

        for event in self._committed_segment_events(result):
            await emit(event)

    async def _resolve_and_commit_consequence(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        command_id: str,
        fingerprint: str,
        emit: ProgressEmit,
        started: float,
    ) -> dict[str, Any]:
        if self.planner is None:
            raise RuntimeGenerationUnavailable("planner is required for consequence resolution")
        pending = state.pending_consequence
        if pending is None:
            raise RuntimeRevisionConflict("no consequence is pending")

        choice = PresentedChoice(
            id=pending.option_id,
            action_id=pending.action_id,
            label=pending.intent[:80],
            intent=pending.intent,
            target_character_id=pending.target_character_id,
            stance_axis=pending.stance_axis,
            stance_value=pending.stance_value,
            accepted_risk=pending.accepted_risk,
            potential_obligation_kind=pending.potential_obligation_kind,
            conflict_axis_id=pending.conflict_axis_id,
        )
        await emit(_progress_event("planning", started))
        # A rejected resolution gets one regeneration attempt with the
        # validator reasons fed back to the planner — same shape as the
        # segment regeneration loop, and nothing commits unless the revised
        # resolution passes the deterministic validator.
        rejection_notes: list[str] = []
        regeneration_left = 1
        while True:
            try:
                resolution = await self._await_with_heartbeats(
                    self.planner.resolve_action(
                        pack, state, choice, rejection_notes=tuple(rejection_notes)
                    ),
                    emit,
                    started,
                )
                resolution = validate_action_resolution(
                    pack,
                    state,
                    resolution,
                    expected_action_id=pending.action_id,
                )
            except (ModelContractError, ProposalRejected) as exc:
                detail = getattr(exc, "errors", None) or str(exc)
                if regeneration_left <= 0:
                    raise RuntimeGenerationUnavailable(
                        "planner failed to resolve the consequence"
                    ) from exc
                regeneration_left -= 1
                rejection_notes = [str(detail)]
                logger.warning("resolution rejected: %s", detail)
                await emit(_progress_event("regenerating", started))
                continue
            except Exception as exc:
                raise RuntimeGenerationUnavailable(
                    "planner failed to resolve the consequence"
                ) from exc
            break

        consequence_events = simulate_consequence(pack, state, resolution)
        consequence_envelopes = tuple(
            EventEnvelope(
                session_id=state.session_id,
                sequence=state.revision + index,
                event=event,
            )
            for index, event in enumerate(consequence_events, start=1)
        )
        resolved_state = apply_events(state, consequence_envelopes)
        return await self._generate_and_commit_segment(
            pack,
            resolved_state,
            consequence_events,
            command_id,
            "resolve_consequence",
            fingerprint,
            emit,
            started,
            commit_revision=state.revision,
            pending_choice=choice,
        )

    async def _generate_and_commit_segment(
        self,
        pack: CompiledScriptPack,
        generation_state: SessionState,
        pre_events: tuple[StoryEvent, ...],
        command_id: str,
        command_kind: str,
        fingerprint: str,
        emit: ProgressEmit,
        started: float,
        *,
        commit_revision: int | None = None,
        pending_choice: PresentedChoice | None = None,
    ) -> dict[str, Any]:
        commit_revision = generation_state.revision if commit_revision is None else commit_revision
        state = generation_state
        mutable_pre_events = list(pre_events)
        if state.pending_scene is not None:
            acknowledgement = SceneAcknowledged(scene_id=state.pending_scene.scene_id)
            mutable_pre_events.append(acknowledgement)
            state = apply_events(
                state,
                (
                    EventEnvelope(
                        session_id=state.session_id,
                        sequence=state.revision + 1,
                        event=acknowledgement,
                    ),
                ),
            )

        pacing = compute_pacing_envelope(state, pack)
        await emit(_progress_event("generating", started))
        # A rejected proposal gets one regeneration attempt with the rejection
        # reasons fed back to the writer — cheaper than a player retry (which
        # repeats the planner too) and still gated by the same chain: nothing
        # commits unless the revised proposal passes guard and judge.
        rejection_notes: list[str] = []
        regeneration_left = 1 if self.unified_agent is not None else 0
        while True:
            plan, draft, judge_preapproved = await self._generate_segment(
                pack,
                state,
                pacing,
                emit,
                started,
                allow_opening_cache=command_kind == "generate_opening" and not rejection_notes,
                rejection_notes=tuple(rejection_notes),
            )

            await emit(_progress_event("validating", started))
            guard_result: GuardResult = self.guard.check_segment(pack, state, plan, draft)
            findings = None
            if guard_result.passed and self.semantic_judge is not None and not judge_preapproved:
                # Judge pre-approved cache content once at cache-build time;
                # re-judging frozen content per session only adds a model
                # call and a nondeterministic rejection risk.
                try:
                    findings = await self._await_with_heartbeats(
                        self.semantic_judge.judge_segment(
                            pack,
                            state,
                            plan,
                            draft,
                            pending_choice,
                        ),
                        emit,
                        started,
                    )
                except Exception as exc:
                    raise RuntimeGenerationUnavailable(
                        "semantic judge failed to evaluate segment"
                    ) from exc

            if guard_result.passed and (findings is None or findings.passed):
                break

            if not guard_result.passed:
                reasons = [
                    f"guard/{v.kind} (block {v.block_index}): {v.detail}"
                    for v in guard_result.violations
                ]
                failure = "guard rejected segment"
            else:
                reasons = [
                    f"judge/{f.kind} (block {f.block_index}): {f.detail}" for f in findings.blocking
                ]
                failure = "semantic judge rejected segment"
            logger.warning("segment rejected: %s", " | ".join(reasons))
            if regeneration_left <= 0:
                raise RuntimeGenerationUnavailable(failure)
            regeneration_left -= 1
            rejection_notes = reasons
            await emit(_progress_event("regenerating", started))

        segment_events = simulate_segment(pack, state, plan, draft)
        base_events: list[StoryEvent] = [*mutable_pre_events, *segment_events]
        base_event_ids = tuple(
            f"{state.session_id}:{commit_revision + index}"
            for index in range(1, len(base_events) + 1)
        )

        # Resolve deterministic placeholder references (relationship event
        # pairs, cost effect ids) BEFORE the ending evaluation runs, so the
        # completion review reads the same committed ids it will cite.
        base_events = self._resolve_internal_references(
            state.session_id,
            base_events,
            base_event_ids,
        )

        if plan.terminal == "ending":
            base_events, event_ids = self._build_ending_batch(
                pack,
                state,
                base_events,
                commit_revision,
            )
        else:
            event_ids = base_event_ids

        all_story_events = base_events
        blocks = self._serialized_blocks(plan, draft)

        def result_factory(updated: SessionState, envelopes: tuple[EventEnvelope, ...]) -> str:
            ready: dict[str, Any] = {
                "segment_id": plan.segment_id,
                "revision": updated.revision,
                "terminal": plan.terminal,
                "blocks": blocks,
                "choices": None,
                "ending": None,
            }
            if plan.terminal == "decision":
                if updated.pending_decision is None:
                    raise RuntimeError("committed decision segment has no pending decision")
                ready["choices"] = [
                    item.model_dump(mode="json") for item in updated.pending_decision.choices
                ]
            elif draft.ending is not None:
                ready["ending"] = {
                    "ending_id": draft.ending.ending_id,
                    "title": draft.ending.title,
                    "tone": draft.ending.tone,
                    "terminal_state_summary": draft.ending.terminal_state_summary,
                }
                if updated.completion is not None:
                    ready["cleared"] = updated.completion.cleared
                    ready["assessments"] = [
                        item.model_dump(mode="json") for item in updated.completion.assessments
                    ]
                    ready["causal_traces"] = [
                        trace.model_dump(mode="json")
                        for trace in derive_causal_traces(
                            self.store.load_events(state.session_id) + envelopes
                        )
                    ]
            return json.dumps(
                {
                    "segment_id": plan.segment_id,
                    "blocks": blocks,
                    "segment_ready": ready,
                }
            )

        await emit(_progress_event("committing", started))
        _, _, result_json = self.store.commit_command(
            state.session_id,
            command_id,
            command_kind,
            fingerprint,
            commit_revision,
            all_story_events,
            result_factory,
            event_ids=event_ids,
        )
        return json.loads(result_json)

    def _build_ending_batch(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        base_events: list[StoryEvent],
        commit_revision: int,
    ) -> tuple[list[StoryEvent], tuple[str, ...]]:
        """Append the ending's completion evaluation to the batch.

        Relationship turning points the committed history has earned are
        derived first so the completion review reads real committed
        evidence, and the CompletionEvaluated event cites the actual
        committed envelope ids (deterministic ``{session}:{sequence}`` ids
        for the entire batch).
        """
        batch = list(base_events)
        event_ids = tuple(
            f"{state.session_id}:{commit_revision + index}" for index in range(1, len(batch) + 1)
        )
        judge_envelopes = tuple(
            EventEnvelope(
                event_id=event_ids[index],
                session_id=state.session_id,
                sequence=commit_revision + index + 1,
                event=event,
            )
            for index, event in enumerate(batch)
        )
        fresh_state = self.store.load_session(state.session_id)
        final_state = apply_events(fresh_state, judge_envelopes)
        full_trace = self.store.load_events(state.session_id) + judge_envelopes

        definitions = getattr(pack.source, "relationship_turning_points", ()) or ()
        derived = derive_relationship_turning_points(definitions, full_trace)
        for index, event in enumerate(derived, start=1):
            batch.append(event)
            tail_id = f"{state.session_id}:{commit_revision + len(batch)}"
            final_state = apply_events(
                final_state,
                (
                    EventEnvelope(
                        event_id=tail_id,
                        session_id=state.session_id,
                        sequence=commit_revision + len(batch),
                        event=event,
                    ),
                ),
            )
            full_trace = full_trace + (
                EventEnvelope(
                    event_id=tail_id,
                    session_id=state.session_id,
                    sequence=commit_revision + len(batch),
                    event=event,
                ),
            )
            event_ids = (*event_ids, tail_id)

        requirements = getattr(pack.source, "completion_requirements", ()) or ()
        completion_result = self.completion_judge.evaluate(requirements, final_state, full_trace)

        ending_event = next(event for event in base_events if isinstance(event, EndingGenerated))
        batch.append(
            CompletionEvaluated(
                cleared=completion_result.cleared,
                assessments=tuple(
                    CompletionAssessmentRecord(
                        requirement_id=item.requirement_id,
                        satisfied=item.satisfied,
                        cited_event_ids=item.cited_event_ids,
                        rationale=item.rationale,
                    )
                    for item in completion_result.assessments
                ),
            )
        )
        batch.append(SessionEnded(ending_id=ending_event.ending_id))

        for index in range(len(event_ids) + 1, len(batch) + 1):
            event_ids = (*event_ids, f"{state.session_id}:{commit_revision + index}")
        return batch, event_ids

    def _resolve_internal_references(
        self,
        session_id: str,
        events: list[StoryEvent],
        event_ids: tuple[str, ...],
    ) -> list[StoryEvent]:
        """Rewrite deterministic placeholder references to committed ids.

        Relationship event pairs and derived costs carry stable placeholders
        from simulation (``rel:{choice}:{n}`` / ``obligation:{choice}``);
        the committed envelope ids are only known once the batch layout is
        final, so the authoritative flow resolves them right before commit.
        """
        resolved = list(events)
        first_scene_index = next(
            (index for index, event in enumerate(resolved) if isinstance(event, SceneCommitted)),
            None,
        )
        # An ending batch has no SceneCommitted; anchor relationship events
        # to the most recent committed scene so their evidence still grounds
        # in the completion review.
        fallback_scene_id: str | None = None
        if first_scene_index is None:
            for envelope in reversed(self.store.load_events(session_id)):
                if isinstance(envelope.event, SceneCommitted):
                    fallback_scene_id = envelope.event_id
                    break
        for index, event in enumerate(resolved):
            if isinstance(event, RelationshipEventRecorded) and not event.scene_event_id:
                if first_scene_index is not None:
                    resolved[index] = event.model_copy(
                        update={"scene_event_id": event_ids[first_scene_index]}
                    )
                elif fallback_scene_id is not None:
                    resolved[index] = event.model_copy(update={"scene_event_id": fallback_scene_id})
            elif isinstance(event, RelationshipChanged) and event.relationship_event_id:
                # The paired RelationshipEventRecorded immediately follows.
                if index + 1 < len(resolved) and isinstance(
                    resolved[index + 1], RelationshipEventRecorded
                ):
                    resolved[index] = event.model_copy(
                        update={"relationship_event_id": event_ids[index + 1]}
                    )
            elif isinstance(event, CostIncurred):
                actual: list[str] = []
                for placeholder in event.effect_event_ids:
                    target = next(
                        (
                            j
                            for j, item in enumerate(resolved)
                            if isinstance(item, ObligationCreated)
                            and item.obligation_id == placeholder
                        ),
                        None,
                    )
                    actual.append(event_ids[target] if target is not None else placeholder)
                if actual != list(event.effect_event_ids):
                    resolved[index] = event.model_copy(update={"effect_event_ids": tuple(actual)})
        return resolved

    async def _generate_segment(
        self,
        pack,
        state,
        pacing,
        emit: ProgressEmit,
        started: float,
        *,
        allow_opening_cache: bool,
        rejection_notes: tuple[str, ...] = (),
    ) -> tuple[SegmentPlan, SegmentDraft, bool]:
        """Return ``(plan, draft, judge_preapproved)`` for the next proposal.

        ``judge_preapproved`` is True only for a cached opening that the
        semantic judge accepted at cache-build time — the runtime may skip
        re-judging that frozen content.  ``rejection_notes`` carries the
        guard/judge reasons from a rejected attempt so the writer can fix
        them; a retry never reads the opening cache (the cached content was
        just rejected).
        """
        if allow_opening_cache and self.pack_cache is not None:
            cached = self.pack_cache.load_opening(pack.pack_hash)
            if cached is not None:
                try:
                    plan = validate_segment_plan(pack, state, cached.segment_plan, pacing)
                    draft = validate_segment_draft(plan, cached.segment_draft)
                    return plan, draft, cached.judge_preapproved
                except (ModelContractError, ProposalRejected) as exc:
                    detail = getattr(exc, "errors", None) or str(exc)
                    raise RuntimeGenerationUnavailable(
                        f"cached opening is invalid: {detail}"
                    ) from exc

        if self.unified_agent is not None:
            try:
                result = await self._await_with_heartbeats(
                    self.unified_agent.generate(
                        pack, state, pacing, rejection_notes=rejection_notes
                    ),
                    emit,
                    started,
                )
                plan = validate_segment_plan(pack, state, result.segment_plan, pacing)
                draft = validate_segment_draft(plan, result.segment_draft)
                return plan, draft, False
            except (ModelContractError, ProposalRejected) as exc:
                detail = getattr(exc, "errors", None) or str(exc)
                raise RuntimeGenerationUnavailable(
                    f"unified agent produced an invalid segment: {detail}"
                ) from exc
            except RuntimeGenerationUnavailable:
                raise
            except Exception as exc:
                raise RuntimeGenerationUnavailable(
                    "unified segment agent failed to generate"
                ) from exc

        try:
            plan = await self._await_with_heartbeats(
                self.director.plan_segment(pack, state, pacing), emit, started
            )
            plan = validate_segment_plan(pack, state, plan, pacing)
        except (ModelContractError, ProposalRejected) as exc:
            detail = getattr(exc, "errors", None) or str(exc)
            raise RuntimeGenerationUnavailable(
                f"director produced an invalid segment plan: {detail}"
            ) from exc
        except Exception as exc:
            raise RuntimeGenerationUnavailable("director failed to produce a segment plan") from exc

        try:
            draft = await self._await_with_heartbeats(
                self.writer.write_segment(pack, state, plan), emit, started
            )
            draft = validate_segment_draft(plan, draft)
        except (ModelContractError, ProposalRejected) as exc:
            detail = getattr(exc, "errors", None) or str(exc)
            raise RuntimeGenerationUnavailable(
                f"writer produced an invalid segment draft: {detail}"
            ) from exc
        except Exception as exc:
            raise RuntimeGenerationUnavailable("writer failed to produce a segment draft") from exc
        return plan, draft, False

    async def _await_with_heartbeats(
        self,
        awaitable: Awaitable[Any],
        emit: ProgressEmit,
        started: float,
    ) -> Any:
        """Await a long model call, emitting heartbeats while it is in flight."""
        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                try:
                    return await asyncio.wait_for(asyncio.shield(task), _HEARTBEAT_INTERVAL_SECONDS)
                except TimeoutError:
                    if task.done():
                        return task.result()
                    await emit(("heartbeat", {"elapsed_ms": _elapsed_ms(started)}))
        except BaseException:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise

    def _load_compatible_session(
        self,
        pack: CompiledScriptPack,
        session_id: str,
    ) -> SessionState:
        state = self.store.load_session(session_id)
        if state.pack_hash != pack.pack_hash:
            raise PackMismatch(f"session {session_id} is pinned to a different Script Pack Version")
        return state

    def _choice_event_at_revision(
        self,
        session_id: str,
        revision: int,
    ) -> str | None:
        if revision < 1:
            return None
        events = self.store.load_events(session_id, after_sequence=revision - 1)
        if not events or events[0].sequence != revision:
            return None
        if events[0].event.type != "player_action_selected":
            return None
        return events[0].event_id

    @staticmethod
    def _serialized_blocks(plan: SegmentPlan, draft: SegmentDraft) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for scene in draft.scene_drafts:
            for block in scene.blocks:
                blocks.append(
                    {
                        "segment_id": plan.segment_id,
                        "index": len(blocks),
                        "kind": block.kind,
                        "text": block.text,
                        "character_id": block.character_id,
                    }
                )
        return blocks

    @staticmethod
    def _committed_segment_events(
        result: dict[str, Any],
    ) -> tuple[tuple[str, Any], ...]:
        ready = result["segment_ready"]
        events: list[tuple[str, Any]] = [
            (
                "segment_started",
                {
                    "segment_id": result["segment_id"],
                    "expected_revision": ready["revision"],
                },
            )
        ]
        events.extend(("block", block) for block in result.get("blocks", ()))
        events.append(("segment_ready", ready))
        return tuple(events)
