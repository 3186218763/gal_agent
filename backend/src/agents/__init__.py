"""
Agents 包初始化

Director / Character / Choice pure helpers are eager; SDK classes load
openai-agents lazily only when constructed (via director._load_openai_agents_sdk).
"""
from .character import SdkCharacter, build_character_prompt
from .choice import SdkChoice, build_choice_prompt, parse_choice_output
from .director import SdkDirector, build_director_prompt

__all__ = [
    "SdkDirector",
    "build_director_prompt",
    "SdkCharacter",
    "build_character_prompt",
    "SdkChoice",
    "build_choice_prompt",
    "parse_choice_output",
]
