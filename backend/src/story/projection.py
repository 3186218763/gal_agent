"""Safe public projections of script packs and session state.

Only data a browser player is entitled to see crosses this boundary.
Internal state (fact truth, character knowledge, beliefs, suspicions,
goals, threads, seeds, hashes) never appears in a projection.
"""

from __future__ import annotations

from src.story.script_pack.models import CompiledScriptPack
from src.story.state import (
    NarrativeBlock,
    PresentedChoice,
    SessionState,
)
from src.story.state.models import FrozenModel


class PackCharacterProjection(FrozenModel):
    character_id: str
    name: str
    public_profile: str


class PackLocationProjection(FrozenModel):
    location_id: str
    name: str


class PackProjection(FrozenModel):
    pack_id: str
    title: str
    language: str
    characters: tuple[PackCharacterProjection, ...]
    locations: tuple[PackLocationProjection, ...]


class SessionProjection(FrozenModel):
    session_id: str
    pack_id: str
    revision: int
    status: str
    phase: str
    scene_count: int
    pending_decision_id: str | None
    scene_id: str | None
    blocks: tuple[NarrativeBlock, ...] = ()
    choices: tuple[PresentedChoice, ...] = ()
    ending_id: str | None = None
    ending_title: str | None = None
    location_id: str
    time_label: str
    present_character_ids: tuple[str, ...]


def project_pack(pack: CompiledScriptPack) -> PackProjection:
    return PackProjection(
        pack_id=pack.source.identity.id,
        title=pack.source.identity.title,
        language=pack.source.identity.language,
        characters=tuple(
            PackCharacterProjection(
                character_id=character.id,
                name=character.name,
                public_profile=character.public_profile,
            )
            for character in pack.source.characters
        ),
        locations=tuple(
            PackLocationProjection(location_id=location.id, name=location.name)
            for location in pack.source.world.locations
        ),
    )


def project_session(state: SessionState) -> SessionProjection:
    if state.pending_scene is not None:
        scene_id = state.pending_scene.scene_id
        blocks = state.pending_scene.blocks
    elif state.ending is not None:
        scene_id = None
        blocks = state.ending.blocks
    else:
        scene_id = None
        blocks = ()
    return SessionProjection(
        session_id=state.session_id,
        pack_id=state.pack_id,
        revision=state.revision,
        status=state.status.value,
        phase=state.world.phase.value,
        scene_count=state.world.scene_count,
        pending_decision_id=(
            state.pending_decision.decision_id
            if state.pending_decision is not None
            else None
        ),
        scene_id=scene_id,
        blocks=blocks,
        choices=(
            state.pending_decision.choices if state.pending_decision is not None else ()
        ),
        ending_id=state.ending.ending_id if state.ending is not None else None,
        ending_title=state.ending.title if state.ending is not None else None,
        location_id=state.world.location_id,
        time_label=state.world.time_label,
        present_character_ids=state.world.present_character_ids,
    )


__all__ = [
    "PackCharacterProjection",
    "PackLocationProjection",
    "PackProjection",
    "SessionProjection",
    "project_pack",
    "project_session",
]
