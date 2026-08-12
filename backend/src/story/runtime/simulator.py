"""Pure proposal-to-event simulation on copied session state."""

from __future__ import annotations

from src.story.runtime.contracts import ActionResolution, SceneDraft, ScenePlan
from src.story.runtime.endings import next_phase
from src.story.runtime.segment_contracts import (
    SegmentDraft,
    SegmentPlan,
    ThreadOperation,
)
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import (
    ActionResolved,
    CharacterLearnedFact,
    DecisionPresented,
    EndingGenerated,
    EventEnvelope,
    FactCommitted,
    FactEvidenced,
    FactRevealed,
    GoalAdvanced,
    NarrativeThread,
    PhaseAdvanced,
    PlayerActionSelected,
    PresentedChoice,
    RelationshipChanged,
    SceneAcknowledged,
    SceneCommitted,
    SessionState,
    StateTransitionError,
    ThreadAdvanced,
    ThreadClosed,
    ThreadOpened,
    ThreadStatus,
    apply_events,
)
from src.story.state.events import StoryEvent


def scene_events(pack, state, plan, draft) -> tuple[StoryEvent, ...]:
    del pack  # pack reserved for future pack-aware scene conversion
    events: list[StoryEvent] = []
    phase = next_phase(state)
    if phase is not None:
        events.append(PhaseAdvanced(phase=phase))
    for fact in plan.fact_commits:
        evidence = (
            (plan.scene_id,)
            if fact.reason == "first_irreversible_evidence" or fact.reveal
            else ()
        )
        events.append(FactCommitted(fact_id=fact.fact_id, value=fact.value, evidence_event_ids=evidence))
        if fact.reveal:
            events.append(FactRevealed(fact_id=fact.fact_id))
        events.extend(
            CharacterLearnedFact(character_id=character_id, fact_id=fact.fact_id)
            for character_id in fact.learned_by
        )
    written = {item.option_id: item for item in draft.choices}
    choices = tuple(
        PresentedChoice(
            id=item.option_id,
            action_id=item.action_id,
            label=written[item.option_id].label,
            intent=item.intent,
            target_character_id=item.target_character_id,
            preview=written[item.option_id].preview,
        )
        for item in plan.choices
    )
    events.append(
        SceneCommitted(
            scene_id=plan.scene_id,
            terminal=plan.terminal,
            location_id=plan.location_id,
            present_character_ids=plan.present_character_ids,
            blocks=draft.blocks,
            decision_id=plan.decision_id,
            choices=choices,
        )
    )
    return tuple(events)


def simulate_events(state: SessionState, events: tuple[StoryEvent, ...]) -> None:
    envelopes = tuple(
        EventEnvelope(
            event_id=f"simulation-{state.revision + index}",
            session_id=state.session_id,
            sequence=state.revision + index,
            event=event,
        )
        for index, event in enumerate(events, start=1)
    )
    candidate = apply_events(state, envelopes)
    if candidate.world.scene_count > candidate.world.max_scenes:
        raise StateTransitionError("simulation exceeded max scene count")


def simulate_scene(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: ScenePlan,
    draft: SceneDraft,
) -> tuple[StoryEvent, ...]:
    events = scene_events(pack, state, plan, draft)
    simulate_events(state, events)
    return events


def resolution_effect_events(
    state: SessionState,
    resolution: ActionResolution,
) -> tuple[StoryEvent, ...]:
    events: list[StoryEvent] = [
        RelationshipChanged(
            character_id=item.character_id,
            axis=item.axis,
            delta=item.delta,
        )
        for item in resolution.relationship_deltas
    ]
    events.extend(
        GoalAdvanced(goal_id=item.goal_id, delta=item.delta)
        for item in resolution.goal_deltas
    )
    events.extend(
        FactEvidenced(
            fact_id=fact_id,
            evidence_event_id=f"action:{state.session_id}:{state.revision + 1}:{fact_id}",
        )
        for fact_id in resolution.evidence_fact_ids
    )
    events.extend(FactRevealed(fact_id=fact_id) for fact_id in resolution.reveal_fact_ids)
    for entry in sorted(resolution.learned_facts, key=lambda item: item.character_id):
        events.extend(
            CharacterLearnedFact(character_id=entry.character_id, fact_id=fact_id)
            for fact_id in sorted(entry.fact_ids)
        )
    return tuple(events)


def simulate_resolution(
    state: SessionState,
    choice: PresentedChoice,
    resolution: ActionResolution,
    idempotency_key: str,
) -> tuple[StoryEvent, ...]:
    if state.pending_decision is None:
        raise StateTransitionError("no decision is pending")
    events: tuple[StoryEvent, ...] = (
        PlayerActionSelected(
            decision_id=state.pending_decision.decision_id,
            option_id=choice.id,
            idempotency_key=idempotency_key,
        ),
        ActionResolved(action_id=resolution.action_id, outcome=resolution.outcome),
        *resolution_effect_events(state, resolution),
    )
    simulate_events(state, events)
    return events


# ---------------------------------------------------------------------------
# Segment-level simulation
# ---------------------------------------------------------------------------


