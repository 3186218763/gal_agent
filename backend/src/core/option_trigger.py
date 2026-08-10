"""
选项触发算法 - 判断何时应该给玩家选择机会
"""
from typing import Dict, Any, List
from ..models import NarrativeBeat


def should_trigger_option(
    current_beat: NarrativeBeat,
    turns_since_last_option: int,
    tension_level: int,
    recent_state_changes: int
) -> Dict[str, Any]:
    """
    判断是否应该触发选项

    Args:
        current_beat: 当前剧情节拍
        turns_since_last_option: 距离上次选项的轮次数
        tension_level: 当前紧张度 (1-10)
        recent_state_changes: 最近的状态变化次数（flags + 关系变化）

    Returns:
        {
            "should_trigger": bool,
            "score": int,
            "reasons": List[str],
            "confidence": int
        }
    """
    score = 0
    reasons = []

    # 1. 剧本标记的选择点 (+40 分)
    if current_beat.has_option_point:
        score += 40
        reasons.append("剧本标记的选择点")

    # 2. 对话轮次累积 (6 轮后开始考虑，每轮 +5 分)
    if turns_since_last_option >= 6:
        added = (turns_since_last_option - 5) * 5
        score += added
        reasons.append(f"已经 {turns_since_last_option} 轮未给选项 (+{added})")

    # 3. 紧张度高 (7+ 时 +15 分)
    if tension_level >= 7:
        score += 15
        reasons.append(f"紧张度较高 ({tension_level}/10)")

    # 4. 最近状态变化多 (2+ 次 +10 分)
    if recent_state_changes >= 2:
        score += 10
        reasons.append(f"最近状态变化频繁 ({recent_state_changes} 次)")

    # 5. 冷却期惩罚 (3 轮内 -30 分)
    if turns_since_last_option < 3:
        score -= 30
        reasons.append("刚做过选择，冷却中 (-30)")

    # 6. Beat 类型奖励（对话类型更适合选项）
    if current_beat.type.value == "dialogue":
        score += 5
        reasons.append("对话场景，适合给选项 (+5)")

    # 7. 有角色交互 (+5 分)
    if current_beat.character_interactions:
        score += 5
        reasons.append("存在角色交互 (+5)")

    # 阈值：50 分触发
    should_trigger = score >= 50
    confidence = min(100, max(0, score))

    return {
        "should_trigger": should_trigger,
        "score": score,
        "reasons": reasons,
        "confidence": confidence
    }


def calculate_tension(
    recent_events: Dict[str, Any]
) -> int:
    """
    计算当前紧张度

    Args:
        recent_events: 最近的事件数据
            - dialogue_exchanges: 对话轮次
            - flags_changed: 改变的 flags 列表
            - relationship_deltas: 关系变化字典

    Returns:
        紧张度 (1-10)
    """
    tension = 5  # 基线

    dialogue_exchanges = recent_events.get('dialogue_exchanges', 0)
    flags_changed = recent_events.get('flags_changed', [])
    relationship_deltas = recent_events.get('relationship_deltas', {})

    # 长时间对话降低紧张感
    if dialogue_exchanges > 6:
        tension -= 1

    # 关键 flags 变化提升紧张感
    if len(flags_changed) > 2:
        tension += 2

    # 关系值大幅波动提升紧张感
    if relationship_deltas:
        max_delta = max(
            abs(delta)
            for deltas in relationship_deltas.values()
            for delta in deltas.values()
        )
        if max_delta > 15:
            tension += 3
        elif max_delta > 10:
            tension += 2

    return max(1, min(10, tension))


def count_recent_state_changes(
    game_state,
    lookback_beats: int = 3
) -> int:
    """
    计算最近的状态变化次数

    Args:
        game_state: 游戏状态
        lookback_beats: 回溯的 beat 数量

    Returns:
        变化次数
    """
    # 简化实现：这里假设我们有一个事件日志
    # 实际实现中应该追踪最近 N 个 beats 的 flag 和关系变化
    # 这里先返回一个占位值
    # TODO: 实现完整的事件日志追踪
    return 0
