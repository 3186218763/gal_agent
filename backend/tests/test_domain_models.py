# backend/tests/test_domain_models.py
from src.domain.enums import Phase, EndingType, GoalStatus, GoalType, EventType


def test_phase_order():
    assert [p.value for p in Phase] == ["setup", "rising", "climax", "falling"]


def test_ending_types_include_fallback():
    assert EndingType.FALLBACK.value == "fallback"
    assert EndingType.VICTORY.value == "victory"
