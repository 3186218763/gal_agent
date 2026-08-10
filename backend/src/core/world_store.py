"""Persist WorldState + EventDatabase per session (JSON on disk)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

from src.domain.events import EventDatabase
from src.domain.setting_pack import SettingPack
from src.domain.world_state import WorldState, initial_world_state


class WorldStore:
    """JSON-backed session store for kernel WorldState + events."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Tuple[WorldState, EventDatabase]] = {}

    def _path(self, session_id: str) -> Path:
        return self.data_dir / f"{session_id}.json"

    def create_session(self, session_id: str, pack: SettingPack) -> WorldState:
        """Create initial WorldState from pack and persist with empty events."""
        state = initial_world_state(pack, session_id)
        events = EventDatabase()
        self.save(session_id, state, events)
        return state

    def save(
        self,
        session_id: str,
        state: WorldState,
        events: EventDatabase,
    ) -> None:
        """Write state + events to data/{session_id}.json and refresh cache."""
        payload = {
            "state": state.model_dump(mode="json"),
            "events": events.model_dump(mode="json"),
        }
        path = self._path(session_id)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._cache[session_id] = (state, events)

    def load(
        self, session_id: str
    ) -> Optional[Tuple[WorldState, EventDatabase]]:
        """Load from cache or disk. Returns None if session missing."""
        if session_id in self._cache:
            return self._cache[session_id]

        path = self._path(session_id)
        if not path.is_file():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        state = WorldState.model_validate(data["state"])
        events = EventDatabase.model_validate(data.get("events") or {"events": []})
        self._cache[session_id] = (state, events)
        return state, events

    def delete(self, session_id: str) -> bool:
        """Remove session from cache and disk. True if file existed or was cached."""
        existed = session_id in self._cache or self._path(session_id).is_file()
        self._cache.pop(session_id, None)
        path = self._path(session_id)
        if path.is_file():
            path.unlink()
        return existed

    def list_sessions(self) -> list[str]:
        """List session IDs present on disk."""
        return sorted(p.stem for p in self.data_dir.glob("*.json"))