def _thread_op_events(
    state: SessionState,
    ops: tuple[ThreadOperation, ...],
) -> list[StoryEvent]:
    events: list[StoryEvent] = []
    for op in ops:
        if op.kind == "open":
            thread = NarrativeThread(
                id=op.thread_id,
                type=op.thread_type,
                introduced_at=f"session:{state.session_id}:rev:{state.revision}",
                involved_character_ids=op.involved_character_ids,
                related_fact_ids=op.related_fact_ids,
            )
            events.append(ThreadOpened(thread=thread))
        elif op.kind == "advance":
            events.append(
                ThreadAdvanced(thread_id=op.thread_id, urgency=op.urgency)
            )
        elif op.kind == "close":
            status = (
                ThreadStatus.RESOLVED
                if op.close_status == "resolved"
                else ThreadStatus.ABANDONED
            )
            events.append(ThreadClosed(thread_id=op.thread_id, status=status))
    return events


def next_phase_for_count(
    state: SessionState,
    projected_scene_count: int,
):
    """Check if phase should advance based on projected scene count."""
    return next_phase(state, projected_count=projected_scene_count)


def segment_events(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    draft: SegmentDraft,
) -> tuple[StoryEvent, ...]:
    del pack  # reserved for future pack-aware segment conversion
    events: list[StoryEvent] = []
    current_scene_count = state.world.scene_count

    # At most one PhaseAdvanced per segment (based on first scene projected count).
    projected_count = current_scene_count + 1
    phase = next_phase_for_count(state, projected_count)
    if phase is not None:
        events.append(PhaseAdvanced(phase=phase))

    # Thread operations (before scenes so they are available for reference).
    events.extend(_thread_op_events(state, plan.thread_ops))

    # Build per-scene events with auto-acknowledge between scenes.
    for i, (scene_plan, scene_draft) in enumerate(
        zip(plan.scenes, draft.scene_drafts)
    ):
        if i > 0:
            events.append(
                SceneAcknowledged(scene_id=plan.scenes[i - 1].scene_id)
            )

        # Fact commits for this scene.
        for fact in scene_plan.fact_commits:
            evidence = (
                (scene_plan.scene_id,)
                if fact.reason == "first_irreversible_evidence" or fact.reveal
                else ()
            )
            events.append(
                FactCommitted(
                    fact_id=fact.fact_id,
                    value=fact.value,
                    evidence_event_ids=evidence,
                )
            )
            if fact.reveal:
                events.append(FactRevealed(fact_id=fact.fact_id))
            events.extend(
                CharacterLearnedFact(character_id=cid, fact_id=fact.fact_id)
                for cid in fact.learned_by
            )

        # For ending segments, EndingGenerated carries all blocks and does
        # not increment scene_count.  Skip SceneCommitted for every scene so
        # that multi-scene endings at max_scenes do not trip the reducer's
        # scene_count < max_scenes guard.
        if plan.terminal == "ending":
            continue

        # SceneCommitted with content only (terminal/decision/choices handled separately).
        events.append(
            SceneCommitted(
                scene_id=scene_plan.scene_id,
                location_id=scene_plan.location_id,
                present_character_ids=scene_plan.present_character_ids,
                blocks=scene_draft.blocks,
            )
        )

    # Decision terminal events.
    if plan.terminal == "decision":
        last_scene = plan.scenes[-1]
        if last_scene.choices:
            written_map = {wc.option_id: wc for wc in draft.choices}
            choices = tuple(
                PresentedChoice(
                    id=choice_plan.option_id,
                    action_id=choice_plan.action_id,
                    label=written_map[choice_plan.option_id].label,
                    intent=choice_plan.intent,
                    target_character_id=choice_plan.target_character_id,
                    preview=written_map[choice_plan.option_id].preview,
                )
                for choice_plan in last_scene.choices
            )
        else:
            # Segment-level decision: choices come from the draft.
            choices = tuple(
                PresentedChoice(
                    id=wc.option_id,
                    action_id=wc.option_id,
                    label=wc.label,
                    intent=wc.label,
                    preview=wc.preview,
                )
                for wc in draft.choices
            )
        decision_id = (
            last_scene.decision_id
            if last_scene.decision_id
            else f"dec_{plan.segment_id}"
        )
        events.append(DecisionPresented(decision_id=decision_id, choices=choices))

    # Ending terminal events.
    elif plan.terminal == "ending":
        ending_draft = draft.ending
        if ending_draft is not None:
            proposal = plan.ending_proposal
            events.append(
                EndingGenerated(
                    ending_id=ending_draft.ending_id,
                    title=ending_draft.title,
                    tone=(
                        ending_draft.tone
                        or (proposal.tone if proposal else "")
                        or "neutral"
                    ),
                    terminal_state_summary=(
                        ending_draft.terminal_state_summary
                        or (proposal.terminal_state_summary if proposal else "")
                    ),
                    blocks=ending_draft.blocks,
                )
            )

    return tuple(events)


def simulate_segment(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    draft: SegmentDraft,
) -> tuple[StoryEvent, ...]:
    events = segment_events(pack, state, plan, draft)
    simulate_events(state, events)
    return events
