from src.story.runtime.endings import next_phase, select_ending
from src.story.script_pack import compile_source
from src.story.state import StoryPhase, initial_session_state
from tests.story_factories import minimal_script_pack_dict


def _base():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "session_01", session_seed=42)
    return state, pack


def state_with_relationship(character_id: str, axis: str, value: int):
    state, pack = _base()
    relationships = {key: dict(axes) for key, axes in state.world.relationships.items()}
    relationships[character_id] = {**relationships[character_id], axis: value}
    world = state.world.model_copy(update={"relationships": relationships})
    return state.model_copy(update={"world": world}), pack


def state_at_scene_count(scene_count: int):
    state, pack = _base()
    world = state.world.model_copy(update={"scene_count": scene_count})
    return state.model_copy(update={"world": world}), pack


def test_normal_ending_waits_for_minimum_scene_count():
    state, pack = state_with_relationship("alice", "trust", 80)
    assert select_ending(pack, state) is None


def test_fallback_ending_is_selected_at_max_scene_count():
    state, pack = state_at_scene_count(20)
    ending = select_ending(pack, state)
    assert ending is not None
    assert ending.type == "fallback"


def test_next_phase_advances_one_step_at_most():
    state, _pack = state_at_scene_count(4)
    # usable = max(1, 20 - 3) = 17; ratio = (4+1)/17 ≈ 0.29 → EXPLORATION
    assert next_phase(state) == StoryPhase.EXPLORATION

    advanced = state.model_copy(
        update={"world": state.world.model_copy(update={"phase": StoryPhase.EXPLORATION})}
    )
    assert next_phase(advanced) is None
