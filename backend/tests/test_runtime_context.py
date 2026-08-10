from src.story.runtime.context import build_condition_context
from src.story.script_pack import compile_source
from src.story.state import initial_session_state
from tests.story_factories import minimal_script_pack_dict


def compiled_state():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "session_01", session_seed=42)
    return state, pack


def test_condition_context_matches_compiled_condition_paths():
    state, pack = compiled_state()
    context = build_condition_context(state)
    assert context["relationships"]["alice"]["trust"] == 35
    assert context["facts"]["who_took_notebook"]["truth_status"] == "possible"
    assert context["goals"]["alice_find_ally"]["completed"] is False
    assert context["session"]["scene_count"] == 0
