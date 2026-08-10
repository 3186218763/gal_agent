from pathlib import Path

from src.content.setting_pack_loader import load_setting_pack
from src.domain.enums import GoalStatus
from src.domain.world_state import initial_world_state
from src.rules.ending_evaluator import evaluate_endings, _normalize

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_ending_priority_and_goal_completed():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    state.relationships["alice"].trust = 75
    state.goal_progress["ally_alice"].status = GoalStatus.COMPLETED
    state.goal_progress["ally_alice"].progress = 1.0
    ending = evaluate_endings(pack, state)
    assert ending is not None
    assert ending.id == "alice_route"


def test_fallback_on_max_steps():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    state.steps = 24
    ending = evaluate_endings(pack, state)
    assert ending is not None
    assert ending.id == "timeout_fallback"


def test_no_ending_when_conditions_unmet():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    assert evaluate_endings(pack, state) is None


def test_normalize_goals_and_logic_ops():
    assert "goals_ally_alice_completed" in _normalize("goals.ally_alice.completed")
    assert " and " in _normalize("a && b")
    assert " or " in _normalize("a || b")
