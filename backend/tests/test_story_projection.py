from src.story.projection import project_pack, project_session
from src.story.script_pack import compile_source
from src.story.state import initial_session_state
from tests.story_factories import minimal_script_pack_dict


def compiled_pack_and_state():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "session_01", session_seed=42)
    return pack, state


def test_pack_projection_exposes_public_metadata_only():
    pack, _ = compiled_pack_and_state()
    projection = project_pack(pack)
    assert projection.pack_id == pack.source.identity.id
    assert projection.title == pack.source.identity.title
    assert projection.language == pack.source.identity.language
    alice = next(c for c in projection.characters if c.character_id == "alice")
    assert alice.name == "Alice"
    assert alice.public_profile == "An outgoing student."
    assert projection.locations[0].location_id == "cafe"
    assert projection.locations[0].name == "Cafe"
    body = projection.model_dump_json()
    assert "secrets" not in body
    assert "beliefs" not in body
    assert "personality" not in body
    assert "known_by" not in body
    assert "pack_hash" not in body


def test_session_projection_never_leaks_internal_state():
    _, state = compiled_pack_and_state()
    projection = project_session(state)
    assert projection.session_id == state.session_id
    assert projection.pack_id == state.pack_id
    assert projection.revision == state.revision
    assert projection.status == state.status.value
    assert projection.phase == state.world.phase.value
    assert projection.scene_count == state.world.scene_count
    assert projection.location_id == state.world.location_id
    assert projection.time_label == state.world.time_label
    assert projection.present_character_ids == state.world.present_character_ids
    body = projection.model_dump_json()
    assert "truth_status" not in body
    assert "knowledge" not in body
    assert "suspicions" not in body
    assert "beliefs" not in body
    assert "pack_hash" not in body
    assert "session_seed" not in body
    assert "goals" not in body
    assert "facts" not in body
