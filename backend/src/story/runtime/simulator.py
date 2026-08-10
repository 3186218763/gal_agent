"""Pure proposal-to-event simulation on copied session state."""

from __future__ import annotations

from src.story.runtime.contracts import ActionResolution, SceneDraft, ScenePlan
from src.story.runtime.endings import next_phase
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import (
    ActionResolved,
    CharacterLearnedFact,
    EventEnvelope,
    FactCommitted,
    FactEvidenced,
    FactRevealed,
    GoalAdvanced,
    PhaseAdvanced,
    PlayerActionSelected,
    PresentedChoice,
    RelationshipChanged,
    SceneCommitted,
    SessionState,
    StateTransitionError,
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
    for character_id in sorted(resolution.learned_facts):
        events.extend(
            CharacterLearnedFact(character_id=character_id, fact_id=fact_id)
            for fact_id in sorted(resolution.learned_facts[character_id])
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
