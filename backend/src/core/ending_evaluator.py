"""
结局评估器 - 检查结局条件
"""
from typing import Dict, Any, Optional, List
import re
from ..models import GameState, EndingCondition


class EndingEvaluator:
    """结局条件评估器"""

    def evaluate_endings(
        self,
        endings: List[EndingCondition],
        game_state: GameState
    ) -> Dict[str, Any]:
        """
        评估结局条件

        Args:
            endings: 结局条件列表
            game_state: 当前游戏状态

        Returns:
            {
                "triggered": bool,
                "ending_id": str | None,
                "ending": EndingCondition | None
            }
        """
        # 按优先级排序
        sorted_endings = sorted(endings, key=lambda e: e.priority, reverse=True)

        # 构建求值上下文
        context = self._build_context(game_state)

        # 逐个检查条件
        for ending in sorted_endings:
            try:
                if self._evaluate_condition(ending.condition, context):
                    return {
                        "triggered": True,
                        "ending_id": ending.id,
                        "ending": ending
                    }
            except Exception as e:
                print(f"Error evaluating ending {ending.id}: {e}")
                continue

        return {
            "triggered": False,
            "ending_id": None,
            "ending": None
        }

    def _build_context(self, game_state: GameState) -> Dict[str, Any]:
        """构建求值上下文"""
        context = {}

        # 添加所有 flags
        context.update(game_state.flags)

        # 添加关系值变量
        for char_id, rel in game_state.relationships.items():
            context[f"{char_id}_trust"] = rel.trust_level
            context[f"{char_id}_romance"] = rel.romance_level

        return context

    def _evaluate_condition(self, condition: str, context: Dict[str, Any]) -> bool:
        """
        安全地评估条件表达式

        支持的语法：
        - 比较运算：>=, <=, >, <, ==, !=
        - 逻辑运算：&&, ||
        - 变量：flags 和 关系值
        - 布尔值：true, false

        Args:
            condition: 条件表达式字符串
            context: 变量上下文

        Returns:
            条件是否满足
        """
        # 替换逻辑运算符为 Python 语法
        condition = condition.replace('&&', ' and ')
        condition = condition.replace('||', ' or ')
        condition = condition.replace('true', 'True')
        condition = condition.replace('false', 'False')

        # 提取所有变量名
        variable_pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b'
        variables = re.findall(variable_pattern, condition)

        # 创建安全的局部命名空间
        safe_namespace = {}
        for var in variables:
            if var in context:
                safe_namespace[var] = context[var]
            elif var not in ['True', 'False', 'and', 'or', 'not']:
                # 未定义的变量默认为 False
                safe_namespace[var] = False

        try:
            # 使用 eval 在受限命名空间中求值
            result = eval(condition, {"__builtins__": {}}, safe_namespace)
            return bool(result)
        except Exception as e:
            print(f"Error evaluating condition '{condition}': {e}")
            return False

    def check_should_evaluate_ending(
        self,
        game_state: GameState,
        beat_index: int,
        total_beats: int
    ) -> bool:
        """
        判断是否应该检查结局

        检查时机：
        1. 章节结束时
        2. 关系值达到极值（>= 90 或 <= 10）
        3. 特定关键 flags 被设置

        Args:
            game_state: 游戏状态
            beat_index: 当前 beat 索引
            total_beats: 总 beat 数

        Returns:
            是否应该检查结局
        """
        # 1. 章节结束
        if beat_index >= total_beats - 1:
            return True

        # 2. 关系值极值
        for rel in game_state.relationships.values():
            if rel.trust_level >= 90 or rel.trust_level <= 10:
                return True
            if rel.romance_level >= 90 or rel.romance_level <= 10:
                return True

        # 3. 关键 flags（这里可以配置一些触发结局检查的关键 flags）
        critical_flags = ['betrayed', 'revealed_secret', 'critical_choice_made']
        for flag in critical_flags:
            if game_state.flags.get(flag):
                return True

        return False
