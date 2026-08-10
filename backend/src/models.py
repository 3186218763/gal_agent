"""
游戏数据模型
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum


class EndingType(Enum):
    """结局类型"""
    VICTORY = "victory"
    BRANCH = "branch"
    GAME_OVER = "game_over"


@dataclass
class CharacterRelationship:
    """角色关系状态"""
    trust: int = 50  # 信任度 0-100
    romance: int = 0  # 好感度 0-100

    def to_dict(self) -> Dict[str, int]:
        return {
            "trust": self.trust,
            "romance": self.romance
        }

    @classmethod
    def from_dict(cls, data: Dict[str, int]) -> 'CharacterRelationship':
        return cls(
            trust=data.get("trust", 50),
            romance=data.get("romance", 0)
        )


@dataclass
class GameState:
    """游戏状态"""
    session_id: str
    current_chapter: str
    current_beat_index: int = 0
    tension_level: int = 5  # 紧张度 1-10

    # 角色关系 {character_id: CharacterRelationship}
    relationships: Dict[str, CharacterRelationship] = field(default_factory=dict)

    # 故事标记 {flag_name: value}
    flags: Dict[str, Any] = field(default_factory=dict)

    # 对话历史计数（用于选项触发）
    dialogue_count_since_last_option: int = 0

    # 最后选项时间戳
    last_option_timestamp: Optional[float] = None

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "current_chapter": self.current_chapter,
            "current_beat_index": self.current_beat_index,
            "tension_level": self.tension_level,
            "relationships": {
                char_id: rel.to_dict()
                for char_id, rel in self.relationships.items()
            },
            "flags": self.flags,
            "dialogue_count_since_last_option": self.dialogue_count_since_last_option,
            "last_option_timestamp": self.last_option_timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'GameState':
        relationships = {
            char_id: CharacterRelationship.from_dict(rel_data)
            for char_id, rel_data in data.get("relationships", {}).items()
        }

        return cls(
            session_id=data["session_id"],
            current_chapter=data["current_chapter"],
            current_beat_index=data.get("current_beat_index", 0),
            tension_level=data.get("tension_level", 5),
            relationships=relationships,
            flags=data.get("flags", {}),
            dialogue_count_since_last_option=data.get("dialogue_count_since_last_option", 0),
            last_option_timestamp=data.get("last_option_timestamp")
        )


@dataclass
class CharacterMetadata:
    """角色元数据"""
    id: str
    name: str
    personality: str
    initial_trust: int = 50
    initial_romance: int = 0


@dataclass
class EndingCondition:
    """结局条件"""
    id: str
    condition: str  # 条件表达式字符串
    type: EndingType
    priority: int
    title: str
    content: str


@dataclass
class ChapterMetadata:
    """章节元数据"""
    chapter_id: str
    title: str
    characters: List[CharacterMetadata]
    endings: List[EndingCondition]


@dataclass
class NarrativeBeat:
    """剧情节拍"""
    title: str
    content: str
    mood: Optional[str] = None
    has_option_point: bool = False
    option_point_weight: str = "medium"  # low, medium, high
    flags_to_set: Dict[str, Any] = field(default_factory=dict)
    character_interactions: List[str] = field(default_factory=list)
    beat_type: str = "narration"  # narration, dialogue, action
