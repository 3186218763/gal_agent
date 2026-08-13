"""Scene advance and choice resolution orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator
from typing import Any

from src.story.runtime.contracts import (
    ActionResult,
    DecisionRequired,
    InvalidChoice,
    ModelContractError,
    PackMismatch,
    PlannerPort,
    RuntimeGenerationUnavailable,
    RuntimeRevisionConflict,
    RuntimeScene,
    RuntimeSessionEnded,
    ScenePlan,
    SegmentPlan,
    StreamingGeneratorPort,
    WriterPort,
)
from src.story.runtime.endings import next_phase, select_ending
from src.story.runtime.simulator import simulate_events, simulate_resolution, simulate_scene
from src.story.runtime.validator import (
    ProposalRejected,
    validate_action_resolution,
    validate_scene_draft,
    validate_scene_plan,
)
from src.story.script_pack.models import CompiledScriptPack, EndingSource
from src.story.state import (
    EndingEntered,
    EndingRuntime,
    EventEnvelope,
    NarrativeBlock,
    PhaseAdvanced,
    PresentedChoice,
    SceneAcknowledged,
    SceneCommitted,
    SessionEnded,
    SessionState,
    SessionStatus,
    apply_events,
)
from src.story.state.events import StoryEvent
from src.story.storage import StoryEventStore


def _command_fingerprint(kind: str, expected_revision: int, choice_id: str | None = None) -> str:
    payload = {"kind": kind, "expected_revision": expected_revision, "choice_id": choice_id}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _trivial_segment_plan(state: SessionState) -> SegmentPlan:
    """Build a minimal single-scene SegmentPlan for the legacy streaming path.

    The single scene continues the story without a decision, so the adapter
    cannot invent choices, facts, or terminal states.  The segment-level
    ``terminal`` literal must be one of "decision"/"ending"; a "continue"
    scene under a "decision" segment keeps validation satisfied.
    """
    return SegmentPlan(
        segment_id=f"scene-{state.revision}",
        scenes=(
            ScenePlan(
                scene_id=f"scene_{state.revision + 1}",
                summary="Continue the story from the current state.",
                location_id=state.world.location_id,
                present_character_ids=state.world.present_character_ids,
                terminal="continue",
            ),
        ),
        terminal="decision",
    )


def _translate_segment_complete(
    complete_data: dict[str, Any],
    plan: SegmentPlan,
) -> dict[str, Any]:
    """Translate a SegmentWriterOutput ``complete`` payload to the legacy scene shape.

    The new adapter yields ``{"segment_draft": {...}}`` while the legacy
    streaming flow expects ``terminal``/``choices``/``scene_id`` keys.
    Terminal is derived from the plan: a decision scene exposes the draft's
    choices; anything else downgrades to ``terminal="continue"`` with empty
    choices.
    """
    segment_draft = complete_data.get("segment_draft") or {}
    scene_drafts = segment_draft.get("scene_drafts") or []
    last_scene = plan.scenes[-1]
    translated: dict[str, Any] = {
        "scene_id": (scene_drafts[0]["scene_id"] if scene_drafts else plan.scenes[0].scene_id),
    }
    if last_scene.terminal == "decision":
        translated["terminal"] = "decision"
        translated["decision_id"] = last_scene.decision_id
        translated["choices"] = list(segment_draft.get("choices", []))
    else:
        translated["terminal"] = "continue"
        translated["choices"] = []
    return translated


class RuntimeService:
    def __init__(
        self,
        store: StoryEventStore,
        planner: PlannerPort,
        writer: WriterPort,
        generator: StreamingGeneratorPort | None = None,
    ) -> None:
        self.store = store
        self.planner = planner
        self.writer = writer
        self.generator = generator

    def _load_matching(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
    ) -> SessionState:
        state = self.store.load_session(session_id)
        if state.pack_id != pack.source.identity.id or state.pack_hash != pack.pack_hash:
            raise PackMismatch(session_id)
        if state.revision != expected_revision:
            raise RuntimeRevisionConflict(
                f"session {session_id}: expected {expected_revision}, current {state.revision}"
            )
        return state

    async def advance(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> RuntimeScene:
        fingerprint = _command_fingerprint("advance", expected_revision)
        claim = self.store.claim_command(session_id, idempotency_key, "advance", fingerprint)
        if claim.replay_json is not None:
            return RuntimeScene.model_validate_json(claim.replay_json)
        try:
            initial = self._load_matching(pack, session_id, expected_revision)
            if initial.status == SessionStatus.ENDED:
                raise RuntimeSessionEnded(session_id)
            if initial.pending_decision is not None:
                raise DecisionRequired(initial.pending_decision.decision_id)
            state = initial
            events: tuple[StoryEvent, ...] = ()
            if state.pending_scene is not None:
                ack = SceneAcknowledged(scene_id=state.pending_scene.scene_id)
                synthetic = EventEnvelope(
                    event_id=f"synthetic-ack-{state.session_id}-{state.revision + 1}",
                    session_id=state.session_id,
                    sequence=state.revision + 1,
                    event=ack,
                )
                state = apply_events(state, (synthetic,))
                events = (ack,)
            ending = select_ending(pack, state)
            if ending is not None:
                events = (*events, *await self._build_ending_events(pack, state, ending))
            else:
                try:
                    proposed = await self.planner.plan_scene(pack, state)
                    plan = validate_scene_plan(pack, state, proposed)
                except (ModelContractError, ProposalRejected) as exc:
                    raise RuntimeGenerationUnavailable(
                        "the model could not produce a valid scene plan"
                    ) from exc
                try:
                    written = await self.writer.write_scene(pack, state, plan)
                    draft = validate_scene_draft(plan, written)
                except (ModelContractError, ProposalRejected) as exc:
                    raise RuntimeGenerationUnavailable(
                        "the model could not produce a valid scene draft"
                    ) from exc
                events = (*events, *simulate_scene(pack, state, plan, draft))

            def result_factory(updated: SessionState, envelopes) -> str:
                committed = next(
                    envelope.event
                    for envelope in envelopes
                    if isinstance(envelope.event, SceneCommitted)
                )
                return RuntimeScene.from_committed(updated, committed).model_dump_json()

            _, _, result_json = self.store.commit_command(
                session_id,
                idempotency_key,
                "advance",
                fingerprint,
                expected_revision,
                events,
                result_factory,
            )
            return RuntimeScene.model_validate_json(result_json)
        except Exception:
            self.store.release_command(session_id, idempotency_key, "advance", fingerprint)
            raise

    async def select_choice(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        choice_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> ActionResult:
        fingerprint = _command_fingerprint("choice", expected_revision, choice_id)
        claim = self.store.claim_command(session_id, idempotency_key, "choice", fingerprint)
        if claim.replay_json is not None:
            return ActionResult.model_validate_json(claim.replay_json)
        try:
            state = self._load_matching(pack, session_id, expected_revision)
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
            try:
                proposed = await self.planner.resolve_action(pack, state, choice)
                resolution = validate_action_resolution(
                    pack,
                    state,
                    proposed,
                    expected_action_id=choice.action_id,
                )
            except (ModelContractError, ProposalRejected) as exc:
                raise RuntimeGenerationUnavailable(
                    "the model could not produce a valid resolution"
                ) from exc
            events = simulate_resolution(state, choice, resolution, idempotency_key)

            def result_factory(updated: SessionState, envelopes) -> str:
                del envelopes
                return ActionResult(
                    session_id=session_id,
                    revision=updated.revision,
                    action_id=resolution.action_id,
                    outcome=resolution.outcome,
                ).model_dump_json()

            _, _, result_json = self.store.commit_command(
                session_id,
                idempotency_key,
                "choice",
                fingerprint,
                expected_revision,
                events,
                result_factory,
            )
            return ActionResult.model_validate_json(result_json)
        except Exception:
            self.store.release_command(session_id, idempotency_key, "choice", fingerprint)
            raise

    async def _build_ending_events(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        ending: EndingSource,
    ) -> tuple[StoryEvent, ...]:
        try:
            draft = await self.writer.write_ending(pack, state, ending)
            if draft.ending_id != ending.id:
                raise ModelContractError("writer changed ending id")
        except ModelContractError as exc:
            raise RuntimeGenerationUnavailable(
                "the model could not produce a valid ending"
            ) from exc
        ending_runtime = EndingRuntime(
            ending_id=ending.id,
            entered_at_revision=state.revision + 1,
            required_payoffs=ending.required_outcomes,
            final_scene_budget=1,
            title=draft.title,
            blocks=draft.blocks,
        )
        committed = SceneCommitted(
            scene_id=f"ending_{ending.id}_{state.revision + 1}",
            terminal="ending",
            location_id=state.world.location_id,
            present_character_ids=state.world.present_character_ids,
            blocks=draft.blocks,
        )
        events = (
            EndingEntered(ending=ending_runtime),
            committed,
            SessionEnded(ending_id=ending.id),
        )
        simulate_events(state, events)
        return events

    async def _legacy_generate_scene_wrapper(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        """Compatibility wrapper (brief Step 8, Option B) for the legacy
        generate_scene call site.

        Generators that only implement the legacy ``generate_scene`` protocol
        (e.g. test fakes) are used unchanged.  Plan-aware generators receive a
        minimal single-scene SegmentPlan and their ``complete`` payload
        (a SegmentWriterOutput dump) is translated back into the legacy scene
        shape expected by the streaming advance flow.
        """
        if not hasattr(self.generator, "generate_segment"):
            async for event_type, data in self.generator.generate_scene(pack, state):
                yield event_type, data
            return
        plan = _trivial_segment_plan(state)
        async for event_type, data in self.generator.generate_segment(pack, state, plan):
            if event_type == "complete":
                data = _translate_segment_complete(data, plan)
            yield event_type, data

    async def advance_streamed(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Streaming version of advance: yields ('block', dict), ('choices', list),
        and ('done', {'session_id': ..., 'revision': ...})."""
        if self.generator is None:
            raise RuntimeError("streaming generator is not configured")

        fingerprint = _command_fingerprint("advance", expected_revision)
        claim = self.store.claim_command(session_id, idempotency_key, "advance", fingerprint)
        if claim.replay_json is not None:
            replay = json.loads(claim.replay_json)
            for block in replay.get("blocks", []):
                yield ("block", block)
            choices = replay.get("choices", [])
            if choices:
                yield ("choices", choices)
            yield ("done", {"session_id": session_id, "revision": replay["revision"]})
            return

        try:
            initial = self._load_matching(pack, session_id, expected_revision)
            if initial.status == SessionStatus.ENDED:
                raise RuntimeSessionEnded(session_id)
            if initial.pending_decision is not None:
                raise DecisionRequired(initial.pending_decision.decision_id)

            state = initial
            events: list[StoryEvent] = []
            if state.pending_scene is not None:
                ack = SceneAcknowledged(scene_id=state.pending_scene.scene_id)
                synthetic = EventEnvelope(
                    event_id=f"synthetic-ack-{state.session_id}-{state.revision + 1}",
                    session_id=state.session_id,
                    sequence=state.revision + 1,
                    event=ack,
                )
                state = apply_events(state, (synthetic,))
                events.append(ack)

            ending = select_ending(pack, state)
            if ending is not None:
                async for evt, data in self._stream_ending(
                    pack,
                    state,
                    ending,
                    idempotency_key,
                    fingerprint,
                    events,
                    expected_revision,
                ):
                    yield (evt, data)
                return

            # Stream regular scene
            collected_blocks: list = []
            complete_data: dict[str, Any] | None = None
            try:
                async for event_type, data in self._legacy_generate_scene_wrapper(pack, state):
                    if event_type == "block":
                        collected_blocks.append(data)
                        yield ("block", data)
                    elif event_type == "complete":
                        complete_data = data
            except ModelContractError as exc:
                raise RuntimeGenerationUnavailable("streaming generator failed") from exc

            if complete_data is None:
                raise RuntimeGenerationUnavailable("stream ended without complete data")

            # Validate blocks lightly
            if not collected_blocks:
                raise RuntimeGenerationUnavailable("model produced no blocks")
            for blk in collected_blocks:
                if not blk.get("text", "").strip():
                    raise RuntimeGenerationUnavailable("empty block text")

            terminal = complete_data.get("terminal", "decision")
            raw_choices = complete_data.get("choices", [])
            if terminal == "decision" and not (2 <= len(raw_choices) <= 4):
                terminal = "continue"
                raw_choices = []

            choice_tuple = tuple(
                PresentedChoice(
                    id=c.get("option_id", f"opt_{i}"),
                    action_id=c.get("action_id", "observe"),
                    label=c.get("label", "..."),
                    intent=c.get("intent", ""),
                    target_character_id=c.get("target_character_id"),
                    preview=c.get("preview"),
                )
                for i, c in enumerate(raw_choices)
            )

            phase = next_phase(state)
            if phase is not None:
                events.append(PhaseAdvanced(phase=phase))

            committed = SceneCommitted(
                scene_id=complete_data.get("scene_id", f"scene_{state.revision + 1}"),
                terminal=terminal,
                location_id=state.world.location_id,
                present_character_ids=state.world.present_character_ids,
                blocks=tuple(
                    NarrativeBlock(
                        kind=b.get("kind", "narration"),
                        text=b["text"],
                        character_id=b.get("character_id"),
                    )
                    for b in collected_blocks
                ),
                decision_id=complete_data.get("decision_id") if terminal == "decision" else None,
                choices=choice_tuple,
            )
            events.append(committed)

            def result_factory(updated: SessionState, envelopes) -> str:
                scene_event = next(
                    (e for e in envelopes if isinstance(e.event, SceneCommitted)),
                    None,
                )
                if scene_event is None:
                    raise RuntimeError("no SceneCommitted in committed events")
                return RuntimeScene.from_committed(updated, scene_event.event).model_dump_json()

            updated_state, _, _ = self.store.commit_command(
                session_id,
                idempotency_key,
                "advance",
                fingerprint,
                expected_revision,
                tuple(events),
                result_factory,
            )

            if choice_tuple:
                yield ("choices", [c.model_dump(mode="json") for c in choice_tuple])
            yield ("done", {"session_id": session_id, "revision": updated_state.revision})

        except Exception:
            self.store.release_command(session_id, idempotency_key, "advance", fingerprint)
            raise

    async def _stream_ending(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        ending: EndingSource,
        idempotency_key: str,
        fingerprint: str,
        prior_events: list[StoryEvent],
        expected_revision: int,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Stream ending blocks using the batch writer.

        The caller (advance_streamed) has already claimed the command receipt;
        this helper uses the same idempotency_key and fingerprint to commit.
        ``prior_events`` contains events accumulated before the ending check
        (e.g. ``SceneAcknowledged``).  ``expected_revision`` is the store's
        original revision that ``commit_command`` must match.
        """
        try:
            draft = await self.writer.write_ending(pack, state, ending)
            if draft.ending_id != ending.id:
                raise ModelContractError("writer changed ending id")
        except ModelContractError as exc:
            raise RuntimeGenerationUnavailable(
                "the model could not produce a valid ending"
            ) from exc

        for block in draft.blocks:
            yield ("block", block.model_dump(mode="json"))

        ending_runtime = EndingRuntime(
            ending_id=ending.id,
            entered_at_revision=state.revision + 1,
            required_payoffs=ending.required_outcomes,
            final_scene_budget=1,
            title=draft.title,
            blocks=draft.blocks,
        )
        committed = SceneCommitted(
            scene_id=f"ending_{ending.id}_{state.revision + 1}",
            terminal="ending",
            location_id=state.world.location_id,
            present_character_ids=state.world.present_character_ids,
            blocks=draft.blocks,
        )
        ending_events = (
            EndingEntered(ending=ending_runtime),
            committed,
            SessionEnded(ending_id=ending.id),
        )
        events = tuple(prior_events) + ending_events
        simulate_events(state, ending_events)

        def result_factory(updated: SessionState, envelopes) -> str:
            scene_event = next(
                (e for e in envelopes if isinstance(e.event, SceneCommitted)),
                None,
            )
            if scene_event is None:
                raise RuntimeError("no SceneCommitted in committed events")
            return RuntimeScene.from_committed(updated, scene_event.event).model_dump_json()

        updated_state, _, _ = self.store.commit_command(
            state.session_id,
            idempotency_key,
            "advance",
            fingerprint,
            expected_revision,
            events,
            result_factory,
        )
        yield (
            "done",
            {
                "session_id": state.session_id,
                "revision": updated_state.revision,
                "ending_id": ending.id,
                "ending_title": draft.title,
            },
        )
