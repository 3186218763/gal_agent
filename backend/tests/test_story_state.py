from pathlib import Path

import pytest
from pydantic import ValidationError

from src.story.script_pack import compile_script_pack, compile_source
from src.story.state import (
    FactTruthStatus,
    FactVisibility,
    StoryPhase,
    initial_session_state,
)
from tests.story_factories import minimal_script_pack_dict


def test_initial_state_separates_truth_visibility_and_character_knowledge():
    pack = compile_source(minimal_script_pack_dict())

    state = initial_session_state(pack, "session_01", session_seed=42)

    assert state.revision == 0
    assert state.world.phase == StoryPhase.OPENING
    assert state.world.location_id == "cafe"
    assert state.facts["cafe_is_open"].truth_status == FactTruthStatus.COMMITTED
    assert state.facts["cafe_is_open"].visibility == FactVisibility.REVEALED
    assert state.facts["who_took_notebook"].truth_status == FactTruthStatus.POSSIBLE
    assert state.facts["who_took_notebook"].value is None
    assert "cafe_is_open" in state.characters["alice"].knowledge
    assert state.world.relationships["alice"]["trust"] == 35
    assert state.world.goals["alice_find_ally"].progress == 0


def test_real_pack_state_keeps_private_fixed_fact_hidden():
    pack = compile_script_pack(
        Path(__file__).resolve().parents[1] / "script_packs" / "cafe_mystery"
    )

    state = initial_session_state(pack, "session_02", session_seed=7)

    assert state.facts["org_exists"].truth_status == FactTruthStatus.COMMITTED
    assert state.facts["org_exists"].visibility == FactVisibility.HIDDEN
    assert "org_exists" in state.characters["alice"].knowledge
    assert "org_exists" in state.characters["bob"].knowledge


def test_session_state_is_immutable():
    state = initial_session_state(
        compile_source(minimal_script_pack_dict()),
        "session_01",
        session_seed=42,
    )

    with pytest.raises(ValidationError):
        state.revision = 1
