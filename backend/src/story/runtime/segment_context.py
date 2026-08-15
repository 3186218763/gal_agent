"""Per-character knowledge-scoped context builders for segment Director and Writer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.story.script_pack.models import CompiledScriptPack
from src.story.state import (
    FactTruthStatus,
    PresentedChoice,
    SessionState,
)

from .contracts import PacingEnvelope, SegmentPlan

# ---------------------------------------------------------------------------
# Per-layer budgets (issue 05)
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Cheap deterministic token proxy: CJK-heavy prose ~1 token per character,
    latin text ~1 token per 4 characters."""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk + (len(text) - cjk + 3) // 4


@dataclass(frozen=True)
class ContextBudgets:
    """Independent quotas for the three history layers of writer context.

    Each layer spends only its own budget: a huge digest never shrinks the
    verbatim window and a long window never squeezes the summaries out.
    """

    scene_summary_max: int = 24
    outstanding_obligation_max: int = 12
    open_promise_max: int = 8
    recent_prose_token_budget: int = 1000


DEFAULT_CONTEXT_BUDGETS = ContextBudgets()


def _get_world_setting(source):
    """Return world setting from v1.0 or v2.0 pack."""
    if hasattr(source, "world_setting"):
        return source.world_setting
    return source.world  # v1.0 fallback


def _get_completion_requirements(source):
    """Return completion requirements (empty for v1.0)."""
    return getattr(source, "completion_requirements", ())


def _get_immutable_rules(source):
    """Return immutable rules from v1.0 or v2.0 pack."""
    if hasattr(source, "world_setting"):
        return source.world_setting.immutable_rules
    return source.world.immutable_rules


def _get_forbidden_content(source):
    """Return forbidden content from v1.0 or v2.0 pack."""
    if hasattr(source, "world_setting"):
        return source.world_setting.forbidden_content
    return source.experience.forbidden_content


def _fact_summary_views(pack: CompiledScriptPack, state: SessionState) -> list[dict[str, Any]]:
    """Return fact summaries suitable for the Director (structural, not prose)."""
    views: list[dict[str, Any]] = []
    for fact in pack.source.facts.fixed:
        runtime = state.facts[fact.id]
        views.append(
            {
                "id": fact.id,
                "kind": "fixed",
                "committable": False,
                "visibility": runtime.visibility.value,
                "known_by": sorted(runtime.known_by),
            }
        )
    for question in pack.source.facts.latent_questions:
        runtime = state.facts[question.id]
        view: dict[str, Any] = {
            "id": question.id,
            "kind": "latent",
            "committable": True,
            "question": question.question,
            "truth_status": runtime.truth_status.value,
            "visibility": runtime.visibility.value,
            "evidence_required": runtime.evidence_required,
            "evidence_count": len(runtime.evidence_event_ids),
        }
        if runtime.truth_status == FactTruthStatus.COMMITTED:
            view["value"] = runtime.value
        else:
            # Mutual-exclusion group: exactly one candidate value can ever
            # become the committed truth; prose may never present two of
            # them as simultaneously true.
            view["candidates"] = [
                {"value": item.value, "requirements": item.requirements}
                for item in question.candidates
            ]
            view["mutually_exclusive"] = True
        views.append(view)
    return views


def _thread_views(state: SessionState) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for thread_id, thread in state.threads.items():
        views.append(
            {
                "id": thread_id,
                "type": thread.type,
                "status": thread.status.value,
                "involved_character_ids": thread.involved_character_ids,
                "related_fact_ids": thread.related_fact_ids,
                "urgency": thread.urgency,
            }
        )
    return views


def _completion_requirement_views(
    pack: CompiledScriptPack, state: SessionState
) -> list[dict[str, Any]]:
    """Return completion requirement views from goals (v1.0 pack compatibility)."""
    views: list[dict[str, Any]] = []
    for goal in pack.source.goals:
        runtime = state.world.goals.get(goal.id)
        views.append(
            {
                "id": goal.id,
                "description": goal.desire,
                "owner": goal.owner,
                "urgency": goal.urgency,
                "status": runtime.status.value if runtime else "active",
                "progress": runtime.progress if runtime else 0.0,
                "success_condition": goal.success_condition,
                "failure_condition": goal.failure_condition,
            }
        )
    return views


