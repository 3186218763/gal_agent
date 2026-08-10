# backend/src/rules/ending_evaluator.py
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from src.domain.enums import GoalStatus
from src.domain.setting_pack import EndingDef, SettingPack
from src.domain.world_state import WorldState

_GOALS_COMPLETED_RE = re.compile(r"goals\.([a-zA-Z0-9_]+)\.completed")
_IDENT_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")
_KEYWORDS = frozenset({"True", "False", "and", "or", "not", "in", "is"})


def _build_context(state: WorldState) -> Dict[str, Any]:
    ctx: Dict[str, Any] = dict(state.flags)
    ctx["steps"] = state.steps
    ctx["phase"] = state.phase.value
    for cid, rel in state.relationships.items():
        ctx[f"{cid}_trust"] = rel.trust
        ctx[f"{cid}_romance"] = rel.romance
    for gid, gr in state.goal_progress.items():
        ctx[f"goals_{gid}_completed"] = gr.status == GoalStatus.COMPLETED
    return ctx


def _normalize(condition: str) -> str:
    c = condition.replace("&&", " and ").replace("||", " or ")
    c = re.sub(r"\btrue\b", "True", c)
    c = re.sub(r"\bfalse\b", "False", c)
    c = _GOALS_COMPLETED_RE.sub(r"goals_\1_completed", c)
    return c


def _evaluate_condition(condition: str, context: Dict[str, Any]) -> bool:
    normalized = _normalize(condition)
    names = _IDENT_RE.findall(normalized)
    safe_ns: Dict[str, Any] = {}
    for name in names:
        if name in _KEYWORDS:
            continue
        if name in context:
            safe_ns[name] = context[name]
        else:
            safe_ns[name] = False
    try:
        result = eval(normalized, {"__builtins__": {}}, safe_ns)
        return bool(result)
    except Exception:
        return False


def evaluate_endings(pack: SettingPack, state: WorldState) -> Optional[EndingDef]:
    """Return the highest-priority ending whose condition is true, or None."""
    context = _build_context(state)
    sorted_endings = sorted(pack.endings, key=lambda e: e.priority, reverse=True)
    for ending in sorted_endings:
        if _evaluate_condition(ending.condition, context):
            return ending
    return None
