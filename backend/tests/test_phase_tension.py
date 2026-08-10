# backend/tests/test_phase_tension.py
from src.domain.enums import Phase
from src.rules.phase_tension import update_tension, maybe_advance_phase, clamp_phase_hint
from src.domain.world_state import WorldState, GoalRuntime
from src.domain.enums import GoalStatus


def test_tension_clamped_and_tags():
    assert update_tension(5, 2, ["confrontation"], Phase.RISING) >= 7
    assert update_tension(2, -5, ["calm"], Phase.SETUP) == 1
    assert update_tension(9, 5, [], Phase.CLIMAX) == 10


def test_phase_hint_max_one_step():
    assert clamp_phase_hint(Phase.SETUP, "climax") == Phase.RISING
    assert clamp_phase_hint(Phase.RISING, "setup") == Phase.RISING  # no backward for V1


def test_advance_setup_to_rising_by_steps():
    state = WorldState(session_id="s", pack_id="t", steps=3, phase=Phase.SETUP)
    assert maybe_advance_phase(state, pack=None) == Phase.RISING