def player_choice_view(
    pending_choice: PresentedChoice,
    pack: CompiledScriptPack,
) -> dict[str, Any]:
    """The player's just-committed choice as the writer's context sees it.

    Two layers: a natural-language confirmation sentence (text layer, what
    the prose must visibly honor) plus the structured Choice Meaning fields
    (intent/stance/risk/obligation).  Lives for exactly one segment — the
    one generated immediately after the selection; later segments only see
    the choice through the event digest.
    """
    language = pack.source.identity.language
    names = {item.id: item.name for item in pack.source.characters}
    target = names.get(pending_choice.target_character_id or "", None)
    choice = pending_choice

    if language.lower().startswith("zh"):
        parts = [f"玩家刚刚选择了「{choice.label}」。这一选择表明：{choice.intent}。"]
        if target is not None:
            axis = choice.stance_axis or "态度"
            value = choice.stance_value or ""
            parts.append(f"玩家对{target}的立场（{axis}）随之明确：{value}。")
        if choice.accepted_risk:
            parts.append(f"玩家已接受的风险：{choice.accepted_risk}。")
        if choice.potential_obligation_kind:
            parts.append(f"这一选择可能带来义务：{choice.potential_obligation_kind}。")
    else:
        parts = [f'The player just chose "{choice.label}". This commits to: {choice.intent}.']
        if target is not None:
            axis = choice.stance_axis or "attitude"
            value = choice.stance_value or ""
            parts.append(f"The player's stance toward {target} ({axis}) is now: {value}.")
        if choice.accepted_risk:
            parts.append(f"The player accepted this risk: {choice.accepted_risk}.")
        if choice.potential_obligation_kind:
            parts.append(f"This choice may create an obligation: {choice.potential_obligation_kind}.")

    return {
        "confirmation": " ".join(parts),
        "structured": {
            "option_id": choice.id,
            "action_id": choice.action_id,
            "label": choice.label,
            "intent": choice.intent,
            "target_character_id": choice.target_character_id,
            "stance_axis": choice.stance_axis,
            "stance_value": choice.stance_value,
            "accepted_risk": choice.accepted_risk,
            "potential_obligation_kind": choice.potential_obligation_kind,
            "conflict_axis_id": choice.conflict_axis_id,
        },
    }


def _event_trace_digest(
    state: SessionState,
    budgets: ContextBudgets = DEFAULT_CONTEXT_BUDGETS,
) -> dict[str, Any]:
    """Deterministically rebuild the story-so-far digest from replayed state.

    Same events in, same digest out — no model calls, no wall-clock, no
    randomness.  Carries the scene outline plus the open ledgers: choices
    register obligations automatically, and settled ones disappear from
    this view forever (the writer only ever sees outstanding work).

    Each list lives under its own quota: the newest scene summaries are
    kept, the oldest beyond ``scene_summary_max`` drop out with the drop
    counted; ledgers keep the first entries of their deterministic sort.
    """
    outstanding_obligations = [
        {
            "obligation_id": obligation.obligation_id,
            "kind": obligation.kind,
            "burden": obligation.burden,
            "character_id": obligation.character_id,
            "source_choice_event_id": obligation.source_choice_event_id,
        }
        for _oid, obligation in sorted(state.drama.obligations.items())
        if obligation.status == "open"
    ]
    obligations_omitted = max(0, len(outstanding_obligations) - budgets.outstanding_obligation_max)
    outstanding_obligations = outstanding_obligations[: budgets.outstanding_obligation_max]
    open_promises = [
        {
            "promise_id": promise.promise_id,
            "expectation": promise.expectation,
            "status": promise.status.value,
            "soft_deadline_decision": promise.soft_deadline_decision,
            "hard_deadline_decision": promise.hard_deadline_decision,
        }
        for _pid, promise in sorted(state.drama.promises.items())
        if promise.status.value in {"open", "escalated", "transformed"}
    ]
    promises_omitted = max(0, len(open_promises) - budgets.open_promise_max)
    open_promises = open_promises[: budgets.open_promise_max]
    summaries = state.scene_summaries
    summaries_omitted = max(0, len(summaries) - budgets.scene_summary_max)
    return {
        "scene_count": state.world.scene_count,
        "revision": state.revision,
        "scene_summaries": [
            {"scene_id": record.scene_id, "summary": record.summary}
            for record in summaries[len(summaries) - budgets.scene_summary_max :]
        ],
        "scene_summaries_omitted": summaries_omitted,
        "outstanding_obligations": outstanding_obligations,
        "outstanding_obligations_omitted": obligations_omitted,
        "open_promises": open_promises,
        "open_promises_omitted": promises_omitted,
        "resolved_thread_count": sum(
            1 for t in state.threads.values() if t.status.value == "resolved"
        ),
        "open_thread_count": sum(1 for t in state.threads.values() if t.status.value == "open"),
    }


