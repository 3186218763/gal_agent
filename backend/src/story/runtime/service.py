"""Scene advance and choice resolution orchestration."""

from __future__ import annotations

from src.story.runtime.contracts import (
    ActionResult,
    DecisionRequired,
    InvalidChoice,
    ModelContractError,
    PackMismatch,
    PlannerPort,
    RuntimeRevisionConflict,
    RuntimeScene,
    RuntimeSessionEnded,
    WriterPort,
)
from src.story.runtime.endings import select_ending
from src.story.runtime.fallbacks import (
    fallback_ending_draft,
    fallback_resolution,
    fallback_scene_draft,
    fallback_scene_plan,
)
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
    SceneAcknowledged,
    SceneCommitted,
    SessionEnded,
    SessionState,
    SessionStatus,
)
from src.story.storage import RevisionConflict, StoryEventStore


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
    ) -> RuntimeScene:
        initial = self._load_matching(pack, session_id, expected_revision)
        for attempt in range(2):
            state = initial if attempt == 0 else self.store.load_session(session_id)
            try:
                return await self._advance_once(pack, state)
            except RevisionConflict as exc:
                if attempt == 1:
                    raise RuntimeRevisionConflict(str(exc)) from exc
        raise AssertionError("advance retry loop exhausted")

    async def _advance_once(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> RuntimeScene:
        if state.status == SessionStatus.ENDED:
            raise RuntimeSessionEnded(state.session_id)
        if state.pending_decision is not None:
            raise DecisionRequired(state.pending_decision.decision_id)
        if state.pending_scene is not None:
            state, _ = self.store.append(
                state.session_id,
                state.revision,
                [SceneAcknowledged(scene_id=state.pending_scene.scene_id)],
            )
        ending = select_ending(pack, state)
        if ending is not None:
            return await self._commit_ending(pack, state, ending)
        try:
            proposed = await self.planner.plan_scene(pack, state)
            plan = validate_scene_plan(pack, state, proposed)
        except (ModelContractError, ProposalRejected):
            plan = validate_scene_plan(pack, state, fallback_scene_plan(pack, state))
        try:
            written = await self.writer.write_scene(pack, state, plan)
            draft = validate_scene_draft(plan, written)
        except (ModelContractError, ProposalRejected):
            draft = validate_scene_draft(plan, fallback_scene_draft(plan))
        events = simulate_scene(pack, state, plan, draft)
        updated, _ = self.store.append(state.session_id, state.revision, events)
        return RuntimeScene.from_committed(updated, events[-1])

    async def select_choice(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        choice_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> ActionResult:
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
        except (ModelContractError, ProposalRejected):
            resolution = validate_action_resolution(
                pack,
                state,
                fallback_resolution(choice),
                expected_action_id=choice.action_id,
            )
        events = simulate_resolution(state, choice, resolution, idempotency_key)
        try:
            updated, _ = self.store.append(session_id, state.revision, events)
        except RevisionConflict as exc:
            raise RuntimeRevisionConflict(str(exc)) from exc
        return ActionResult(
            session_id=session_id,
            revision=updated.revision,
            action_id=resolution.action_id,
            outcome=resolution.outcome,
        )

    async def _commit_ending(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        ending: EndingSource,
    ) -> RuntimeScene:
        try:
            draft = await self.writer.write_ending(pack, state, ending)
            if draft.ending_id != ending.id:
                raise ModelContractError("writer changed ending id")
        except ModelContractError:
            draft = fallback_ending_draft(ending)
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
        updated, _ = self.store.append(state.session_id, state.revision, events)
        return RuntimeScene.from_committed(updated, committed)
