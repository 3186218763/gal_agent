"""
Director Agent (SDK) — produces SceneIntent for the GameKernel.

openai-agents is optional at import time; missing package only errors when
SdkDirector is constructed / used. SDK is imported lazily to avoid clashing
with this local package name (`agents`).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional, Union

from src.domain.enums import Phase
from src.domain.scene import SceneIntent
from src.domain.setting_pack import SettingPack
from src.domain.world_state import WorldState

logger = logging.getLogger(__name__)

_DIRECTOR_SYSTEM = """你是一个 Galgame 游戏导演（Director）。

职责：
1. 在给定世界设定内发明合理场景与旁白（narration）
2. 服务当前焦点目标（focus goals），推动剧情
3. 指定说话角色与简短对话指令（dialogue_directives），不要替角色写完整长对话
4. **不要** 编写玩家选项（options）——选项由 Choice 模块负责
5. 只输出 **一个 JSON 对象**，字段与 SceneIntent 对齐，不要 Markdown 代码围栏或解释文字

JSON 字段：
- narration: string（场景旁白，必填）
- mood: string（如 calm / neutral / tense）
- location_id: string | null
- speaking_character_ids: string[]
- dialogue_directives: object（char_id -> 简短指令）
- focus_goal_ids: string[]
- suggested_tension_delta: int，必须在 [-2, 2]
- wants_option: bool
- decision_pressure: bool
- event_tags: string[]
- phase_hint: string | null

规则：suggested_tension_delta 不得超出 [-2, 2]；不要输出玩家可点击选项文本。
"""


def build_director_prompt(
    premise: str,
    phase: Union[Phase, str],
    tension: int,
    goals_summary: str,
    memories: list[str],
    opening_seed: str,
    steps: int,
) -> str:
    """Pure prompt builder for unit tests (no network)."""
    phase_str = phase.value if isinstance(phase, Phase) else str(phase)
    mem_block = "\n".join(f"- {m}" for m in memories) if memories else "- （无）"
    return (
        f"世界前提（premise）：\n{premise}\n\n"
        f"开场种子（opening_seed，steps==0 时可参考）：\n{opening_seed}\n\n"
        f"当前阶段（phase）：{phase_str}\n"
        f"紧张度（tension）：{tension}\n"
        f"步数（steps）：{steps}\n"
        f"目标进度（goals）：{goals_summary}\n\n"
        f"近期记忆（memories）：\n{mem_block}\n\n"
        "请发明下一场景。在世界内推进剧情，服务焦点目标；"
        "不要写玩家选项；只输出 SceneIntent 对应的 JSON。"
        "suggested_tension_delta 必须在 [-2, 2]。"
    )


def _goals_summary(state: WorldState) -> str:
    if not state.goal_progress:
        return "(none)"
    parts = []
    for gid, gr in state.goal_progress.items():
        parts.append(f"{gid}:{gr.progress}")
    return ", ".join(parts)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse JSON object from model output; tolerate fenced markdown."""
    raw = (text or "").strip()
    if not raw:
        raise json.JSONDecodeError("empty", raw, 0)

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    raise json.JSONDecodeError("no json object", raw, 0)