def recent_prose_window(
    state: SessionState,
    budgets: ContextBudgets = DEFAULT_CONTEXT_BUDGETS,
) -> dict[str, Any] | None:
    """The verbatim tail of the committed prose (issue 05's window layer).

    Fills from the newest block backwards until the layer's token budget is
    spent — the writer always sees the seam it must continue from, in the
    exact quotation style and formatting the player last read.  Returns
    None when no prose has been committed yet.
    """
    ring = state.recent_prose_blocks
    if not ring:
        return None
    kept: list[Any] = []
    spent = 0
    omitted = 0
    for record in reversed(ring):
        cost = estimate_tokens(record.text)
        if kept and spent + cost > budgets.recent_prose_token_budget:
            omitted = len(ring) - len(kept)
            break
        kept.append(record)
        spent += cost
    kept.reverse()  # reading order: oldest kept block first
    return {
        "token_budget": budgets.recent_prose_token_budget,
        "blocks_omitted": omitted,
        "blocks": [
            {
                "scene_id": record.scene_id,
                "kind": record.kind,
                "character_id": record.character_id,
                "text": record.text,
            }
            for record in kept
        ],
    }


def build_director_context(
    pack: CompiledScriptPack,
    state: SessionState,
    pacing: PacingEnvelope,
    budgets: ContextBudgets = DEFAULT_CONTEXT_BUDGETS,
) -> dict[str, Any]:
    """Build the context for the Segment Director Agent.

    The Director sees world truth, event digest, character knowledge map,
    completion requirements, open threads, and the pacing envelope.
    It does NOT receive raw prose context — it returns structural plans.
    """
    source = pack.source
    characters = []
    for character in source.characters:
        runtime = state.characters[character.id]
        characters.append(
            {
                "id": character.id,
                "name": character.name,
                "public_profile": character.public_profile,
                "personality": character.personality.model_dump(mode="json"),
                "drives": character.drives,
                "boundaries": character.boundaries.model_dump(mode="json"),
                "relationship": dict(state.world.relationships.get(character.id, {})),
                "emotional_state": dict(runtime.emotional_state),
                "known_fact_ids": sorted(runtime.knowledge),
                "beliefs": {k: v.model_dump(mode="json") for k, v in runtime.beliefs.items()},
                "secrets": [],  # Director does not get raw secrets
            }
        )
    return {
        "pack": {
            "id": source.identity.id,
            "language": source.identity.language,
            "premise": _get_world_setting(source).premise,
            "immutable_rules": _get_immutable_rules(source),
            "forbidden_content": _get_forbidden_content(source),
            "protagonist_id": source.protagonist.id,
            "protagonist_capabilities": list(source.protagonist.capabilities),
        },
        "world_truth": {
            "location_id": state.world.location_id,
            "phase": state.world.phase.value,
            "scene_count": state.world.scene_count,
            "pressure": state.world.pressure,
            "present_character_ids": list(state.world.present_character_ids),
        },
        "facts": _fact_summary_views(pack, state),
        "goals": _completion_requirement_views(pack, state),
        "completion_requirements": _completion_requirement_views(pack, state),
        "open_threads": _thread_views(state),
        "characters": characters,
        "pacing": pacing.model_dump(mode="json"),
        "available_action_ids": sorted(pack.action_ids & set(source.protagonist.capabilities)),
        "event_trace": _event_trace_digest(state, budgets),
    }


