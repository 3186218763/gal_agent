from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .enums import EventType, Phase


class GameEvent(BaseModel):
    id: str
    step: int
    type: EventType
    payload: Dict[str, Any] = Field(default_factory=dict)
    phase: Optional[Phase] = None
    tension: Optional[int] = None
    tags: List[str] = Field(default_factory=list)


class EventDatabase(BaseModel):
    events: List[GameEvent] = Field(default_factory=list)

    def append(self, event: GameEvent) -> None:
        self.events.append(event)

    def list(self) -> List[GameEvent]:
        return list(self.events)

    def recent(self, n: int) -> List[GameEvent]:
        return self.events[-n:] if n > 0 else []
