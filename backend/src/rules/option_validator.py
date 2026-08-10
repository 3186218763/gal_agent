# backend/src/rules/option_validator.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Set

from src.domain.options import ChoiceOption, PredictedConsequences


@dataclass
class ValidationResult:
    valid: bool
    issues: List[str] = field(default_factory=list)
    options: List[ChoiceOption] = field(default_factory=list)


def consequence_fingerprint(opt: ChoiceOption) -> str:
    """Stable fingerprint of predicted consequences for 差分 / 假选择 detection.

    Includes goal effect ``delta_progress`` and ``force_complete`` so two options
    that touch the same goal with different progress are not treated as 假选择.
    """
    c = opt.predicted_consequences
    goal_effects = sorted(
        (
            {
                "goal_id": ge.goal_id,
                "delta_progress": ge.delta_progress,
                "force_complete": ge.force_complete,
            }
            for ge in c.goal_effects
        ),
        key=lambda g: (g["goal_id"], g["delta_progress"], g["force_complete"]),
    )
    payload: Dict[str, Any] = {
        "flag_changes": c.flag_changes,
        "relationship_deltas": c.relationship_deltas,
        "goal_effects": goal_effects,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _has_nonempty_consequences(opt: ChoiceOption) -> bool:
    c = opt.predicted_consequences
    return bool(c.flag_changes or c.relationship_deltas or c.goal_effects)


def validate_options(
    options: list[ChoiceOption],
    *,
    valid_character_ids: Iterable[str],
    valid_goal_ids: Iterable[str],
    recent_choice_tags: list[str] | None = None,
) -> ValidationResult:
    """
    Validate generated choice options.

    Rules:
    - count n in 2..4
    - each option has non-empty consequences (flag / rel / goal)
    - fingerprints unique (no 假选择)
    - text length 2..50
    - relationship character ids ⊆ valid set; goal ids ⊆ valid set
    - assign id as opt_0, opt_1, ...
    - options whose tags fully match recent_choice_tags are discarded
    """
    issues: List[str] = []
    char_ids: Set[str] = set(valid_character_ids)
    goal_ids: Set[str] = set(valid_goal_ids)
    recent = set(recent_choice_tags or [])

    kept: List[ChoiceOption] = []
    seen_fps: Set[str] = set()

    for i, opt in enumerate(options):
        text = (opt.text or "").strip()
        text_len = len(text)

        if not (2 <= text_len <= 50):
            issues.append(f"文案长度须在 2..50，当前 {text_len}: {text!r}")

        if not _has_nonempty_consequences(opt):
            issues.append(f"后果为空（须改 flag / 关系 / goal 之一）: {text!r}")

        for cid in opt.predicted_consequences.relationship_deltas:
            if cid not in char_ids:
                issues.append(f"非法角色 id: {cid}")

        for ge in opt.predicted_consequences.goal_effects:
            if ge.goal_id not in goal_ids:
                issues.append(f"非法 goal id: {ge.goal_id}")

        tags = set(opt.predicted_consequences.tags or [])
        if recent and tags and tags == recent:
            issues.append(f"与近期 choice tags 完全同构，已丢弃: {sorted(tags)}")
            continue

        fp = consequence_fingerprint(opt)
        if fp in seen_fps:
            issues.append(f"假选择：后果 fingerprint 雷同，缺少差分: {fp}")
        else:
            seen_fps.add(fp)

        kept.append(opt.model_copy(update={"id": f"opt_{len(kept)}"}))

    n = len(kept)
    if not (2 <= n <= 4):
        issues.append(f"选项数量须在 2..4，当前 n={n}")

    # Re-check uniqueness only among kept (already done); valid iff no issues
    return ValidationResult(
        valid=len(issues) == 0,
        issues=issues,
        options=kept,
    )


def fallback_options() -> list[ChoiceOption]:
    """Hard-coded safe options when generation/validation fails after retries."""
    return [
        ChoiceOption(
            id="fb_0",
            text="继续追问细节",
            stance="bold",
            predicted_consequences=PredictedConsequences(
                flag_changes={"asked_more": True},
                tags=["ask"],
            ),
            narrative_preview="你想知道更多",
        ),
        ChoiceOption(
            id="fb_1",
            text="暂时观望",
            stance="cautious",
            predicted_consequences=PredictedConsequences(
                flag_changes={"watched": True},
                tension_delta=-1,
                tags=["wait"],
            ),
            narrative_preview="你先不表态",
        ),
        ChoiceOption(
            id="fb_2",
            text="转移话题",
            stance="withdraw",
            predicted_consequences=PredictedConsequences(
                flag_changes={"changed_subject": True},
                tags=["deflect"],
            ),
            narrative_preview="气氛稍缓",
        ),
    ]
