"""
选项数据模型
"""
from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class PredictedConsequences:
    """预期后果"""
    flag_changes: Dict[str, Any] = field(default_factory=dict)
    relationship_deltas: Dict[str, Dict[str, int]] = field(default_factory=dict)
    narrative_impact: str = ""


@dataclass
class GeneratedOption:
    """生成的选项"""
    text: str
    predicted_consequences: PredictedConsequences

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "text": self.text,
            "predicted_consequences": {
                "flag_changes": self.predicted_consequences.flag_changes,
                "relationship_deltas": self.predicted_consequences.relationship_deltas,
                "narrative_impact": self.predicted_consequences.narrative_impact
            }
        }
