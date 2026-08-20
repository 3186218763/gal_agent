"""Pure proposal-to-event simulation on copied session state."""

from __future__ import annotations

from src.story.runtime.contracts import ActionResolution, SceneDraft, ScenePlan
from src.story.runtime.endings import next_phase
from src.story.runtime.segment_contracts import (
    EntityAttributeUpdate,
    MotifUpdate,
    PromiseMarkUpdate,
    PromiseSettleUpdate,
    SegmentDraft,
    SegmentPlan,
    ThreadOperation,
)
from src.story.script_pack.models import CompiledScriptPack, ScriptPackSourceV2
from src.story.state import (
    ActionResolved,
    ArcPressureAdvanced,
    BeatCompleted,
    CharacterLearnedFact,
    DecisionPresented,
    EndingGenerated,
    EntityAttributeSet,
    EventEnvelope,
    FactCommitted,
    FactEvidenced,
    FactRevealed,
    FactTruthStatus,
    FactVisibility,
    GoalAdvanced,
    MotifUsed,
    NarrativePromiseMarked,
    NarrativePromiseSettled,
    NarrativeThread,
    ObligationCreated,
    ObligationResolved,
    PhaseAdvanced,
    PlayerActionSelected,
    PresentedChoice,
    PromiseOpened,
    RelationshipChanged,
    RelationshipEventRecorded,
    SceneAcknowledged,
    SceneCommitted,
    SessionState,
    StanceChallenged,
    StanceExpressed,
    StateTransitionError,
    ThreadAdvanced,
    ThreadClosed,
    ThreadOpened,
    ThreadStatus,
    apply_events,
    derive_cost_incurred,
)
from src.story.state.events import StoryEvent

_KEY_LINE_MAX = 80
_KEY_LINES_PER_SCENE = 2

_DRAMATIC_ARC_ORDER = ("approach", "fracture", "accountability")


def scene_key_lines(blocks) -> tuple[str, ...]:
    """Deterministic archive lines: the scene's first dialogue and last block."""
    lines: list[str] = []
    for block in blocks:
        if block.kind == "dialogue" and block.text.strip():
            lines.append(block.text.strip()[:_KEY_LINE_MAX])
            break
    for block in reversed(blocks):
        text = block.text.strip()
        if text and (not lines or text[:_KEY_LINE_MAX] != lines[0]):
            lines.append(text[:_KEY_LINE_MAX])
            break
    return tuple(dict.fromkeys(lines))[:_KEY_LINES_PER_SCENE]


def scene_events(pack, state, plan, draft) -> tuple[StoryEvent, ...]:
    del pack  # pack reserved for future pack-aware scene conversion
    events: list[StoryEvent] = []
    phase = next_phase(state)
    if phase is not None:
        events.append(PhaseAdvanced(phase=phase))
    for fact in plan.fact_commits:
        evidence = (
            (plan.scene_id,) if fact.reason == "first_irreversible_evidence" or fact.reveal else ()
        )
        events.append(
            FactCommitted(fact_id=fact.fact_id, value=fact.value, evidence_event_ids=evidence)
        )
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
            stance_axis=item.stance_axis,
            stance_value=item.stance_value,
            accepted_risk=item.accepted_risk,
            potential_obligation_kind=item.potential_obligation_kind,
            conflict_axis_id=item.conflict_axis_id,
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
            summary=plan.summary,
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
        GoalAdvanced(goal_id=item.goal_id, delta=item.delta) for item in resolution.goal_deltas
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


def choice_selection_event(
    state: SessionState,
    choice: PresentedChoice,
    idempotency_key: str,
) -> PlayerActionSelected:
    if state.pending_decision is None:
        raise StateTransitionError("no decision is pending")
    return PlayerActionSelected(
        decision_id=state.pending_decision.decision_id,
        option_id=choice.id,
        action_id=choice.action_id,
        intent=choice.intent,
        target_character_id=choice.target_character_id,
        idempotency_key=idempotency_key,
        stance_axis=choice.stance_axis,
        stance_value=choice.stance_value,
        accepted_risk=choice.accepted_risk,
        # Compatibility alias: the accepted risk is the committed cost
        # category so derived costs match the committed Choice Meaning.
        accepted_cost_category=choice.accepted_risk,
        potential_obligation_kind=choice.potential_obligation_kind,
        conflict_axis_id=choice.conflict_axis_id,
    )


