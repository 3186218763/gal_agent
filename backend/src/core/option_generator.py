"""
选项生成器和验证器
"""
from typing import List, Dict, Any
import json
from ..models import GeneratedOption


def validate_options(options: List[GeneratedOption]) -> Dict[str, Any]:
    """
    验证生成的选项质量

    Args:
        options: 生成的选项列表

    Returns:
        {
            "valid": bool,
            "issues": List[str],
            "filtered_options": List[GeneratedOption]
        }
    """
    issues = []

    if len(options) < 2:
        issues.append("选项数量不足（至少需要 2 个）")
        return {
            "valid": False,
            "issues": issues,
            "filtered_options": options
        }

    # 检查 1: 后果区分度
    consequence_fingerprints = []
    for opt in options:
        fingerprint = json.dumps({
            "flags": opt.predicted_consequences.flag_changes,
            "relationships": opt.predicted_consequences.relationship_deltas
        }, sort_keys=True)
        consequence_fingerprints.append(fingerprint)

    unique_consequences = len(set(consequence_fingerprints))

    if unique_consequences < len(options) * 0.75:
        issues.append(f"选项后果重复度过高（{len(options) - unique_consequences} 个重复）")

    # 检查 2: 至少有实质影响
    has_impact = any(
        len(opt.predicted_consequences.flag_changes) > 0 or
        len(opt.predicted_consequences.relationship_deltas) > 0
        for opt in options
    )

    if not has_impact:
        issues.append("所有选项都没有实质影响")

    # 检查 3: 避免全极端（需要有中间选项）
    extreme_options = []
    moderate_options = []

    for opt in options:
        max_delta = 0
        if opt.predicted_consequences.relationship_deltas:
            for char_deltas in opt.predicted_consequences.relationship_deltas.values():
                for value in char_deltas.values():
                    max_delta = max(max_delta, abs(value))

        if max_delta > 15:
            extreme_options.append(opt)
        else:
            moderate_options.append(opt)

    if len(moderate_options) == 0 and len(options) > 2:
        issues.append("建议添加一个温和选项（所有选项都是极端的）")

    # 检查 4: 文本质量（长度合理）
    for i, opt in enumerate(options):
        if len(opt.text) < 2:
            issues.append(f"选项 {i+1} 文本过短")
        elif len(opt.text) > 50:
            issues.append(f"选项 {i+1} 文本过长（建议 50 字以内）")

    # 按区分度排序，保留最好的选项
    scored_options = []
    for opt in options:
        score = calculate_distinctiveness(opt, options)
        scored_options.append((score, opt))

    scored_options.sort(reverse=True, key=lambda x: x[0])
    filtered = [opt for _, opt in scored_options[:4]]  # 最多保留 4 个

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "filtered_options": filtered
    }


def calculate_distinctiveness(
    option: GeneratedOption,
    all_options: List[GeneratedOption]
) -> float:
    """
    计算选项的区分度分数

    Args:
        option: 当前选项
        all_options: 所有选项

    Returns:
        区分度分数 (0-10)
    """
    score = 5.0  # 基础分

    # 1. 有实质影响 (+2)
    if option.predicted_consequences.flag_changes or \
       option.predicted_consequences.relationship_deltas:
        score += 2

    # 2. 关系变化幅度适中 (+1)
    if option.predicted_consequences.relationship_deltas:
        max_delta = max(
            abs(value)
            for deltas in option.predicted_consequences.relationship_deltas.values()
            for value in deltas.values()
        )
        if 5 <= max_delta <= 20:
            score += 1

    # 3. 有叙事影响描述 (+1)
    if option.predicted_consequences.narrative_impact:
        score += 1

    # 4. 与其他选项的差异度
    differences = 0
    for other in all_options:
        if other is option:
            continue

        # 比较 flags
        if option.predicted_consequences.flag_changes != \
           other.predicted_consequences.flag_changes:
            differences += 1

        # 比较关系变化方向
        if option.predicted_consequences.relationship_deltas != \
           other.predicted_consequences.relationship_deltas:
            differences += 1

    if differences >= len(all_options) - 1:
        score += 1

    return min(10, score)
