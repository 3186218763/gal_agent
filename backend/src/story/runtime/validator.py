"""Deterministic proposal and draft validation for Planner/Writer outputs."""

from __future__ import annotations

from collections.abc import Iterable

from src.story.runtime.context import build_condition_context
from src.story.runtime.contracts import ActionResolution, SceneDraft, ScenePlan
from src.story.runtime.segment_contracts import (
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
)
from src.story.script_pack.models import CompiledScriptPack, ScriptPackSourceV2
from src.story.state import FactTruthStatus, FactVisibility, SessionState


class ProposalRejected(ValueError):
    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


def _validate_fact_commits(pack, state, commits) -> list[str]:
    errors: list[str] = []
    context = build_condition_context(state)
    seen: set[str] = set()
    questions = {item.id: item for item in pack.source.facts.latent_questions}
    for commit in commits:
        if commit.fact_id in seen:
            errors.append(f"duplicate fact commit: {commit.fact_id}")
            continue
        seen.add(commit.fact_id)
        current = state.facts.get(commit.fact_id)
        question = questions.get(commit.fact_id)
        if current is None or question is None or current.truth_status != FactTruthStatus.POSSIBLE:
            errors.append(f"fact is not an open latent question: {commit.fact_id}")
            continue
        candidate = next((item for item in question.candidates if item.value == commit.value), None)
        if candidate is None:
            errors.append(f"unknown candidate value: {commit.fact_id}.{commit.value}")
            continue
        if commit.reason not in question.commit_when:
            errors.append(f"commit reason is not allowed: {commit.fact_id}.{commit.reason}")
        if commit.reason == "explicit_revelation" and not commit.reveal:
            errors.append(f"explicit revelation must reveal the fact: {commit.fact_id}")
        for index in range(len(candidate.requirements)):
            key = f"fact.{commit.fact_id}.candidate.{commit.value}.requirement.{index}"
            if not pack.conditions[key].evaluate(context):
                errors.append(f"candidate requirement is false: {commit.fact_id}.{commit.value}.{index}")
        unknown_learners = set(commit.learned_by) - pack.character_ids
        errors.extend(f"unknown character: {item}" for item in sorted(unknown_learners))
        if commit.reveal and question.evidence_required > 1:
            errors.append(f"fact cannot be revealed by one scene: {commit.fact_id}")
    return errors


def validate_scene_plan(pack: CompiledScriptPack, state: SessionState, plan: ScenePlan) -> ScenePlan:
    errors: list[str] = []
    location_ids = {
        item.id
        for item in (
            pack.source.world_setting.locations
            if isinstance(pack.source, ScriptPackSourceV2)
            else pack.source.world.locations
        )
    }
    if plan.location_id not in location_ids:
        errors.append(f"unknown location: {plan.location_id}")
    errors.extend(
        f"unknown character: {item}"
        for item in plan.present_character_ids
        if item not in pack.character_ids
    )
    errors.extend(f"unknown goal: {item}" for item in plan.focus_goal_ids if item not in pack.goal_ids)
    errors.extend(f"unknown fact: {item}" for item in plan.related_fact_ids if item not in pack.fact_ids)
    allowed_actions = pack.action_ids & set(pack.source.protagonist.capabilities)
    errors.extend(
        f"unavailable action: {choice.action_id}"
        for choice in plan.choices
        if choice.action_id not in allowed_actions
    )
    option_ids = [item.option_id for item in plan.choices]
    if len(option_ids) != len(set(option_ids)):
        errors.append("choice option ids must be unique")
    errors.extend(_validate_fact_commits(pack, state, plan.fact_commits))
    if errors:
        raise ProposalRejected(errors)
    return plan