def _character_known_facts(
    pack: CompiledScriptPack,
    state: SessionState,
    character_id: str,
) -> list[dict[str, Any]]:
    """Return only the facts this specific character knows."""
    runtime = state.characters.get(character_id)
    if runtime is None:
        return []
    fixed = {item.id: item for item in pack.source.facts.fixed}
    views: list[dict[str, Any]] = []
    for fact_id in sorted(runtime.knowledge):
        fact_runtime = state.facts.get(fact_id)
        if fact_runtime is None or fact_runtime.truth_status != FactTruthStatus.COMMITTED:
            continue
        source = fixed.get(fact_id)
        views.append(
            {
                "id": fact_id,
                "value": source.statement if source is not None else fact_runtime.value,
                "visibility": fact_runtime.visibility.value,
            }
        )
    return views


def build_segment_writer_context(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    *,
    pending_choice: PresentedChoice | None = None,
    budgets: ContextBudgets = DEFAULT_CONTEXT_BUDGETS,
) -> dict[str, Any]:
    """Build per-speaker-scoped context for the Segment Writer Agent.

    Each present character receives ONLY its own knowledge, beliefs, voice,
    and boundaries.  The writer never gets an unfiltered list of every
    character's secrets.
    """
    source = pack.source
    sources = {item.id: item for item in source.characters}

    all_present: set[str] = set()
    for scene in plan.scenes:
        all_present.update(scene.present_character_ids)

    characters: list[dict[str, Any]] = []
    for character_id in sorted(all_present):
        char_source = sources.get(character_id)
        if char_source is None:
            continue
        runtime = state.characters[character_id]
        characters.append(
            {
                "id": character_id,
                "name": char_source.name,
                "public_profile": char_source.public_profile,
                "personality": char_source.personality.model_dump(mode="json"),
                "voice": char_source.voice.model_dump(mode="json"),
                "drives": char_source.drives,
                "boundaries": char_source.boundaries.model_dump(mode="json"),
                "relationship": dict(state.world.relationships.get(character_id, {})),
                "emotional_state": dict(runtime.emotional_state),
                "known_facts": _character_known_facts(pack, state, character_id),
                "beliefs": {k: v.model_dump(mode="json") for k, v in runtime.beliefs.items()},
            }
        )

    # Collect approved narration facts (facts the plan references)
    approved_fact_ids: set[str] = set()
    for scene in plan.scenes:
        approved_fact_ids.update(scene.related_fact_ids)
        approved_fact_ids.update(fc.fact_id for fc in scene.fact_commits)
    approved_fact_ids.update(fc.fact_id for fc in plan.new_facts)
    narration_facts = [
        view for view in _fact_summary_views(pack, state) if view["id"] in approved_fact_ids
    ]

    world_setting = _get_world_setting(source)
    ctx: dict[str, Any] = {
        "language": source.identity.language,
        "viewpoint": source.experience.viewpoint,
        "prose_style": source.experience.prose_style,
        "tone": source.experience.tone,
        "forbidden_content": _get_forbidden_content(source),
        "world_rules": {
            "premise": world_setting.premise,
            "immutable_rules": _get_immutable_rules(source),
            "locations": [{"id": loc.id, "name": loc.name} for loc in world_setting.locations],
        },
        "approved_plan": plan.model_dump(mode="json"),
        "approved_narration_facts": narration_facts,
        "characters": characters,
        "event_trace": _event_trace_digest(state, budgets),
    }

    # Verbatim tail window: the literal prose blocks committed just before
    # this segment, for seam/quotation-style continuity (issue 05).
    window = recent_prose_window(state, budgets)
    if window is not None:
        ctx["recent_prose"] = window

    # The just-committed choice speaks loudest in the segment that directly
    # follows it; callers pass ``pending_choice`` for that segment only.
    if pending_choice is not None:
        ctx["player_choice"] = player_choice_view(pending_choice, pack)

    if plan.terminal == "ending" and plan.ending_proposal is not None:
        ctx["ending_proposal"] = plan.ending_proposal.model_dump(mode="json")

    return ctx
