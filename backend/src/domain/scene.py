from __future__ import annotations
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class SceneIntent(BaseModel):
    """Director structured output."""
    narration: str
    mood: str = "neutral"
    location_id: Optional[str] = None
    speaking_character_ids: List[str] = Field(default_factory=list)
    dialogue_directives: Dict[str, str] = Field(default_factory=dict)  # char_id -> brief
    focus_goal_ids: List[str] = Field(default_factory=list)
    suggested_tension_delta: int = 0
    wants_option: bool = False
    decision_pressure: bool = False
    event_tags: List[str] = Field(default_factory=list)
    phase_hint: Optional[str] = None