def validate_action_resolution(
    pack: CompiledScriptPack,
    state: SessionState,
    resolution: ActionResolution,
    expected_action_id: str | None = None,
) -> ActionResolution:
    if resolution.action_id not in pack.action_ids:
        raise ProposalRejected([f"unavailable action: {resolution.action_id}"])
    errors: list[str] = []
    if expected_action_id is not None and resolution.action_id != expected_action_id:
        errors.append(
            f"resolution action mismatch: expected {expected_action_id}, got {resolution.action_id}"
        )
    extension = next(
        (item for item in pack.source.interaction_rules.extensions if item.id == resolution.action_id),
        None,
    )
    if extension is not None:
        context = build_condition_context(state)
        for index in range(len(extension.preconditions)):
            key = f"action.{extension.id}.precondition.{index}"
            if not pack.conditions[key].evaluate(context):
                errors.append(f"action precondition is false: {extension.id}.{index}")
    for item in resolution.relationship_deltas:
        axes = state.world.relationships.get(item.character_id)
        if axes is None:
            errors.append(f"unknown relationship character: {item.character_id}")
            continue
        if item.axis not in axes:
            errors.append(f"unknown relationship axis: {item.character_id}.{item.axis}")
            continue
        bounds = (-10, 10) if extension is None else extension.effects.relationship_axes.get(item.axis)
        if bounds is None or not bounds[0] <= item.delta <= bounds[1]:
            errors.append(f"relationship delta out of bounds: {item.character_id}.{item.axis}")
    relationship_keys = [(item.character_id, item.axis) for item in resolution.relationship_deltas]
    if len(relationship_keys) != len(set(relationship_keys)):
        errors.append("relationship deltas must target unique character axes")
    goal_bounds = (-0.15, 0.25) if extension is None else extension.effects.goal_progress
    for item in resolution.goal_deltas:
        if item.goal_id not in state.world.goals:
            errors.append(f"unknown goal: {item.goal_id}")
        elif not goal_bounds[0] <= item.delta <= goal_bounds[1]:
            errors.append(f"goal delta out of bounds: {item.goal_id}")
    goal_ids = [item.goal_id for item in resolution.goal_deltas]
    if len(goal_ids) != len(set(goal_ids)):
        errors.append("goal deltas must target unique goals")
    if len(resolution.evidence_fact_ids) != len(set(resolution.evidence_fact_ids)):
        errors.append("evidenced fact ids must be unique")
    if len(resolution.evidence_fact_ids) > 1:
        errors.append("one action can add evidence to at most one fact")
    for fact_id in resolution.evidence_fact_ids:
        fact = state.facts.get(fact_id)
        if fact is None:
            errors.append(f"unknown fact: {fact_id}")
        elif fact.truth_status != FactTruthStatus.COMMITTED:
            errors.append(f"cannot evidence uncommitted fact: {fact_id}")
        elif fact.visibility == FactVisibility.REVEALED:
            errors.append(f"cannot add evidence to revealed fact: {fact_id}")
    for fact_id in resolution.reveal_fact_ids:
        fact = state.facts.get(fact_id)
        if fact is None:
            errors.append(f"unknown fact: {fact_id}")
        elif fact.truth_status != FactTruthStatus.COMMITTED:
            errors.append(f"cannot reveal uncommitted fact: {fact_id}")
        elif (
            len(fact.evidence_event_ids)
            + (1 if fact_id in resolution.evidence_fact_ids else 0)
            < fact.evidence_required
        ):
            errors.append(f"fact lacks evidence: {fact_id}")
    if len(resolution.reveal_fact_ids) != len(set(resolution.reveal_fact_ids)):
        errors.append("revealed fact ids must be unique")
    character_entries: list[str] = []
    for entry in resolution.learned_facts:
        if entry.character_id in character_entries:
            errors.append(f"learned fact character must be unique: {entry.character_id}")
            continue
        character_entries.append(entry.character_id)
        if entry.character_id not in state.characters:
            errors.append(f"unknown character: {entry.character_id}")
        for fact_id in entry.fact_ids:
            fact = state.facts.get(fact_id)
            if fact is None or fact.truth_status != FactTruthStatus.COMMITTED:
                errors.append(
                    f"character cannot learn unavailable fact: {entry.character_id}.{fact_id}"
                )
        if len(entry.fact_ids) != len(set(entry.fact_ids)):
            errors.append(f"learned fact ids must be unique: {entry.character_id}")
    if errors:
        raise ProposalRejected(errors)
    return resolution


def validate_scene_draft(plan: ScenePlan, draft: SceneDraft) -> SceneDraft:
    errors: list[str] = []
    if draft.scene_id != plan.scene_id:
        errors.append(f"scene id mismatch: expected {plan.scene_id}, got {draft.scene_id}")
    if not draft.blocks or any(not block.text.strip() for block in draft.blocks):
        errors.append("scene draft requires non-empty blocks")
    for block in draft.blocks:
        if block.kind == "dialogue" and block.character_id not in plan.present_character_ids:
            errors.append(f"dialogue speaker is not present: {block.character_id}")
    planned_ids = [item.option_id for item in plan.choices]
    written_ids = [item.option_id for item in draft.choices]
    if set(written_ids) != set(planned_ids) or len(written_ids) != len(planned_ids):
        errors.append("written choice ids must exactly match planned choice ids")
    normalized_labels = [item.label.strip().casefold() for item in draft.choices]
    if any(not label for label in normalized_labels):
        errors.append("written choice labels cannot be empty")
    if len(normalized_labels) != len(set(normalized_labels)):
        errors.append("written choice labels must be unique")
    if plan.terminal == "decision" and not 2 <= len(draft.choices) <= 4:
        errors.append("decision draft requires 2-4 choices")
    if plan.terminal == "continue" and draft.choices:
        errors.append("continue draft cannot contain choices")
    if errors:
        raise ProposalRejected(errors)
    return draft


