"""Canonical condition evaluation context for script-pack predicates."""

from __future__ import annotations

from typing import Any

from src.story.state import SessionState


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
