from pathlib import Path

from src.content.setting_pack_loader import load_setting_pack
from src.core.world_store import WorldStore
from src.domain.events import EventDatabase

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_world_store_roundtrip(tmp_path):
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    store = WorldStore(tmp_path)
    state = store.create_session("sid", pack)
    state.steps = 2
    events = EventDatabase()
    store.save("sid", state, events)
    loaded_state, loaded_events = store.load("sid")
    assert loaded_state.steps == 2
    assert loaded_state.pack_id == pack.pack_id
    assert loaded_events.events == []


def test_world_store_load_missing(tmp_path):
    store = WorldStore(tmp_path)
    assert store.load("nope") is None


def test_world_store_delete_and_list(tmp_path):
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    store = WorldStore(tmp_path)
    store.create_session("a", pack)
    store.create_session("b", pack)
    assert set(store.list_sessions()) == {"a", "b"}
    assert store.delete("a") is True
    assert store.load("a") is None
    assert store.list_sessions() == ["b"]