def validate_segment_plan(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    pacing: PacingEnvelope,
) -> SegmentPlan:
    errors: list[str] = []

    # Terminal-ending requires an ending proposal.
    if plan.terminal == "ending" and plan.ending_proposal is None:
        errors.append("ending terminal requires ending_proposal")

    # Ending before min_scenes is rejected (unless must_end).
    if plan.terminal == "ending" and not pacing.can_end and not pacing.must_end:
        errors.append(
            f"ending proposed before min_scenes ({pacing.min_scenes}); "
            f"current scene_count is {pacing.scene_count}"
        )

    # must_end forces an ending terminal.
    if pacing.must_end and plan.terminal != "ending":
        errors.append("must_end is True; segment terminal must be 'ending'")

    # All but last scene must have terminal="continue".
    for i, scene in enumerate(plan.scenes[:-1]):
        if scene.terminal != "continue":
            errors.append(
                f"non-terminal scene at index {i} must have terminal='continue'"
            )

    # Scene count must not exceed remaining budget.
    if len(plan.scenes) > pacing.remaining_budget:
        errors.append(
            f"segment has {len(plan.scenes)} scenes but only "
            f"{pacing.remaining_budget} remaining in budget"
        )

    # Validate each scene plan individually.
    for scene in plan.scenes:
        try:
            validate_scene_plan(pack, state, scene)
        except ProposalRejected as exc:
            errors.extend(f"scene {scene.scene_id}: {e}" for e in exc.errors)

    # Thread operations: no new threads in convergence window.
    if pacing.in_convergence or pacing.max_new_threads == 0:
        new_thread_count = sum(1 for op in plan.thread_ops if op.kind == "open")
        if new_thread_count > 0:
            errors.append(
                f"cannot open {new_thread_count} new thread(s) in convergence window"
            )
    else:
        new_thread_count = sum(1 for op in plan.thread_ops if op.kind == "open")
        if new_thread_count > pacing.max_new_threads:
            errors.append(
                f"segment opens {new_thread_count} threads but budget is "
                f"{pacing.max_new_threads}"
            )

    # Validate thread operation referential integrity.
    existing_thread_ids = set(state.threads.keys())
    for op in plan.thread_ops:
        if op.kind == "open" and op.thread_id in existing_thread_ids:
            errors.append(f"thread already exists: {op.thread_id}")
        if op.kind in ("advance", "close") and op.thread_id not in existing_thread_ids:
            errors.append(f"unknown thread for {op.kind}: {op.thread_id}")

    if errors:
        raise ProposalRejected(errors)
    return plan


def validate_segment_draft(
    plan: SegmentPlan,
    draft: SegmentDraft,
) -> SegmentDraft:
    errors: list[str] = []

    if draft.segment_id != plan.segment_id:
        errors.append(
            f"segment_id mismatch: expected {plan.segment_id}, got {draft.segment_id}"
        )

    if len(draft.scene_drafts) != len(plan.scenes):
        errors.append(
            f"scene count mismatch: plan has {len(plan.scenes)} scenes, "
            f"draft has {len(draft.scene_drafts)}"
        )
        if errors:
            raise ProposalRejected(errors)

    # Validate each scene draft against its plan.
    for scene_plan, scene_draft in zip(plan.scenes, draft.scene_drafts):
        try:
            validate_scene_draft(scene_plan, scene_draft)
        except ProposalRejected as exc:
            errors.extend(f"scene {scene_plan.scene_id}: {e}" for e in exc.errors)

    # For ending terminal, draft must have ending.
    if plan.terminal == "ending" and draft.ending is None:
        errors.append("ending terminal requires draft.ending")

    # For ending terminal, draft.ending title must match proposal title.
    if (
        plan.terminal == "ending"
        and plan.ending_proposal is not None
        and draft.ending is not None
        and draft.ending.title != plan.ending_proposal.title
    ):
        errors.append(
            f"ending title mismatch: proposal has '{plan.ending_proposal.title}', "
            f"draft has '{draft.ending.title}'"
        )

    if errors:
        raise ProposalRejected(errors)
    return draft
