from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .enums import GoalStatus, Phase
from .options import ChoiceOption
from .setting_pack import SettingPack


class RelationshipState(BaseModel):
    trust: int = 50
    romance: int = 0


class GoalRuntime(BaseModel):
    status: GoalStatus = GoalStatus.ACTIVE
    progress: float = 0.0
    evidence_event_ids: List[str] = Field(default_factory=list)


class WorldState(BaseModel):
    session_id: str
    pack_id: str
    steps: int = 0
    phase: Phase = Phase.SETUP
    tension: int = 5
    flags: Dict[str, Any] = Field(default_factory=dict)
    relationships: Dict[str, RelationshipState] = Field(default_factory=dict)
    goal_progress: Dict[str, GoalRuntime] = Field(default_factory=dict)
    turns_since_last_option: int = 0
    summary: str = ""
    pending_options: List[ChoiceOption] = Field(default_factory=list)
    ended: bool = False
    ending_id: Optional[str] = None


def initial_world_state(pack: SettingPack, session_id: str) -> WorldState:
    relationships = {
        c.id: RelationshipState(
            trust=c.initial_relationship.trust,
            romance=c.initial_relationship.romance,
        )
        for c in pack.characters
    }
    goal_progress = {
        g.id: GoalRuntime(status=GoalStatus.ACTIVE, progress=0.0)
        for g in pack.goals
    }
    return WorldState(
        session_id=session_id,
        pack_id=pack.pack_id,
        flags=dict(pack.initial_flags),
        relationships=relationships,
        goal_progress=goal_progress,
    )
