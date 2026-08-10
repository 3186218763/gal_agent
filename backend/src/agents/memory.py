"""
Memory Agent (V1) — rule-based recall from EventDatabase.

No LLM required: last-K event summaries + optional state.summary.
"""
from __future__ import annotations

from src.domain.enums import EventType
from src.domain.events import EventDatabase, GameEvent
from src.domain.setting_pack import SettingPack
from src.domain.world_state import WorldState


def _format_event(ev: GameEvent) -> str:
    """Readable one-line summary of a game event for agent memory context."""
    payload = ev.payload or {}
    et = ev.type

    if et == EventType.NARRATION:
        text = (
            payload.get("content")
            or payload.get("narration")
            or payload.get("summary")
            or payload.get("text")
            or ""
        )
        return f"[step {ev.step}] narration: {text}".rstrip()

    if et == EventType.DIALOGUE:
        char = (
            payload.get("character")
            or payload.get("character_id")
            or "unknown"
        )
        text = (
            payload.get("content")
            or payload.get("dialogue")
            or payload.get("text")
            or payload.get("summary")
            or ""
        )
        return f"[step {ev.step}] {char}: {text}".rstrip()

    if et == EventType.PLAYER_CHOICE:
        text = (
            payload.get("content")
            or payload.get("summary")
            or payload.get("text")
            or payload.get("option_text")
            or ""
        )
        return f"[step {ev.step}] player choice: {text}".rstrip()

    # SYSTEM / fallback
    text = (
        payload.get("content")
        or payload.get("summary")
        or payload.get("text")
        or ""
    )
    if text:
        return f"[step {ev.step}] {et.value}: {text}"
    return f"[step {ev.step}] {et.value}"


class RuleMemory:
    """MemoryPort: last-k event summaries; prepend state.summary when set."""

    async def recall(
        self,
        state: WorldState,
        pack: SettingPack,
        events: EventDatabase,
        k: int = 5,
    ) -> list[str]:
        out: list[str] = []
        if state.summary:
            out.append(f"summary: {state.summary}")
        for ev in events.recent(k):
            out.append(_format_event(ev))
        return out