def _clamp_tension_delta(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(-2, min(2, n))


def _safe_scene_intent(opening_seed: str, premise: str) -> SceneIntent:
    """Fallback SceneIntent when model output cannot be parsed."""
    narration = (opening_seed or "").strip() or (premise or "").strip() or "故事继续。"
    if len(narration) > 240:
        narration = narration[:240].rstrip() + "…"
    return SceneIntent(
        narration=narration,
        mood="neutral",
        location_id=None,
        speaking_character_ids=[],
        dialogue_directives={},
        focus_goal_ids=[],
        suggested_tension_delta=0,
        wants_option=False,
        decision_pressure=False,
        event_tags=["fallback"],
        phase_hint=None,
    )


def _scene_from_dict(data: dict[str, Any], opening_seed: str, premise: str) -> SceneIntent:
    if "suggested_tension_delta" in data:
        data = {
            **data,
            "suggested_tension_delta": _clamp_tension_delta(data["suggested_tension_delta"]),
        }
    if not data.get("narration"):
        return _safe_scene_intent(opening_seed, premise)
    try:
        return SceneIntent.model_validate(data)
    except Exception:
        return _safe_scene_intent(opening_seed, premise)


def _is_local_agents_package(mod: Any) -> bool:
    """True if `mod` is this repo's agents package, not openai-agents."""
    f = (getattr(mod, "__file__", None) or "").replace("\\", "/")
    if not f:
        return False
    # Local package lives under backend/src/agents/
    return f.endswith("/src/agents/__init__.py") or "/backend/src/agents/" in f


def _load_openai_agents_sdk() -> tuple[Any, Any]:
    """
    Import Agent and Runner from openai-agents.

    This repo's local package is also named `agents` and often sits earlier on
    sys.path (via src layout). Resolve the real SDK from site-packages when
    the top-level name is shadowed.
    """
    existing = sys.modules.get("agents")
    if existing is not None and not _is_local_agents_package(existing):
        if hasattr(existing, "Agent") and hasattr(existing, "Runner"):
            return existing.Agent, existing.Runner

    # Prefer a site-packages copy of agents that exposes Agent/Runner.
    for entry in list(sys.path):
        if not entry or entry == ".":
            continue
        init_path = Path(entry) / "agents" / "__init__.py"
        try:
            resolved = str(init_path.resolve())
        except OSError:
            continue
        if not init_path.is_file():
            continue
        if resolved.endswith("/src/agents/__init__.py") or "/backend/src/agents/" in resolved.replace(
            "\\", "/"
        ):
            continue
        # Load under a private name first to inspect, then rebind as needed.
        try:
            spec = importlib.util.spec_from_file_location(
                "agents",
                init_path,
                submodule_search_locations=[str(init_path.parent)],
            )
            if spec is None or spec.loader is None:
                continue
            # If local package occupies sys.modules['agents'], park it.
            parked: dict[str, Any] = {}
            for key in list(sys.modules):
                if key == "agents" or key.startswith("agents."):
                    mod = sys.modules[key]
                    if _is_local_agents_package(mod) or (
                        getattr(mod, "__file__", None)
                        and "/src/agents/" in str(mod.__file__).replace("\\", "/")
                    ):
                        parked[key] = sys.modules.pop(key)
            try:
                sdk = importlib.util.module_from_spec(spec)
                sys.modules["agents"] = sdk
                spec.loader.exec_module(sdk)
                if hasattr(sdk, "Agent") and hasattr(sdk, "Runner"):
                    return sdk.Agent, sdk.Runner
            finally:
                # If SDK load failed, restore parked local modules.
                if "agents" in sys.modules and not hasattr(sys.modules["agents"], "Agent"):
                    for key, mod in parked.items():
                        sys.modules[key] = mod
        except Exception:
            continue

    # Last resort: plain import (works when openai-agents is installed and
    # not shadowed).
    try:
        # Temporarily drop backend/src from path so `agents` is not local.
        src_entries = [
            p
            for p in sys.path
            if p and Path(p).resolve().name == "src" and (Path(p) / "agents").is_dir()
        ]
        removed = []
        for p in src_entries:
            sys.path.remove(p)
            removed.append(p)
        # Clear shadowed local package if present.
        parked = {}
        for key in list(sys.modules):
            if key == "agents" or key.startswith("agents."):
                mod = sys.modules[key]
                if _is_local_agents_package(mod) or (
                    getattr(mod, "__file__", None)
                    and "/src/agents/" in str(mod.__file__).replace("\\", "/")
                ):
                    parked[key] = sys.modules.pop(key)
        try:
            sdk = importlib.import_module("agents")
            if hasattr(sdk, "Agent") and hasattr(sdk, "Runner"):
                return sdk.Agent, sdk.Runner
        finally:
            for p in reversed(removed):
                if p not in sys.path:
                    sys.path.insert(0, p)
            if not (hasattr(sys.modules.get("agents"), "Agent")):
                for key, mod in parked.items():
                    sys.modules[key] = mod
    except Exception:
        pass

    raise ImportError(
        "openai-agents package is required for SdkDirector. "
        "Install with: pip install openai-agents  "
        "Or set GAL_USE_STUBS=1 to use StubDirector."
    )


class SdkDirector:
    """DirectorPort implementation via OpenAI Agents SDK."""

    def __init__(self, model: Optional[str] = None) -> None:
        Agent, _Runner = _load_openai_agents_sdk()
        kwargs: dict[str, Any] = {
            "name": "Director",
            "instructions": _DIRECTOR_SYSTEM,
        }
        if model:
            kwargs["model"] = model
        self._agent = Agent(**kwargs)
        self._runner = _Runner

    async def generate_scene(
        self,
        state: WorldState,
        pack: SettingPack,
        memories: list[str],
    ) -> SceneIntent:
        prompt = build_director_prompt(
            premise=pack.premise or "",
            phase=state.phase,
            tension=state.tension,
            goals_summary=_goals_summary(state),
            memories=list(memories or []),
            opening_seed=pack.opening_seed or "",
            steps=state.steps,
        )
        try:
            result = await self._runner.run(self._agent, input=prompt)
            output_text = getattr(result, "final_output", None) or str(result)
            data = _extract_json_object(str(output_text))
            return _scene_from_dict(data, pack.opening_seed or "", pack.premise or "")
        except Exception:
            logger.warning("SdkDirector.generate_scene failed; using fallback", exc_info=True)
            return _safe_scene_intent(pack.opening_seed or "", pack.premise or "")
