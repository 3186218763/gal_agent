"""
Agents 包初始化

Director exports are eager (pure prompt + SdkDirector lazy-SDK).
Character is lazy so importing build_director_prompt does not require openai-agents.
"""
from .director import SdkDirector, build_director_prompt

__all__ = [
    "SdkDirector",
    "build_director_prompt",
    "CharacterAgent",
    "CharacterFactory",
]


def __getattr__(name: str):
    if name in ("CharacterAgent", "CharacterFactory"):
        from .character import CharacterAgent, CharacterFactory

        return CharacterAgent if name == "CharacterAgent" else CharacterFactory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
