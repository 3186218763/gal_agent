"""Per-character knowledge-scoped context builders for segment Director and Writer."""

from __future__ import annotations

from typing import Any

from src.story.script_pack.models import CompiledScriptPack
from src.story.state import (
    FactTruthStatus,
    SessionState,
)

from .contracts import PacingEnvelope, SegmentPlan


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


def _fact_summary_views(
    pack: CompiledScriptPack, state: SessionState
) -> list[dict[str, Any]]:
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
            view["candidates"] = [
                {"value": item.value, "requirements": item.requirements}
                for item in question.candidates
            ]
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


def _event_trace_digest(state: SessionState) -> dict[str, Any]:
    """Build a summary of recent events for the Director context."""
    return {
        "scene_count": state.world.scene_count,
        "revision": state.revision,
        "recent_scene_summaries": [],  # Would be populated from event store in full implementation
        "resolved_thread_count": sum(1 for t in state.threads.values() if t.status.value == "resolved"),
        "open_thread_count": sum(1 for t in state.threads.values() if t.status.value == "open"),
    }


def build_director_context(
    pack: CompiledScriptPack,
    state: SessionState,
    pacing: PacingEnvelope,
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
                "beliefs": {
                    k: v.model_dump(mode="json") for k, v in runtime.beliefs.items()
                },
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
        "available_action_ids": sorted(
            pack.action_ids & set(source.protagonist.capabilities)
        ),
        "event_trace": _event_trace_digest(state),
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
                "beliefs": {
                    k: v.model_dump(mode="json")
                    for k, v in runtime.beliefs.items()
                },
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
            "locations": [
                {"id": loc.id, "name": loc.name}
                for loc in world_setting.locations
            ],
        },
        "approved_plan": plan.model_dump(mode="json"),
        "approved_narration_facts": narration_facts,
        "characters": characters,
    }

    if plan.terminal == "ending" and plan.ending_proposal is not None:
        ctx["ending_proposal"] = plan.ending_proposal.model_dump(mode="json")

    return ctx
