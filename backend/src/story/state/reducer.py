from __future__ import annotations

from collections.abc import Iterable

from src.story.state.events import (
    ActionResolved,
    ArcPressureAdvanced,
    BeliefChanged,
    CharacterDramaticStateChanged,
    CharacterLearnedFact,
    CompletionEvaluated,
    ConsequenceBroken,
    ConsequenceRealized,
    ConsequenceScheduled,
    CostIncurred,
    DecisionPresented,
    DramaticQuestionSet,
    EndingEntered,
    EndingGenerated,
    EventEnvelope,
    FactCommitted,
    FactEvidenced,
    FactRevealed,
    GoalAdvanced,
    ObligationCreated,
    ObligationResolved,
    PhaseAdvanced,
    PlayerActionSelected,
    PromiseChanged,
    PromiseOpened,
    RelationshipChanged,
    RelationshipEventRecorded,
    RelationshipTurningPointReached,
    SceneAcknowledged,
    SceneCommitted,
    SessionEnded,
    StanceChallenged,
    StanceExpressed,
    ThreadAdvanced,
    ThreadClosed,
    ThreadOpened,
)
from src.story.state.models import (
    RECENT_PROSE_BLOCK_CAP,
    CompletionState,
    DramaticArcPhase,
    DramaticQuestionRuntime,
    EndingRuntime,
    FactTruthStatus,
    FactVisibility,
    GoalStatus,
    ObligationRuntime,
    PendingConsequenceReference,
    PendingDecisionReference,
    PendingSceneReference,
    PromiseRuntime,
    PromiseStatus,
    ProseBlockRecord,
    SceneSummaryRecord,
    ScheduledConsequenceRuntime,
    SessionState,
    SessionStatus,
    StanceRuntime,
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

_DRAMATIC_ARC_PHASES = (
    DramaticArcPhase.APPROACH,
    DramaticArcPhase.FRACTURE,
    DramaticArcPhase.ACCOUNTABILITY,
)

_PROMISE_TRANSITIONS = {
    PromiseStatus.OPEN: frozenset(
        {
            PromiseStatus.ESCALATED,
            PromiseStatus.TRANSFORMED,
            PromiseStatus.FULFILLED,
            PromiseStatus.BROKEN,
        }
    ),
    PromiseStatus.ESCALATED: frozenset(
        {PromiseStatus.TRANSFORMED, PromiseStatus.FULFILLED, PromiseStatus.BROKEN}
    ),
    PromiseStatus.TRANSFORMED: frozenset(
        {PromiseStatus.ESCALATED, PromiseStatus.FULFILLED, PromiseStatus.BROKEN}
    ),
    PromiseStatus.FULFILLED: frozenset(),
    PromiseStatus.BROKEN: frozenset(),
}


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
        scene_summaries = next_state.scene_summaries
        if event.summary:
            scene_summaries = (
                *scene_summaries,
                SceneSummaryRecord(scene_id=event.scene_id, summary=event.summary.strip()),
            )
        # Bounded verbatim ring: the newest blocks survive, the oldest fall off.
        recent_prose = (
            *next_state.recent_prose_blocks,
            *(
                ProseBlockRecord(
                    scene_id=event.scene_id,
                    kind=block.kind,
                    character_id=block.character_id,
                    text=block.text,
                )
                for block in event.blocks
            ),
        )
        if len(recent_prose) > RECENT_PROSE_BLOCK_CAP:
            recent_prose = recent_prose[-RECENT_PROSE_BLOCK_CAP:]
        next_state = next_state.model_copy(
            update={
                "world": world,
                "pending_scene": pending_scene,
                "pending_decision": pending_decision,
                "scene_summaries": scene_summaries,
                "recent_prose_blocks": recent_prose,
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
        _require(next_state.pending_consequence is None, "a consequence is already pending")
        _require(
            next_state.pending_decision.decision_id == event.decision_id,
            "player action does not match pending decision",
        )
        offered = next(
            (item for item in next_state.pending_decision.choices if item.id == event.option_id),
            None,
        )
        _require(offered is not None, "player choice was not offered")
        assert offered is not None
        _require(
            event.action_id == offered.action_id
            and event.intent == offered.intent
            and event.target_character_id == offered.target_character_id
            and event.stance_axis == offered.stance_axis
            and event.stance_value == offered.stance_value
            and event.accepted_risk == offered.accepted_risk
            and event.potential_obligation_kind == offered.potential_obligation_kind
            and event.conflict_axis_id == offered.conflict_axis_id,
            "selected choice meaning does not match the offered choice meaning",
        )
        pending_consequence = PendingConsequenceReference(
            choice_event_id=envelope.event_id,
            decision_id=event.decision_id,
            option_id=event.option_id,
            action_id=event.action_id,
            intent=event.intent,
            target_character_id=event.target_character_id,
            stance_axis=event.stance_axis,
            stance_value=event.stance_value,
            accepted_risk=event.accepted_risk,
            potential_obligation_kind=event.potential_obligation_kind,
            conflict_axis_id=event.conflict_axis_id,
        )
        drama = next_state.drama.model_copy(
            update={"decision_count": next_state.drama.decision_count + 1}
        )
        next_state = next_state.model_copy(
            update={
                "pending_scene": None,
                "pending_decision": None,
                "pending_consequence": pending_consequence,
                "drama": drama,
            }
        )

    elif isinstance(event, ActionResolved):
        if next_state.pending_consequence is not None:
            pending = next_state.pending_consequence
            _require(
                event.source_choice_event_id == pending.choice_event_id,
                "action resolution does not match the pending choice",
            )
            _require(event.action_id == pending.action_id, "resolved action does not match choice")
            _require(pending.outcome is None, "pending choice is already resolved")
            next_state = next_state.model_copy(
                update={
                    "pending_consequence": pending.model_copy(
                        update={
                            "outcome": event.outcome,
                            "resolution_event_id": envelope.event_id,
                        }
                    )
                }
            )

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

    elif isinstance(event, CharacterDramaticStateChanged):
        _require(event.character_id in next_state.characters, "unknown character")
        characters = dict(next_state.characters)
        character = characters[event.character_id]
        characters[event.character_id] = character.model_copy(
            update={
                "current_desire": event.current_desire,
                "current_fear": event.current_fear,
                "emotional_condition": event.emotional_condition,
                "judgment_of_protagonist": event.judgment_of_protagonist,
                "boundary_being_tested": event.boundary_being_tested,
            }
        )
        next_state = next_state.model_copy(update={"characters": characters})

    elif isinstance(event, RelationshipChanged):
        _require(
            (event.source_choice_event_id is None) == (event.relationship_event_id is None),
            "relationship semantic links must be provided together",
        )
        _require(
            event.character_id in next_state.world.relationships,
            "unknown relationship character",
        )
        relationships = {key: dict(value) for key, value in next_state.world.relationships.items()}
        current = relationships[event.character_id].get(event.axis, 0)
        relationships[event.character_id][event.axis] = max(0, min(100, current + event.delta))
        world = next_state.world.model_copy(update={"relationships": relationships})
        next_state = next_state.model_copy(update={"world": world})

    elif isinstance(event, DramaticQuestionSet):
        question = DramaticQuestionRuntime(
            key=event.key,
            text=event.text,
            source_event_id=event.source_event_id,
        )
        drama = next_state.drama.model_copy(update={"primary_question": question})
        next_state = next_state.model_copy(update={"drama": drama})

    elif isinstance(event, StanceExpressed):
        _require(
            event.key == f"{event.axis}:{event.value}",
            "stance key must be the canonical axis:value key",
        )
        stances = dict(next_state.drama.stances)
        current = stances.get(event.key)
        if current is None:
            _require(
                event.relation == "established",
                "new stance must be established before it can change",
            )
            stances[event.key] = StanceRuntime(
                key=event.key,
                axis=event.axis,
                value=event.value,
                relation=event.relation,
                expression_event_ids=(envelope.event_id,),
                source_choice_event_ids=(event.source_choice_event_id,),
            )
        else:
            _require(event.relation != "established", "stance is already established")
            _require(
                current.axis == event.axis and current.value == event.value,
                "stance update must match its established axis and value",
            )
            stances[event.key] = current.model_copy(
                update={
                    "relation": event.relation,
                    "expression_event_ids": (
                        *current.expression_event_ids,
                        envelope.event_id,
                    ),
                    "source_choice_event_ids": (
                        *current.source_choice_event_ids,
                        event.source_choice_event_id,
                    ),
                }
            )
        drama = next_state.drama.model_copy(update={"stances": stances})
        next_state = next_state.model_copy(update={"drama": drama})

    elif isinstance(event, StanceChallenged):
        _require(event.stance_key in next_state.drama.stances, "unknown stance")
        if event.challenging_character_id is not None:
            _require(
                event.challenging_character_id in next_state.characters,
                "unknown challenging character",
            )
        stances = dict(next_state.drama.stances)
        current = stances[event.stance_key]
        stances[event.stance_key] = current.model_copy(
            update={"challenge_event_ids": (*current.challenge_event_ids, envelope.event_id)}
        )
        drama = next_state.drama.model_copy(update={"stances": stances})
        next_state = next_state.model_copy(update={"drama": drama})

    elif isinstance(event, RelationshipEventRecorded):
        _require(event.character_id in next_state.characters, "unknown character")
        characters = dict(next_state.characters)
        character = characters[event.character_id]
        _require(
            envelope.event_id not in character.relationship_event_ids,
            "relationship event already recorded",
        )
        characters[event.character_id] = character.model_copy(
            update={
                "relationship_event_ids": (
                    *character.relationship_event_ids,
                    envelope.event_id,
                )
            }
        )
        next_state = next_state.model_copy(update={"characters": characters})

    elif isinstance(event, RelationshipTurningPointReached):
        _require(event.character_id in next_state.characters, "unknown character")
        character = next_state.characters[event.character_id]
        _require(
            len(event.relationship_event_ids) == len(set(event.relationship_event_ids))
            and set(event.relationship_event_ids).issubset(character.relationship_event_ids),
            "turning point relationship event evidence must be unique and belong to the character",
        )
        _require(
            event.turning_point_id not in next_state.drama.reached_turning_point_ids,
            "relationship turning point already reached",
        )
        reached = frozenset((*next_state.drama.reached_turning_point_ids, event.turning_point_id))
        drama = next_state.drama.model_copy(update={"reached_turning_point_ids": reached})
        characters = dict(next_state.characters)
        characters[event.character_id] = character.model_copy(
            update={
                "turning_point_ids": frozenset(
                    (*character.turning_point_ids, event.turning_point_id)
                )
            }
        )
        next_state = next_state.model_copy(update={"drama": drama, "characters": characters})

    elif isinstance(event, PromiseOpened):
        _require(event.promise_id not in next_state.drama.promises, "promise already exists")
        _require(
            event.soft_deadline_decision <= event.hard_deadline_decision,
            "promise soft deadline cannot exceed hard deadline",
        )
        _require(
            event.soft_deadline_decision > next_state.drama.decision_count,
            "promise soft deadline must be in the future",
        )
        for character_id in event.involved_character_ids:
            _require(character_id in next_state.characters, "unknown promise character")
        for fact_id in event.related_fact_ids:
            _require(fact_id in next_state.facts, "unknown promise fact")
        promises = dict(next_state.drama.promises)
        promises[event.promise_id] = PromiseRuntime(
            promise_id=event.promise_id,
            expectation=event.expectation,
            source_event_id=event.source_event_id,
            involved_character_ids=event.involved_character_ids,
            related_fact_ids=event.related_fact_ids,
            opened_at_decision=next_state.drama.decision_count,
            soft_deadline_decision=event.soft_deadline_decision,
            hard_deadline_decision=event.hard_deadline_decision,
        )
        drama = next_state.drama.model_copy(update={"promises": promises})
        next_state = next_state.model_copy(update={"drama": drama})

    elif isinstance(event, PromiseChanged):
        _require(event.promise_id in next_state.drama.promises, "unknown promise")
        promises = dict(next_state.drama.promises)
        current = promises[event.promise_id]
        _require(
            bool(_PROMISE_TRANSITIONS[current.status]),
            "terminal promise cannot change",
        )
        _require(
            event.status in _PROMISE_TRANSITIONS[current.status],
            "invalid promise lifecycle transition",
        )
        promises[event.promise_id] = current.model_copy(
            update={
                "status": event.status,
                "payoff_event_ids": tuple(
                    dict.fromkeys((*current.payoff_event_ids, *event.payoff_event_ids))
                ),
            }
        )
        drama = next_state.drama.model_copy(update={"promises": promises})
        next_state = next_state.model_copy(update={"drama": drama})

    elif isinstance(event, ObligationCreated):
        _require(
            event.obligation_id not in next_state.drama.obligations,
            "obligation already exists",
        )
        if event.character_id is not None:
            _require(event.character_id in next_state.characters, "unknown obligation character")
        obligations = dict(next_state.drama.obligations)
        obligations[event.obligation_id] = ObligationRuntime(
            obligation_id=event.obligation_id,
            kind=event.kind,
            burden=event.burden,
            source_choice_event_id=event.source_choice_event_id,
            character_id=event.character_id,
        )
        characters = next_state.characters
        if event.character_id is not None:
            characters = dict(characters)
            character = characters[event.character_id]
            characters[event.character_id] = character.model_copy(
                update={
                    "unresolved_obligation_ids": frozenset(
                        (*character.unresolved_obligation_ids, event.obligation_id)
                    )
                }
            )
        drama = next_state.drama.model_copy(update={"obligations": obligations})
        next_state = next_state.model_copy(update={"drama": drama, "characters": characters})

    elif isinstance(event, ObligationResolved):
        _require(event.obligation_id in next_state.drama.obligations, "unknown obligation")
        obligations = dict(next_state.drama.obligations)
        current = obligations[event.obligation_id]
        _require(current.status == "open", "obligation is already resolved")
        obligations[event.obligation_id] = current.model_copy(
            update={
                "status": event.outcome,
                "resolution_scene_event_id": event.resolution_scene_event_id,
                "resolution_event_id": envelope.event_id,
            }
        )
        characters = next_state.characters
        if current.character_id is not None:
            _require(current.character_id in characters, "unknown obligation character")
            characters = dict(characters)
            character = characters[current.character_id]
            _require(
                event.obligation_id in character.unresolved_obligation_ids,
                "obligation is missing from character authority",
            )
            unresolved = set(character.unresolved_obligation_ids)
            unresolved.remove(event.obligation_id)
            characters[current.character_id] = character.model_copy(
                update={"unresolved_obligation_ids": frozenset(unresolved)}
            )
        drama = next_state.drama.model_copy(update={"obligations": obligations})
        next_state = next_state.model_copy(update={"drama": drama, "characters": characters})

    elif isinstance(event, ConsequenceScheduled):
        _require(
            event.consequence_id not in next_state.drama.scheduled_consequences,
            "consequence already exists",
        )
        _require(
            event.due_after_decision <= event.hard_deadline_decision,
            "consequence due decision cannot exceed hard deadline",
        )
        _require(
            next_state.drama.decision_count < event.due_after_decision,
            "consequence due decision must be in the future",
        )
        consequences = dict(next_state.drama.scheduled_consequences)
        consequences[event.consequence_id] = ScheduledConsequenceRuntime(
            consequence_id=event.consequence_id,
            cause_event_id=event.cause_event_id,
            required_effect=event.required_effect,
            due_after_decision=event.due_after_decision,
            hard_deadline_decision=event.hard_deadline_decision,
        )
        drama = next_state.drama.model_copy(update={"scheduled_consequences": consequences})
        next_state = next_state.model_copy(update={"drama": drama})

    elif isinstance(event, ConsequenceRealized):
        _require(
            event.consequence_id in next_state.drama.scheduled_consequences,
            "unknown consequence",
        )
        consequences = dict(next_state.drama.scheduled_consequences)
        current = consequences[event.consequence_id]
        _require(current.status == "scheduled", "consequence is already resolved")
        consequences[event.consequence_id] = current.model_copy(
            update={"status": "realized", "realization_event_id": envelope.event_id}
        )
        drama = next_state.drama.model_copy(update={"scheduled_consequences": consequences})
        next_state = next_state.model_copy(update={"drama": drama})

    elif isinstance(event, ConsequenceBroken):
        _require(
            event.consequence_id in next_state.drama.scheduled_consequences,
            "unknown consequence",
        )
        consequences = dict(next_state.drama.scheduled_consequences)
        current = consequences[event.consequence_id]
        _require(current.status == "scheduled", "consequence is already resolved")
        consequences[event.consequence_id] = current.model_copy(
            update={"status": "broken", "broken_event_id": envelope.event_id}
        )
        drama = next_state.drama.model_copy(update={"scheduled_consequences": consequences})
        next_state = next_state.model_copy(update={"drama": drama})

    elif isinstance(event, CostIncurred):
        _require(
            envelope.event_id not in next_state.drama.cost_event_ids,
            "cost event already recorded",
        )
        drama = next_state.drama.model_copy(
            update={"cost_event_ids": (*next_state.drama.cost_event_ids, envelope.event_id)}
        )
        next_state = next_state.model_copy(update={"drama": drama})

    elif isinstance(event, ArcPressureAdvanced):
        current_index = _DRAMATIC_ARC_PHASES.index(next_state.drama.arc_phase)
        target_index = _DRAMATIC_ARC_PHASES.index(event.phase)
        _require(
            target_index == current_index + 1,
            "dramatic arc must advance exactly one step",
        )
        drama = next_state.drama.model_copy(update={"arc_phase": event.phase})
        next_state = next_state.model_copy(update={"drama": drama})

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
        if next_state.pending_consequence is not None:
            _require(
                next_state.pending_consequence.outcome is not None,
                "cannot present a decision before resolving the pending consequence",
            )
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
            update={
                "pending_scene": None,
                "pending_decision": pending_decision,
                "pending_consequence": None,
            }
        )

    elif isinstance(event, EndingGenerated):
        _require(next_state.ending is None, "ending already entered")
        if next_state.pending_consequence is not None:
            _require(
                next_state.pending_consequence.outcome is not None,
                "cannot generate an ending before resolving the pending consequence",
            )
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
            update={
                "status": SessionStatus.RESOLVING,
                "world": world,
                "ending": ending,
                "pending_consequence": None,
            }
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
                "pending_consequence": None,
            }
        )

    return next_state.model_copy(update={"revision": envelope.sequence})


def apply_events(state: SessionState, envelopes: Iterable[EventEnvelope]) -> SessionState:
    candidate = state
    for envelope in envelopes:
        candidate = apply_event(candidate, envelope)
    return candidate
