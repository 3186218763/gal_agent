from src.domain.enums import Phase
from src.rules.option_trigger import should_trigger_option


def test_hard_cooldown_blocks():
    r = should_trigger_option(
        turns_since_last_option=1,
        tension=10,
        phase=Phase.CLIMAX,
        wants_option=True,
        decision_pressure=True,
    )
    assert r["should_trigger"] is False


def test_climax_high_tension_triggers():
    r = should_trigger_option(
        turns_since_last_option=3,
        tension=9,
        phase=Phase.CLIMAX,
        wants_option=True,
        decision_pressure=True,
    )
    assert r["should_trigger"] is True


def test_setup_low_tension_usually_no():
    r = should_trigger_option(
        turns_since_last_option=3,
        tension=3,
        phase=Phase.SETUP,
        wants_option=False,
        decision_pressure=False,
    )
    assert r["should_trigger"] is False


def test_long_drought_boost():
    r = should_trigger_option(
        turns_since_last_option=8,
        tension=4,
        phase=Phase.RISING,
        wants_option=False,
        decision_pressure=False,
    )
    assert r["score"] >= 50 or r["should_trigger"] is True
