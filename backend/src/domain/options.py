from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GoalEffect(BaseModel):
    goal_id: str
    delta_progress: float = 0.0
    force_complete: bool = False


class PredictedConsequences(BaseModel):
    flag_changes: Dict[str, Any] = Field(default_factory=dict)
    relationship_deltas: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    goal_effects: List[GoalEffect] = Field(default_factory=list)
    tension_delta: int = 0
    tags: List[str] = Field(default_factory=list)


class ChoiceOption(BaseModel):
    id: str = ""
    text: str
    stance: str = "neutral"
    player_intent: str = ""
    predicted_consequences: PredictedConsequences = Field(default_factory=PredictedConsequences)
    narrative_preview: str = ""
