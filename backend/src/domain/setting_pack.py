from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .enums import EndingType, GoalType


class RelationshipInit(BaseModel):
    trust: int = 50
    romance: int = 0


class CharacterDef(BaseModel):
    id: str
    name: str
    personality: str
    public_info: str = ""
    private_info: str = ""
    initial_relationship: RelationshipInit = Field(default_factory=RelationshipInit)


class LocationDef(BaseModel):
    id: str
    name: str
    tags: List[str] = Field(default_factory=list)


class FactionDef(BaseModel):
    id: str
    name: str
    description: str = ""


class WorldDef(BaseModel):
    locations: List[LocationDef] = Field(default_factory=list)
    factions: List[FactionDef] = Field(default_factory=list)


class GoalDef(BaseModel):
    id: str
    title: str
    description: str
    type: GoalType = GoalType.PURSUE
    weight: float = 1.0
    conflicts_with: List[str] = Field(default_factory=list)
    success_hint: str = ""
    suggests_flags: List[str] = Field(default_factory=list)


class EndingDef(BaseModel):
    id: str
    title: str
    condition: str
    type: EndingType = EndingType.BRANCH
    priority: int = 50
    content: str = ""


class SettingPack(BaseModel):
    pack_id: str
    title: str
    premise: str
    characters: List[CharacterDef]
    goals: List[GoalDef]
    endings: List[EndingDef]
    world: WorldDef = Field(default_factory=WorldDef)
    opening_seed: str = ""
    initial_flags: Dict[str, Any] = Field(default_factory=dict)
    max_steps: int = 24
