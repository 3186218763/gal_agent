from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.segment_contracts import PacingEnvelope
from src.story.script_pack.compiler import compile_source
from src.story.state import StoryPhase, initial_session_state
from tests.story_factories import minimal_script_pack_dict


def _make_pack_and_state(scene_count=0, min_scenes=8, max_scenes=20, reserved=3):
    raw = minimal_script_pack_dict()
    raw["experience"]["min_scenes"] = min_scenes
    raw["experience"]["max_scenes"] = max_scenes
    raw["experience"]["reserved_resolution_scenes"] = reserved
    pack = compile_source(raw)
    state = initial_session_state(pack, "s1", session_seed=1)
    if scene_count > 0:
        state = state.model_copy(update={
            "world": state.world.model_copy(update={"scene_count": scene_count})
        })
    return pack, state


def test_pacing_at_opening():
    pack, state = _make_pack_and_state(scene_count=0)
    env = compute_pacing_envelope(state, pack)
    assert env.phase == StoryPhase.OPENING
    assert env.scene_count == 0
    assert env.remaining_budget == 20
    assert env.can_end is False
    assert env.must_end is False
    assert env.in_convergence is False
    assert env.max_new_threads == 3
    assert env.quiet_scene_allowance >= 1


def test_pacing_can_end_after_min_scenes():
    pack, state = _make_pack_and_state(scene_count=8)
    env = compute_pacing_envelope(state, pack)
    assert env.can_end is True
    assert env.must_end is False


def test_pacing_must_end_at_max():
    pack, state = _make_pack_and_state(scene_count=20)
    env = compute_pacing_envelope(state, pack)
    assert env.must_end is True
    assert env.can_end is True
    assert env.remaining_budget == 0


def test_pacing_convergence_window():
    pack, state = _make_pack_and_state(scene_count=17, max_scenes=20, reserved=3)
    env = compute_pacing_envelope(state, pack)
    assert env.in_convergence is True
    assert env.max_new_threads == 0


def test_pacing_before_convergence():
    pack, state = _make_pack_and_state(scene_count=16, max_scenes=20, reserved=3)
    env = compute_pacing_envelope(state, pack)
    assert env.in_convergence is False
    assert env.max_new_threads == 3


def test_pacing_returns_pacing_envelope_type():
    pack, state = _make_pack_and_state()
    env = compute_pacing_envelope(state, pack)
    assert isinstance(env, PacingEnvelope)
