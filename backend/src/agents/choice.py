"""
Choice Agent (SDK) — produces player options for the GameKernel.

openai-agents is optional at import time; missing package only errors when
SdkChoice is constructed / used. SDK load is shared with director.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from src.domain.options import ChoiceOption, PredictedConsequences
from src.domain.scene import SceneIntent
from src.domain.setting_pack import SettingPack
from src.domain.world_state import WorldState

_CHOICE_SYSTEM = """你是 Galgame 的选项生成器（Choice Agent）。

职责：
1. 根据当前场景与世界状态，生成 2~4 个互有差分的玩家选项
2. 每个选项必须有可区分的 predicted_consequences（假选择禁止）
3. 只输出 **一个 JSON 对象**，不要 Markdown 代码围栏或解释文字

严格 JSON 结构：
{
  "options": [
    {
      "text": string,                    // 选项文案，长度 2..50
      "stance": string,                  // 如 bold / cautious / neutral / withdraw
      "predicted_consequences": {
        "flag_changes": object,          // 至少一项后果：flag / 关系 / goal
        "relationship_deltas": object,   // char_id -> {trust?: int, romance?: int, ...}
        "goal_effects": [                // 可选
          {"goal_id": string, "delta_progress": number, "force_complete": bool}
        ],
        "tension_delta": int,            // 建议 [-2, 2]
        "tags": string[]                 // 短标签，用于去重
      },
      "narrative_preview": string        // 选后一句话预览
    }
  ]
}

规则：
- 选项数 n 必须在 2..4
- 每个选项至少改 flag / 关系 / goal 之一（后果不可全空）
- 各选项后果 fingerprint 必须不同（不同 flag / 不同角色关系 / 不同 goal）
- relationship 的 char_id 与 goal_id 必须使用世界内已有 id
- text 简洁可点选；不要输出旁白长文或角色完整对话
"""


def build_choice_prompt(
    narration: str,
    mood: str,
    phase: str,
    tension: int,
    character_ids: list[str],
    goal_ids: list[str],
    memories: list[str],
    focus_goal_ids: list[str] | None = None,
) -> str:
    """Pure prompt builder for unit tests (no network)."""
    mem_block = "\n".join(f"- {m}" for m in memories) if memories else "- （无）"
    chars = ", ".join(character_ids) if character_ids else "（无）"
    goals = ", ".join(goal_ids) if goal_ids else "（无）"
    focus = ", ".join(focus_goal_ids or []) if focus_goal_ids else "（无）"
    return (
        f"当前场景旁白（narration）：\n{narration}\n\n"
        f"氛围（mood）：{mood}\n"
        f"阶段（phase）：{phase}\n"
        f"紧张度（tension）：{tension}\n"
        f"可用角色 id：{chars}\n"
        f"可用 goal id：{goals}\n"
        f"焦点目标：{focus}\n\n"
        f"近期记忆（memories）：\n{mem_block}\n\n"
        "请生成 2~4 个互有差分的玩家选项。"
        "只输出含 options 数组的 JSON；每个选项须有 text、stance、"
        "predicted_consequences（flag_changes / relationship_deltas / "
        "goal_effects / tension_delta / tags）与 narrative_preview。"
    )


def parse_choice_output(raw: str) -> list[ChoiceOption]:
    """
    Pure JSON parse helper: strip fences, parse options array → ChoiceOption list.

    Returns [] on empty / unparseable / invalid structure so the kernel can
    retry validation and fall back.
    """
    text = (raw or "").strip()
    if not text:
        return []

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    data: Any = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                # Also try a bare array of options
                a_start = text.find("[")
                a_end = text.rfind("]")
                if a_start >= 0 and a_end > a_start:
                    try:
                        data = json.loads(text[a_start : a_end + 1])
                    except json.JSONDecodeError:
                        return []
                else:
                    return []
        else:
            a_start = text.find("[")
            a_end = text.rfind("]")
            if a_start >= 0 and a_end > a_start:
                try:
                    data = json.loads(text[a_start : a_end + 1])
                except json.JSONDecodeError:
                    return []
            else:
                return []

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("options")
        if not isinstance(items, list):
            return []
    else:
        return []

    out: list[ChoiceOption] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            # Ensure predicted_consequences is a dict if present
            pc = item.get("predicted_consequences")
            if pc is None:
                item = {**item, "predicted_consequences": {}}
            elif not isinstance(pc, dict):
                continue
            opt = ChoiceOption.model_validate(item)
            if not (opt.text or "").strip():
                continue
            out.append(opt)
        except Exception:
            continue
    return out


class SdkChoice:
    """ChoicePort implementation via OpenAI Agents SDK."""

    def __init__(self, model: Optional[str] = None) -> None:
        from src.agents.director import _load_openai_agents_sdk

        Agent, _Runner = _load_openai_agents_sdk()
        kwargs: dict[str, Any] = {
            "name": "Choice",
            "instructions": _CHOICE_SYSTEM,
        }
        if model:
            kwargs["model"] = model
        self._agent = Agent(**kwargs)
        self._runner = _Runner

    async def generate_options(
        self,
        state: WorldState,
        pack: SettingPack,
        scene: SceneIntent,
        memories: list[str],
    ) -> list[ChoiceOption]:
        phase = state.phase.value if hasattr(state.phase, "value") else str(state.phase)
        prompt = build_choice_prompt(
            narration=scene.narration or "",
            mood=scene.mood or "neutral",
            phase=phase,
            tension=state.tension,
            character_ids=[c.id for c in pack.characters],
            goal_ids=[g.id for g in pack.goals],
            memories=list(memories or []),
            focus_goal_ids=list(scene.focus_goal_ids or []),
        )
        try:
            result = await self._runner.run(self._agent, input=prompt)
            output_text = getattr(result, "final_output", None) or str(result)
            return parse_choice_output(str(output_text))
        except Exception:
            # Empty list fails validation → kernel retries then fallback_options()
            return []
