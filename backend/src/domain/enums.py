# backend/src/domain/enums.py
from enum import Enum


class Phase(str, Enum):
    SETUP = "setup"
    RISING = "rising"
    CLIMAX = "climax"
    FALLING = "falling"


class EndingType(str, Enum):
    VICTORY = "victory"
    BRANCH = "branch"
    GAME_OVER = "game_over"
    FALLBACK = "fallback"


class GoalStatus(str, Enum):
    LOCKED = "locked"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class GoalType(str, Enum):
    PURSUE = "pursue"
    AVOID = "avoid"
    DISCOVER = "discover"


class EventType(str, Enum):
    NARRATION = "narration"
    DIALOGUE = "dialogue"
    PLAYER_CHOICE = "player_choice"
    SYSTEM = "system"
