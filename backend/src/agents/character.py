"""
Character Agent (SDK) — produces NPC dialogue for the GameKernel.

openai-agents is optional at import time; missing package only errors when
SdkCharacter is constructed / used. SDK load is shared with director.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.domain.setting_pack import CharacterDef, SettingPack
from src.domain.world_state import WorldState

logger = logging.getLogger(__name__)

_CHARACTER_SYSTEM = """你是 Galgame 中的 NPC 角色扮演者。

规则：
1. 始终保持角色一致性，按性格与信任度说话
2. 直接以第一人称输出对话，不要加角色名前缀或「我说：」
3. 不要透露你是 AI，不要元话语
4. 对话简洁（1-3 句），自然贴合情境与指令
5. 信任度高时更友好坦诚；信任度低时警惕、冷淡或保留
"""


def build_character_prompt(
    name: str,
    personality: str,
    trust: int,
    directive: str,
    memories: list[str],
) -> str:
    """Pure prompt builder for unit tests (no network)."""
    mem_block = "\n".join(f"- {m}" for m in memories) if memories else "- （无）"
    return (
        f"角色名：{name}\n"
        f"性格：{personality}\n"
        f"对玩家的信任度：{trust}/100\n\n"
        f"导演指令（directive）：{directive or '（无）'}\n\n"
        f"近期记忆（memories）：\n{mem_block}\n\n"
        "请根据性格、信任度与指令说一段符合角色的话。"
        "直接输出对话内容，不要角色名前缀或标记。"
    )


def _lookup_character(pack: SettingPack, char_id: str) -> Optional[CharacterDef]:
    for c in pack.characters:
        if c.id == char_id:
            return c
    return None


def _trust_for(state: WorldState, char_id: str) -> int:
    rel = state.relationships.get(char_id)
    if rel is None:
        return 50
    return int(rel.trust)


def _fallback_line(name: str) -> str:
    return f"{name}: ……"


class SdkCharacter:
    """CharacterPort implementation via OpenAI Agents SDK."""

    def __init__(self, model: Optional[str] = None) -> None:
        # Import lazily via director helper (handles local `agents` shadow).
        from src.agents.director import _load_openai_agents_sdk

        Agent, _Runner = _load_openai_agents_sdk()
        kwargs: dict[str, Any] = {
            "name": "Character",
            "instructions": _CHARACTER_SYSTEM,
        }
        if model:
            kwargs["model"] = model
        self._agent = Agent(**kwargs)
        self._runner = _Runner

    async def generate_dialogue(
        self,
        char_id: str,
        directive: str,
        state: WorldState,
        pack: SettingPack,
        memories: list[str],
    ) -> str:
        char = _lookup_character(pack, char_id)
        name = char.name if char else char_id
        personality = char.personality if char else ""
        trust = _trust_for(state, char_id)

        prompt = build_character_prompt(
            name=name,
            personality=personality,
            trust=trust,
            directive=directive or "",
            memories=list(memories or []),
        )
        try:
            result = await self._runner.run(self._agent, input=prompt)
            output_text = getattr(result, "final_output", None) or str(result)
            dialogue = str(output_text).strip()
            if not dialogue:
                return _fallback_line(name)
            return dialogue
        except Exception:
            logger.warning(
                "SdkCharacter.generate_dialogue failed for %s; using fallback",
                char_id,
                exc_info=True,
            )
            return _fallback_line(name)
