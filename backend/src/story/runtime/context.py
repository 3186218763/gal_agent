"""Canonical condition evaluation context and truth-safe model contexts."""

from __future__ import annotations

from typing import Any

from src.story.script_pack.models import CompiledScriptPack, EndingSource
from src.story.state import FactTruthStatus, FactVisibility, SessionState

from .contracts import ScenePlan


def build_condition_context(state: SessionState) -> dict[str, Any]:
    return {
        "relationships": {key: dict(value) for key, value in state.world.relationships.items()},
        "facts": {
            key: {
                "truth_status": value.truth_status.value,
                "visibility": value.visibility.value,
                "value": value.value,
            }
            for key, value in state.facts.items()
        },
        "goals": {
            key: {
                "status": value.status.value,
                "progress": value.progress,
                "completed": value.completed,
            }
            for key, value in state.world.goals.items()
        },
        "world": {
            "location_id": state.world.location_id,
            "phase": state.world.phase.value,
            "pressure": state.world.pressure,
        },
        "session": {
            "scene_count": state.world.scene_count,
            "revision": state.revision,
            "status": state.status.value,
        },
        "threads": {
            key: {"status": value.status.value, "urgency": value.urgency}
            for key, value in state.threads.items()
        },
    }


def _planner_fact_views(pack: CompiledScriptPack, state: SessionState) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for fact in pack.source.facts.fixed:
        runtime = state.facts[fact.id]
        views.append(
            {
                "id": fact.id,
                "kind": "fixed",
                "statement": fact.statement,
                "visibility": runtime.visibility.value,
                "known_by": sorted(runtime.known_by),
            }
        )
    for question in pack.source.facts.latent_questions:
        runtime = state.facts[question.id]
        view: dict[str, Any] = {
            "id": question.id,
            "kind": "latent",
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
    condition_context = build_condition_context(state)
    for fact in pack.source.facts.derived:
        views.append(
            {
                "id": fact.id,
                "kind": "derived",
                "value": pack.conditions[f"fact.{fact.id}.derived"].evaluate(condition_context),
            }
        )
    return views


def build_planner_context(pack: CompiledScriptPack, state: SessionState) -> dict[str, Any]:
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
                "voice": character.voice.model_dump(mode="json"),
                "drives": character.drives,
                "boundaries": character.boundaries.model_dump(mode="json"),
                "capabilities": character.capabilities,
                "relationship": state.world.relationships[character.id],
                "emotional_state": runtime.emotional_state,
                "known_fact_ids": sorted(runtime.knowledge),
                "beliefs": {
                    key: value.model_dump(mode="json") for key, value in runtime.beliefs.items()
                },
                "suspicions": {
                    key: value.model_dump(mode="json")
                    for key, value in runtime.suspicions.items()
                },
            }
        )
    return {
        "pack": {
            "id": source.identity.id,
            "language": source.identity.language,
            "viewpoint": source.experience.viewpoint,
            "prose_style": source.experience.prose_style,
            "tone": source.experience.tone,
            "premise": source.world.premise,
            "immutable_rules": source.world.immutable_rules,
            "forbidden_content": source.experience.forbidden_content,
        },
        "state": build_condition_context(state),
        "facts": _planner_fact_views(pack, state),
        "characters": characters,
        "available_action_ids": sorted(pack.action_ids & set(source.protagonist.capabilities)),
        "goals": [goal.model_dump(mode="json") for goal in source.goals],
    }


def _known_fact_view(
    pack: CompiledScriptPack, state: SessionState, fact_id: str
) -> dict[str, Any] | None:
    runtime = state.facts.get(fact_id)
    if runtime is None or runtime.truth_status != FactTruthStatus.COMMITTED:
        return None
    fixed = next((item for item in pack.source.facts.fixed if item.id == fact_id), None)
    return {
        "id": fact_id,
        "value": fixed.statement if fixed is not None else runtime.value,
        "visibility": runtime.visibility.value,
    }


def build_writer_context(
    pack: CompiledScriptPack,
    state: SessionState,
    present_character_ids: tuple[str, ...] | list[str],
    approved_plan: ScenePlan,
) -> dict[str, Any]:
    sources = {item.id: item for item in pack.source.characters}
    characters = []
    for character_id in present_character_ids:
        source = sources[character_id]
        runtime = state.characters[character_id]
        known_facts = [
            view
            for fact_id in sorted(runtime.knowledge)
            if (view := _known_fact_view(pack, state, fact_id)) is not None
        ]
        characters.append(
            {
                "id": character_id,
                "name": source.name,
                "public_profile": source.public_profile,
                "personality": source.personality.model_dump(mode="json"),
                "voice": source.voice.model_dump(mode="json"),
                "drives": source.drives,
                "boundaries": source.boundaries.model_dump(mode="json"),
                "relationship": state.world.relationships[character_id],
                "emotional_state": runtime.emotional_state,
                "known_facts": known_facts,
                "beliefs": {
                    key: value.model_dump(mode="json") for key, value in runtime.beliefs.items()
                },
                "suspicions": {
                    key: value.model_dump(mode="json")
                    for key, value in runtime.suspicions.items()
                },
            }
        )
    approved_fact_ids = set(approved_plan.related_fact_ids)
    approved_fact_ids.update(item.fact_id for item in approved_plan.fact_commits)
    narration_facts = [
        item for item in _planner_fact_views(pack, state) if item["id"] in approved_fact_ids
    ]
    return {
        "language": pack.source.identity.language,
        "viewpoint": pack.source.experience.viewpoint,
        "prose_style": pack.source.experience.prose_style,
        "tone": pack.source.experience.tone,
        "forbidden_content": pack.source.experience.forbidden_content,
        "approved_plan": approved_plan.model_dump(mode="json"),
        "approved_narration_facts": narration_facts,
        "characters": characters,
    }


def build_ending_context(
    pack: CompiledScriptPack, state: SessionState, ending: EndingSource
) -> dict[str, Any]:
    revealed_facts = []
    fixed = {item.id: item for item in pack.source.facts.fixed}
    for fact_id, runtime in state.facts.items():
        if runtime.visibility != FactVisibility.REVEALED:
            continue
        source = fixed.get(fact_id)
        revealed_facts.append(
            {
                "id": fact_id,
                "value": source.statement if source is not None else runtime.value,
            }
        )
    return {
        "language": pack.source.identity.language,
        "viewpoint": pack.source.experience.viewpoint,
        "prose_style": pack.source.experience.prose_style,
        "tone": pack.source.experience.tone,
        "ending": ending.model_dump(mode="json"),
        "relationships": {key: dict(value) for key, value in state.world.relationships.items()},
        "goals": {
            key: value.model_dump(mode="json") for key, value in state.world.goals.items()
        },
        "revealed_facts": revealed_facts,
        "characters": [
            {
                "id": item.id,
                "name": item.name,
                "public_profile": item.public_profile,
                "voice": item.voice.model_dump(mode="json"),
                "boundaries": item.boundaries.model_dump(mode="json"),
            }
            for item in pack.source.characters
        ],
    }
