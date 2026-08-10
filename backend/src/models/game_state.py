"""
数据模型定义
"""
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from enum import Enum


class BeatType(str, Enum):
    """Beat 类型"""
    NARRATION = "narration"
    DIALOGUE = "dialogue"
    ACTION = "action"


class EndingType(str, Enum):
    """结局类型"""
    VICTORY = "victory"
    GAME_OVER = "game_over"
    BRANCH = "branch"


@dataclass
class NarrativeBeat:
    """剧情节拍"""
    title: str
    content: str
    type: BeatType = BeatType.NARRATION
    mood: Optional[str] = None
    has_option_point: bool = False
    flags_to_set: Dict[str, Any] = field(default_factory=dict)
    character_interactions: List[str] = field(default_factory=list)


@dataclass
class Relationship:
    """角色关系"""
    trust_level: int = 50  # 0-100
    romance_level: int = 0  # 0-100
    memory_log: List[str] = field(default_factory=list)


@dataclass
class GameState:
    """游戏状态"""
    session_id: str
    current_chapter: str
    current_beat_index: int = 0
    flags: Dict[str, Any] = field(default_factory=dict)
    relationships: Dict[str, Relationship] = field(default_factory=dict)
    turns_since_last_option: int = 0
    tension_level: int = 5  # 1-10

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "session_id": self.session_id,
            "current_chapter": self.current_chapter,
            "current_beat_index": self.current_beat_index,
            "flags": self.flags,
            "relationships": {
                char_id: {
                    "trust_level": rel.trust_level,
                    "romance_level": rel.romance_level,
                    "memory_log": rel.memory_log
                }
                for char_id, rel in self.relationships.items()
            },
            "turns_since_last_option": self.turns_since_last_option,
            "tension_level": self.tension_level
        }


@dataclass
class EndingCondition:
    """结局条件"""
    id: str
    condition: str  # 条件表达式，如 "alice_trust >= 80 && revealed_secret"
    type: EndingType
    priority: int = 50
    title: str = ""
    content: str = ""


@dataclass
class Character:
    """角色定义"""
    id: str
    name: str
    personality: str
    initial_trust: int = 50
    initial_romance: int = 0


@dataclass
class ChapterMetadata:
    """章节元数据"""
    chapter_id: str
    title: str
    characters: List[Character]
    endings: List[EndingCondition]
    key_decision_points: List[Dict[str, Any]] = field(default_factory=list)
