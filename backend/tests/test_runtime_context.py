import json
from pathlib import Path

from src.story.runtime.context import (
    build_condition_context,
    build_planner_context,
    build_writer_context,
)
from src.story.runtime.contracts import ScenePlan
from src.story.script_pack import compile_script_pack, compile_source
from src.story.state import initial_session_state
from tests.story_factories import minimal_script_pack_dict

CAFE_PACK_DIR = Path(__file__).resolve().parents[1] / "script_packs" / "cafe_mystery"


def compiled_state():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "session_01", session_seed=42)
    return state, pack


def cafe_state():
    pack = compile_script_pack(CAFE_PACK_DIR)
    state = initial_session_state(pack, "session_cafe", session_seed=7)
    return state, pack


def test_condition_context_matches_compiled_condition_paths():
    state, pack = compiled_state()
    context = build_condition_context(state)
    assert context["relationships"]["alice"]["trust"] == 35
    assert context["facts"]["who_took_notebook"]["truth_status"] == "possible"
    assert context["goals"]["alice_find_ally"]["completed"] is False
    assert context["session"]["scene_count"] == 0


def test_writer_context_does_not_give_alice_bobs_private_fact():
    state, pack = cafe_state()
    plan = ScenePlan(
        scene_id="scene_01",
        summary="Alice studies the protagonist's reaction.",
        location_id=state.world.location_id,
        present_character_ids=("alice",),
        terminal="continue",
    )
    context = build_writer_context(
        pack,
        state,
        present_character_ids=("alice",),
        approved_plan=plan,
    )
    alice = context["characters"][0]
    assert all(item["id"] != "bob_has_org_history" for item in alice["known_facts"])
    assert "鲍勃过去曾因隐环遭受损失" not in json.dumps(alice, ensure_ascii=False)


def test_possible_latent_fact_exposes_question_but_not_candidate_answer():
    state, pack = cafe_state()
    context = build_planner_context(pack, state)
    fact = next(item for item in context["facts"] if item["id"] == "notebook_holder")
    assert fact["question"] == "现在谁持有笔记本？"
    assert "value" not in fact
