from __future__ import annotations

from collections.abc import Iterable

from src.story.state.events import (
    ActionResolved,
    BeliefChanged,
    CharacterLearnedFact,
    CompletionEvaluated,
    DecisionPresented,
    EndingEntered,
    EndingGenerated,
    EventEnvelope,
    FactCommitted,
    FactEvidenced,
    FactRevealed,
    GoalAdvanced,
    PhaseAdvanced,
    PlayerActionSelected,
    RelationshipChanged,
    SceneAcknowledged,
    SceneCommitted,
    SessionEnded,
    ThreadAdvanced,
    ThreadClosed,
    ThreadOpened,
)
from src.story.state.models import (
    CompletionState,
    EndingRuntime,
    FactTruthStatus,
    FactVisibility,
    GoalStatus,
    PendingDecisionReference,
    PendingSceneReference,
    SessionState,
    SessionStatus,
    StoryPhase,
    ThreadStatus,
)


class StateTransitionError(ValueError):
    """A domain event violates a story-state invariant."""


_PHASES = (
    StoryPhase.OPENING,
    StoryPhase.EXPLORATION,
    StoryPhase.ESCALATION,
    StoryPhase.CRISIS,
    StoryPhase.RESOLUTION,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StateTransitionError(message)


def apply_event(state: SessionState, envelope: EventEnvelope) -> SessionState:
    _require(
        envelope.session_id == state.session_id,
        "event session does not match state session",
    )
    _require(
        envelope.sequence == state.revision + 1,
        f"expected event sequence {state.revision + 1}, got {envelope.sequence}",
    )
    _require(state.status != SessionStatus.ENDED, "ended session cannot accept new events")

    event = envelope.event
    next_state = state

    if isinstance(event, SceneCommitted):
        _require(next_state.pending_scene is None, "a scene is already pending")
        is_ending = event.terminal == "ending"
        if is_ending:
            _require(next_state.ending is not None, "ending scene requires entered ending")
            _require(event.decision_id is None and not event.choices, "ending scene cannot decide")
        else:
            _require(
                next_state.world.scene_count < next_state.world.max_scenes,
                "max scene count reached",
            )
        is_decision = event.terminal == "decision"
        _require(
            is_decision == (event.decision_id is not None),
            "decision_id must be present only for a decision scene",
        )
        _require(
            (is_decision and 2 <= len(event.choices) <= 4)
            or (not is_decision and not event.choices),
            "decision scenes require 2-4 choices and other scenes require none",
        )
        choice_ids = [item.id for item in event.choices]
        _require(len(choice_ids) == len(set(choice_ids)), "choice ids must be unique")
        world = next_state.world.model_copy(
            update={
                "location_id": event.location_id,
                "present_character_ids": event.present_character_ids,
                "scene_count": (
                    next_state.world.scene_count if is_ending else next_state.world.scene_count + 1
                ),
            }
        )
        pending_scene = PendingSceneReference(
            scene_id=event.scene_id,
            revision=envelope.sequence,
            terminal=event.terminal,
            blocks=event.blocks,
        )
        pending_decision = (
            PendingDecisionReference(
                decision_id=event.decision_id,
                scene_id=event.scene_id,
                revision=envelope.sequence,
                choices=event.choices,
            )
            if is_decision
            else None
        )
        next_state = next_state.model_copy(
            update={
                "world": world,
                "pending_scene": pending_scene,
                "pending_decision": pending_decision,
            }
        )

    elif isinstance(event, SceneAcknowledged):
        _require(next_state.pending_scene is not None, "no scene is pending")
        _require(
            next_state.pending_scene.scene_id == event.scene_id,
            "scene acknowledgement does not match pending scene",
        )
        _require(
            next_state.pending_decision is None,
            "decision scenes are acknowledged by player_action_selected",
        )
        next_state = next_state.model_copy(update={"pending_scene": None})

    elif isinstance(event, PlayerActionSelected):
        _require(next_state.pending_decision is not None, "no decision is pending")
        _require(
            next_state.pending_decision.decision_id == event.decision_id,
            "player action does not match pending decision",
        )
        offered_ids = {item.id for item in next_state.pending_decision.choices}
        _require(event.option_id in offered_ids, "player choice was not offered")
        next_state = next_state.model_copy(update={"pending_scene": None, "pending_decision": None})

    elif isinstance(event, ActionResolved):
        pass

    elif isinstance(event, FactCommitted):
        _require(event.fact_id in next_state.facts, "unknown fact")
        current = next_state.facts[event.fact_id]
        _require(
            current.truth_status in {FactTruthStatus.POSSIBLE, FactTruthStatus.STAGED},
            f"fact {event.fact_id} is already committed",
        )
        evidence = tuple(dict.fromkeys(event.evidence_event_ids))
        visibility = FactVisibility.EVIDENCED if evidence else FactVisibility.HIDDEN
        updated = current.model_copy(
            update={
                "truth_status": FactTruthStatus.COMMITTED,
                "value": event.value,
                "visibility": visibility,
                "evidence_event_ids": evidence,
                "committed_by_event_id": envelope.event_id,
            }
        )
        facts = dict(next_state.facts)
        facts[event.fact_id] = updated
        next_state = next_state.model_copy(update={"facts": facts})

    elif isinstance(event, FactEvidenced):
        _require(event.fact_id in next_state.facts, "unknown fact")
        current = next_state.facts[event.fact_id]
        _require(
            current.truth_status == FactTruthStatus.COMMITTED,
            "evidence can only attach to a committed fact",
        )
        evidence = tuple(dict.fromkeys((*current.evidence_event_ids, event.evidence_event_id)))
        visibility = (
            current.visibility
            if current.visibility == FactVisibility.REVEALED
            else FactVisibility.EVIDENCED
        )
        facts = dict(next_state.facts)
        facts[event.fact_id] = current.model_copy(
            update={"evidence_event_ids": evidence, "visibility": visibility}
        )
        next_state = next_state.model_copy(update={"facts": facts})

    elif isinstance(event, FactRevealed):
        _require(event.fact_id in next_state.facts, "unknown fact")
        current = next_state.facts[event.fact_id]
        _require(
            current.truth_status == FactTruthStatus.COMMITTED,
            "only a committed fact can be revealed",
        )
        _require(
            len(current.evidence_event_ids) >= current.evidence_required,
            f"fact {event.fact_id} lacks required evidence",
        )
        facts = dict(next_state.facts)
        facts[event.fact_id] = current.model_copy(update={"visibility": FactVisibility.REVEALED})
        next_state = next_state.model_copy(update={"facts": facts})

    elif isinstance(event, CharacterLearnedFact):
        _require(event.character_id in next_state.characters, "unknown character")
        _require(event.fact_id in next_state.facts, "unknown fact")
        _require(
            next_state.facts[event.fact_id].truth_status == FactTruthStatus.COMMITTED,
            "character cannot learn an uncommitted fact",
        )
        character = next_state.characters[event.character_id]
        characters = dict(next_state.characters)
        characters[event.character_id] = character.model_copy(
            update={"knowledge": character.knowledge | {event.fact_id}}
        )
        fact = next_state.facts[event.fact_id]
        facts = dict(next_state.facts)
        facts[event.fact_id] = fact.model_copy(
            update={"known_by": fact.known_by | {event.character_id}}
        )
        next_state = next_state.model_copy(update={"characters": characters, "facts": facts})

    elif isinstance(event, BeliefChanged):
        _require(event.character_id in next_state.characters, "unknown character")
        character = next_state.characters[event.character_id]
        beliefs = dict(character.beliefs)
        beliefs[event.belief_id] = event.belief
        characters = dict(next_state.characters)
        characters[event.character_id] = character.model_copy(update={"beliefs": beliefs})
        next_state = next_state.model_copy(update={"characters": characters})

    elif isinstance(event, RelationshipChanged):
        _require(
            event.character_id in next_state.world.relationships,
            "unknown relationship character",
        )
        relationships = {key: dict(value) for key, value in next_state.world.relationships.items()}
        current = relationships[event.character_id].get(event.axis, 0)
        relationships[event.character_id][event.axis] = max(0, min(100, current + event.delta))
        world = next_state.world.model_copy(update={"relationships": relationships})
        next_state = next_state.model_copy(update={"world": world})

    elif isinstance(event, GoalAdvanced):
        _require(event.goal_id in next_state.world.goals, "unknown goal")
        current = next_state.world.goals[event.goal_id]
        progress = max(0.0, min(1.0, current.progress + event.delta))
        status = event.status or current.status
        if progress >= 1:
            status = GoalStatus.COMPLETED
        evidence = current.evidence_event_ids
        if event.evidence_event_id:
            evidence = tuple(dict.fromkeys((*evidence, event.evidence_event_id)))
        goals = dict(next_state.world.goals)
        goals[event.goal_id] = current.model_copy(
            update={"progress": progress, "status": status, "evidence_event_ids": evidence}
        )
        world = next_state.world.model_copy(update={"goals": goals})
        next_state = next_state.model_copy(update={"world": world})

    elif isinstance(event, ThreadOpened):
        _require(event.thread.id not in next_state.threads, "thread already exists")
        threads = dict(next_state.threads)
        threads[event.thread.id] = event.thread
        next_state = next_state.model_copy(update={"threads": threads})

    elif isinstance(event, ThreadAdvanced):
        _require(event.thread_id in next_state.threads, "unknown thread")
        current = next_state.threads[event.thread_id]
        _require(
            current.status not in {ThreadStatus.RESOLVED, ThreadStatus.ABANDONED},
            "closed thread cannot advance",
        )
        threads = dict(next_state.threads)
        threads[event.thread_id] = current.model_copy(
            update={
                "status": ThreadStatus.ADVANCING,
                "urgency": event.urgency if event.urgency is not None else current.urgency,
                "last_advanced_event_id": envelope.event_id,
            }
        )
        next_state = next_state.model_copy(update={"threads": threads})

    elif isinstance(event, ThreadClosed):
        _require(event.thread_id in next_state.threads, "unknown thread")
        current = next_state.threads[event.thread_id]
        threads = dict(next_state.threads)
        threads[event.thread_id] = current.model_copy(update={"status": ThreadStatus(event.status)})
        next_state = next_state.model_copy(update={"threads": threads})

    elif isinstance(event, PhaseAdvanced):
        current_index = _PHASES.index(next_state.world.phase)
        target_index = _PHASES.index(event.phase)
        _require(target_index == current_index + 1, "phase must advance exactly one step")
        world = next_state.world.model_copy(update={"phase": event.phase})
        next_state = next_state.model_copy(update={"world": world})

    elif isinstance(event, EndingEntered):
        _require(next_state.ending is None, "ending already entered")
        _require(
            event.ending.entered_at_revision == envelope.sequence,
            "ending revision must match event sequence",
        )
        world = next_state.world.model_copy(update={"phase": StoryPhase.RESOLUTION})
        next_state = next_state.model_copy(
            update={"status": SessionStatus.RESOLVING, "world": world, "ending": event.ending}
        )

    elif isinstance(event, DecisionPresented):
        _require(next_state.pending_decision is None, "a decision is already pending")
        choice_ids = [item.id for item in event.choices]
        _require(len(choice_ids) == len(set(choice_ids)), "choice ids must be unique")
        _require(2 <= len(event.choices) <= 4, "decision requires 2-4 choices")
        scene_id = next_state.pending_scene.scene_id if next_state.pending_scene is not None else ""
        pending_decision = PendingDecisionReference(
            decision_id=event.decision_id,
            scene_id=scene_id,
            revision=envelope.sequence,
            choices=event.choices,
        )
        next_state = next_state.model_copy(
            update={"pending_scene": None, "pending_decision": pending_decision}
        )

    elif isinstance(event, EndingGenerated):
        _require(next_state.ending is None, "ending already entered")
        ending = EndingRuntime(
            ending_id=event.ending_id,
            entered_at_revision=envelope.sequence,
            title=event.title,
            blocks=event.blocks,
            tone=event.tone,
            terminal_state_summary=event.terminal_state_summary,
        )
        world = next_state.world.model_copy(update={"phase": StoryPhase.RESOLUTION})
        next_state = next_state.model_copy(
            update={"status": SessionStatus.RESOLVING, "world": world, "ending": ending}
        )

    elif isinstance(event, CompletionEvaluated):
        _require(next_state.ending is not None, "completion requires an ending")
        completion = CompletionState(
            cleared=event.cleared,
            assessments=event.assessments,
        )
        next_state = next_state.model_copy(update={"completion": completion})

    elif isinstance(event, SessionEnded):
        _require(next_state.ending is not None, "cannot end without ending state")
        _require(
            next_state.ending.ending_id == event.ending_id,
            "ending id does not match entered ending",
        )
        next_state = next_state.model_copy(
            update={
                "status": SessionStatus.ENDED,
                "pending_scene": None,
                "pending_decision": None,
            }
        )

    return next_state.model_copy(update={"revision": envelope.sequence})


def apply_events(state: SessionState, envelopes: Iterable[EventEnvelope]) -> SessionState:
    candidate = state
    for envelope in envelopes:
        candidate = apply_event(candidate, envelope)
    return candidate
