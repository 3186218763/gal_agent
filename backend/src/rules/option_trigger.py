# backend/src/rules/option_trigger.py
from __future__ import annotations
from typing import Any, Dict, List
from src.domain.enums import Phase


def should_trigger_option(
    *,
    turns_since_last_option: int,
    tension: int,
    phase: Phase,
    wants_option: bool,
    decision_pressure: bool,
    threshold: int = 50,
    min_cooldown: int = 2,
) -> Dict[str, Any]:
    reasons: List[str] = []
    if turns_since_last_option < min_cooldown:
        return {
            "should_trigger": False,
            "score": -999,
            "reasons": ["hard_cooldown"],
        }

    score = 0
    if turns_since_last_option <= 3:
        score += 0
    elif turns_since_last_option <= 5:
        score += 15
        reasons.append("turns_mid")
    else:
        score += 25
        reasons.append("turns_long")

    if tension >= 9:
        score += 35
    elif tension >= 7:
        score += 25
    elif tension >= 5:
        score += 10

    phase_pts = {
        Phase.SETUP: -10,
        Phase.RISING: 0,
        Phase.CLIMAX: 20,
        Phase.FALLING: 10,
    }[phase]
    score += phase_pts
    reasons.append(f"phase:{phase.value}:{phase_pts}")

    if wants_option:
        score += 15
        reasons.append("director_wants")
    if decision_pressure:
        score += 20
        reasons.append("decision_pressure")
    if turns_since_last_option >= 7:
        # turns_long (25) + drought_boost must clear default threshold 50 alone
        score += 25
        reasons.append("drought_boost")

    return {
        "should_trigger": score >= threshold,
        "score": score,
        "reasons": reasons,
    }
