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

YOKAI_PACK_DIR = Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school"


def compiled_state():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "session_01", session_seed=42)
    return state, pack


def yokai_state():
    pack = compile_script_pack(YOKAI_PACK_DIR)
    state = initial_session_state(pack, "session_yokai", session_seed=7)
    return state, pack


def test_condition_context_matches_compiled_condition_paths():
    state, _pack = compiled_state()
    context = build_condition_context(state)
    assert context["relationships"]["alice"]["trust"] == 35
    assert context["facts"]["who_took_notebook"]["truth_status"] == "possible"
    assert context["goals"]["alice_find_ally"]["completed"] is False
    assert context["session"]["scene_count"] == 0


def test_writer_context_does_not_give_hiyori_mios_private_fact():
    state, pack = yokai_state()
    plan = ScenePlan(
        scene_id="scene_01",
        summary="日和确认转学生是否跟得上班级安排。",
        location_id=state.world.location_id,
        present_character_ids=("hiyori",),
        terminal="continue",
    )
    context = build_writer_context(
        pack,
        state,
        present_character_ids=("hiyori",),
        approved_plan=plan,
    )
    hiyori = context["characters"][0]
    assert all(item["id"] != "paper_fox_awake" for item in hiyori["known_facts"])
    assert "狐形纸签会在放学钟后" not in json.dumps(hiyori, ensure_ascii=False)


def test_possible_latent_fact_exposes_question_but_not_candidate_answer():
    state, pack = yokai_state()
    context = build_planner_context(pack, state)
    fact = next(item for item in context["facts"] if item["id"] == "paper_fox_sender")
    assert fact["question"] == "是谁让狐形纸签混进陆言的留学资料袋，它原本想把他带到哪里？"
    assert "value" not in fact
