from pathlib import Path

from src.content.setting_pack_loader import load_setting_pack
from src.domain.enums import GoalStatus
from src.domain.options import GoalEffect, PredictedConsequences
from src.domain.world_state import initial_world_state
from src.rules.goal_tracker import apply_consequences, apply_goal_effects

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_apply_relationship_and_goal_progress():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    new_state = apply_consequences(
        state,
        pack,
        PredictedConsequences(
            flag_changes={"met_alice": True},
            relationship_deltas={"alice": {"trust": 20}},
            goal_effects=[GoalEffect(goal_id="ally_alice", delta_progress=0.5)],
        ),
    )
    assert new_state.flags["met_alice"] is True
    assert new_state.relationships["alice"].trust == 70
    assert new_state.goal_progress["ally_alice"].progress == 0.5
    # original state unchanged
    assert "met_alice" not in state.flags
    assert state.relationships["alice"].trust == 50


def test_force_complete_sets_completed():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    new_state = apply_goal_effects(
        state,
        pack,
        [GoalEffect(goal_id="ally_alice", delta_progress=0.1, force_complete=True)],
    )
    gr = new_state.goal_progress["ally_alice"]
    assert gr.status == GoalStatus.COMPLETED
    assert gr.progress == 1.0
    # conflicting goals remain active (director handles weight demotion)
    assert new_state.goal_progress["ally_bob"].status == GoalStatus.ACTIVE


def test_progress_caps_and_completes_at_one():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    mid = apply_goal_effects(
        state, pack, [GoalEffect(goal_id="ally_alice", delta_progress=0.6)]
    )
    assert mid.goal_progress["ally_alice"].progress == 0.6
    assert mid.goal_progress["ally_alice"].status == GoalStatus.ACTIVE

    done = apply_goal_effects(
        mid, pack, [GoalEffect(goal_id="ally_alice", delta_progress=0.5)]
    )
    assert done.goal_progress["ally_alice"].progress == 1.0
    assert done.goal_progress["ally_alice"].status == GoalStatus.COMPLETED


def test_relationship_clamped_0_100():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    high = apply_consequences(
        state,
        pack,
        PredictedConsequences(relationship_deltas={"alice": {"trust": 100, "romance": 200}}),
    )
    assert high.relationships["alice"].trust == 100
    assert high.relationships["alice"].romance == 100

    low = apply_consequences(
        state,
        pack,
        PredictedConsequences(relationship_deltas={"alice": {"trust": -200}}),
    )
    assert low.relationships["alice"].trust == 0
