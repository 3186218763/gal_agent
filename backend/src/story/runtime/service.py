"""Scene advance and choice resolution orchestration."""

from __future__ import annotations

import hashlib
import json

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
    WriterPort,
)
from src.story.runtime.endings import select_ending
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


class RuntimeService:
    def __init__(
        self,
        store: StoryEventStore,
        planner: PlannerPort,
        writer: WriterPort,
    ) -> None:
        self.store = store
        self.planner = planner
        self.writer = writer

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
