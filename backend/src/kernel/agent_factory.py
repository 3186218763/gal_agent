"""Agent port factory: stubs vs SDK implementations."""
from __future__ import annotations

from src.kernel.ports import CharacterPort, ChoicePort, DirectorPort, MemoryPort
from src.kernel.stubs import StubCharacter, StubChoice, StubDirector, StubMemory


def build_ports(
    use_stubs: bool = True,
) -> tuple[DirectorPort, CharacterPort, ChoicePort, MemoryPort]:
    """
    Build Director/Character/Choice/Memory ports.

    Stubs for all when use_stubs=True; when False, SdkDirector + SdkCharacter
    + SdkChoice and stub memory until later tasks wire it.
    """
    if use_stubs:
        return (
            StubDirector(),
            StubCharacter(),
            StubChoice(),
            StubMemory(),
        )

    from src.agents.character import SdkCharacter
    from src.agents.choice import SdkChoice
    from src.agents.director import SdkDirector

    return (
        SdkDirector(),
        SdkCharacter(),
        SdkChoice(),
        StubMemory(),
    )
