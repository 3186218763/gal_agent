# backend/src/kernel/ports.py
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from src.domain.events import EventDatabase
from src.domain.options import ChoiceOption
from src.domain.scene import SceneIntent
from src.domain.setting_pack import SettingPack
from src.domain.world_state import WorldState


@runtime_checkable
class DirectorPort(Protocol):
    async def generate_scene(
        self,
        state: WorldState,
        pack: SettingPack,
        memories: list[str],
    ) -> SceneIntent: ...


@runtime_checkable
class CharacterPort(Protocol):
    async def generate_dialogue(
        self,
        char_id: str,
        directive: str,
        state: WorldState,
        pack: SettingPack,
        memories: list[str],
    ) -> str: ...


@runtime_checkable
class ChoicePort(Protocol):
    async def generate_options(
        self,
        state: WorldState,
        pack: SettingPack,
        scene: SceneIntent,
        memories: list[str],
    ) -> list[ChoiceOption]: ...


@runtime_checkable
class MemoryPort(Protocol):
    async def recall(
        self,
        state: WorldState,
        pack: SettingPack,
        events: EventDatabase,
        k: int = 5,
    ) -> list[str]: ...