def simulate_consequence(
    pack: CompiledScriptPack,
    state: SessionState,
    resolution: ActionResolution,
) -> tuple[StoryEvent, ...]:
    """Build the Story Consequence batch for the pending choice.

    Emits the action resolution plus the semantic effects of the committed
    Choice Meaning: relationship changes paired with relationship events
    (visible in the following segment's first scene), stance establishment
    or reinforcement, obligation creation, and derived costs.  Internal
    event references (relationship event ids, scene anchors, cost effect
    ids) are deterministic placeholders that the authoritative command flow
    resolves against the actual committed envelope ids before commit.
    """
    pending = state.pending_consequence
    if pending is None:
        raise StateTransitionError("no consequence is pending")
    choice_id = pending.choice_event_id

    events: list[StoryEvent] = [
        ActionResolved(
            source_choice_event_id=choice_id,
            action_id=resolution.action_id,
            outcome=resolution.outcome,
        )
    ]

    # Relationship changes paired with relationship events; the pair makes
    # the change visible as dramatic development in the segment's first
    # scene and grounds relationship turning-point evidence.
    for index, item in enumerate(resolution.relationship_deltas):
        events.append(
            RelationshipChanged(
                character_id=item.character_id,
                axis=item.axis,
                delta=item.delta,
                source_choice_event_id=choice_id,
                relationship_event_id=f"rel:{choice_id}:{index}",
            )
        )
        events.append(
            RelationshipEventRecorded(
                character_id=item.character_id,
                tag=f"relationship_changed_{item.axis}",
                source_choice_event_id=choice_id,
                scene_event_id="",
            )
        )

    events.extend(
        GoalAdvanced(goal_id=item.goal_id, delta=item.delta) for item in resolution.goal_deltas
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

    # Choice Meaning semantic commitments — the player already chose these;
    # the planner cannot veto them (ADR 0003 / 0013).
    obligation_event: ObligationCreated | None = None
    if pending.stance_axis is not None and pending.stance_value is not None:
        key = f"{pending.stance_axis}:{pending.stance_value}"
        relation = "established" if key not in state.drama.stances else "reinforced"
        events.append(
            StanceExpressed(
                key=key,
                axis=pending.stance_axis,
                value=pending.stance_value,
                relation=relation,
                source_choice_event_id=choice_id,
            )
        )
    if pending.potential_obligation_kind is not None:
        obligation_event = ObligationCreated(
            obligation_id=f"obligation:{choice_id}",
            kind=pending.potential_obligation_kind,
            burden=_obligation_burden(pack, pending.potential_obligation_kind),
            source_choice_event_id=choice_id,
        )
        events.append(obligation_event)
        if pending.accepted_risk is not None:
            choice = _choice_event_from_pending(pending)
            cost = derive_cost_incurred(
                choice_id,
                choice,
                f"obligation:{choice_id}",
                obligation_event,
            )
            if cost is not None:
                events.append(cost)

    simulate_events(state, tuple(events))
    return tuple(events)


def _obligation_burden(pack: CompiledScriptPack, kind: str) -> int:
    definitions = getattr(pack.source, "obligation_kinds", ()) or ()
    definition = next((item for item in definitions if item.id == kind), None)
    return definition.burden if definition is not None else 1


def _choice_event_from_pending(pending) -> PlayerActionSelected:
    """Rebuild the committed Choice Meaning for deterministic derivation."""
    return PlayerActionSelected(
        decision_id=pending.decision_id,
        option_id=pending.option_id,
        action_id=pending.action_id,
        intent=pending.intent,
        target_character_id=pending.target_character_id,
        idempotency_key="",
        stance_axis=pending.stance_axis,
        stance_value=pending.stance_value,
        accepted_risk=pending.accepted_risk,
        accepted_cost_category=pending.accepted_risk,
        potential_obligation_kind=pending.potential_obligation_kind,
        conflict_axis_id=pending.conflict_axis_id,
    )


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
            events.append(ThreadAdvanced(thread_id=op.thread_id, urgency=op.urgency))
        elif op.kind == "close":
            status = (
                ThreadStatus.RESOLVED if op.close_status == "resolved" else ThreadStatus.ABANDONED
            )
            events.append(ThreadClosed(thread_id=op.thread_id, status=status))
    return events


def next_phase_for_count(
    state: SessionState,
    projected_scene_count: int,
):
    """Check if phase should advance based on projected scene count."""
    return next_phase(state, projected_count=projected_scene_count)


def _beat_events(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
) -> list[StoryEvent]:
    """Translate Beat Map effects into deterministic events.

    Effects fire only when their state preconditions hold: a staged fact
    must be committed but not yet revealed, a revealed fact must have
    climbed the evidence ladder (or the beat is an ending, carrying the
    finale exemption), a stance challenge needs an expressed stance, and
    arc pressure advances exactly one step.  Silent skips keep a beat
    performable when earlier prose already covered its effect organically.
    Facts committed by this segment's own scenes count as covered.
    """
    source = pack.source
    if not isinstance(source, ScriptPackSourceV2) or source.structure is None:
        return []
    beats = {beat.id: beat for act in source.structure.acts for beat in act.beats}
    if not plan.beat_ids:
        return []

    committed_here = {
        fact.fact_id
        for scene in plan.scenes
        for fact in scene.fact_commits
    }
    revealed_here = {
        fact.fact_id
        for scene in plan.scenes
        for fact in scene.fact_commits
        if fact.reveal
    }

    events: list[StoryEvent] = []
    for beat_id in plan.beat_ids:
        beat = beats.get(beat_id)
        if beat is None:
            continue
        anchor = f"beat:{beat_id}"
        effects = beat.effects
        if effects is not None:
            # The author's deterministic answers first: a latent commit with
            # its evidence makes the same beat's reveal pass the ladder.
            for commit in effects.commit_latent:
                record = state.facts.get(commit.fact_id)
                if (
                    record is None
                    or record.truth_status != FactTruthStatus.POSSIBLE
                    or commit.fact_id in committed_here
                ):
                    continue
                events.append(
                    FactCommitted(
                        fact_id=commit.fact_id,
                        value=commit.value,
                        evidence_event_ids=(anchor,),
                    )
                )
                committed_here.add(commit.fact_id)
            for fact_id in effects.stage_fact_ids:
                record = state.facts.get(fact_id)
                if (
                    record is None
                    or record.visibility == FactVisibility.REVEALED
                    or fact_id in committed_here
                ):
                    continue
                events.append(
                    FactEvidenced(fact_id=fact_id, evidence_event_id=anchor)
                )
            for fact_id in effects.reveal_fact_ids:
                record = state.facts.get(fact_id)
                if (
                    record is None
                    or fact_id in revealed_here
                    or record.visibility == FactVisibility.REVEALED
                ):
                    continue
                committed = (
                    record.truth_status == FactTruthStatus.COMMITTED
                    or fact_id in committed_here
                )
                if beat.kind == "ending":
                    ladder_ok = True
                elif fact_id in committed_here:
                    # This segment's commit carries exactly one evidence
                    # anchor (the beat itself).
                    ladder_ok = record.evidence_required <= 1
                else:
                    ladder_ok = (
                        record.truth_status == FactTruthStatus.COMMITTED
                        and len(record.evidence_event_ids) >= record.evidence_required
                    )
                if committed and ladder_ok:
                    events.append(
                        FactRevealed(fact_id=fact_id, finale=beat.kind == "ending")
                    )
            for index, expectation in enumerate(effects.promise_expectations):
                events.append(
                    PromiseOpened(
                        promise_id=f"{anchor}:{index}",
                        expectation=expectation,
                        source_event_id=anchor,
                        soft_deadline_decision=(
                            state.drama.decision_count
                            + effects.promise_soft_deadline_decisions
                        ),
                        hard_deadline_decision=(
                            state.drama.decision_count
                            + effects.promise_hard_deadline_decisions
                        ),
                    )
                )
            for challenge in effects.stance_challenges:
                if challenge.stance_axis not in state.drama.stances:
                    continue
                events.append(
                    StanceChallenged(
                        stance_key=challenge.stance_axis,
                        scene_event_id=anchor,
                        challenging_character_id=challenge.challenging_character_id,
                    )
                )
            if effects.advance_arc_phase:
                current_index = _DRAMATIC_ARC_ORDER.index(state.drama.arc_phase)
                if current_index + 1 < len(_DRAMATIC_ARC_ORDER):
                    events.append(
                        ArcPressureAdvanced(
                            phase=_DRAMATIC_ARC_ORDER[current_index + 1]
                        )
                    )
        events.append(BeatCompleted(beat_id=beat_id, source_event_id=anchor))
    return events


def segment_events(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    draft: SegmentDraft,
) -> tuple[StoryEvent, ...]:
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
    for i, (scene_plan, scene_draft) in enumerate(zip(plan.scenes, draft.scene_drafts)):
        if i > 0 and plan.terminal != "ending":
            events.append(SceneAcknowledged(scene_id=plan.scenes[i - 1].scene_id))

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
                # Ending segments carry the finale exemption flag so the
                # reducer's evidence gate lets the payoff land in one scene.
                events.append(
                    FactRevealed(fact_id=fact.fact_id, finale=plan.terminal == "ending")
                )
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
                summary=scene_plan.summary,
                key_lines=scene_key_lines(scene_draft.blocks),
            )
        )

    # Obligation settlement: the writer marks which outstanding obligations
    # this segment visibly delivers.  The settling scene is cited through a
    # placeholder the authoritative flow rewrites to the committed event id.
    for obligation_id in plan.resolved_obligation_ids:
        events.append(
            ObligationResolved(
                obligation_id=obligation_id,
                outcome="fulfilled",
                resolution_scene_event_id=f"scene_ref:{plan.scenes[-1].scene_id}",
            )
        )

    # Canon Ledger registration: narrative details this prose establishes,
    # anchored to the segment's final scene.
    anchor_scene_id = plan.scenes[-1].scene_id if plan.scenes else ""
    for update in draft.ledger_updates:
        if isinstance(update, EntityAttributeUpdate):
            events.append(
                EntityAttributeSet(
                    entity_id=update.entity_id,
                    entity_name=update.entity_name,
                    attribute=update.attribute,
                    value=update.value,
                    scene_id=anchor_scene_id,
                )
            )
        elif isinstance(update, PromiseMarkUpdate):
            events.append(
                NarrativePromiseMarked(
                    promise_id=update.promise_id,
                    statement=update.statement,
                    scene_id=anchor_scene_id,
                )
            )
        elif isinstance(update, PromiseSettleUpdate):
            events.append(
                NarrativePromiseSettled(
                    promise_id=update.promise_id,
                    outcome=update.outcome,
                    scene_id=anchor_scene_id,
                )
            )
        elif isinstance(update, MotifUpdate):
            events.append(
                MotifUsed(
                    motif_id=update.motif_id,
                    label=update.label,
                    scene_id=anchor_scene_id,
                )
            )

    # Beat Map effects: deterministic dead-event wiring.  Each performed
    # beat marks itself BeatCompleted and fires its declared effects, subject
    # to state preconditions (facts committed by this segment's scenes count
    # as staged/revealed already — the beat never double-fires).
    events.extend(_beat_events(pack, state, plan))

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
                    stance_axis=choice_plan.stance_axis,
                    stance_value=choice_plan.stance_value,
                    accepted_risk=choice_plan.accepted_risk,
                    potential_obligation_kind=choice_plan.potential_obligation_kind,
                    conflict_axis_id=choice_plan.conflict_axis_id,
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
        decision_id = last_scene.decision_id if last_scene.decision_id else f"dec_{plan.segment_id}"
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
                    tone=(ending_draft.tone or (proposal.tone if proposal else "") or "neutral"),
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
