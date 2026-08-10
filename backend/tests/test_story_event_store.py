from pathlib import Path

import pytest

from src.story.script_pack import compile_source
from src.story.state import FactRevealed, RelationshipChanged, initial_session_state
from src.story.state.reducer import StateTransitionError
from src.story.storage import RevisionConflict, SessionAlreadyExists, StoryEventStore
from tests.story_factories import minimal_script_pack_dict


def _state():
    return initial_session_state(
        compile_source(minimal_script_pack_dict()),
        "session_01",
        session_seed=42,
    )


def test_create_and_load_initial_session(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    state = _state()

    store.create_session(state)
    loaded = store.load_session(state.session_id)

    assert loaded == state
    assert store.list_sessions() == ["session_01"]
    assert store.event_count("session_01") == 0


def test_append_assigns_sequences_and_replays_after_snapshot(tmp_path: Path):
    database = tmp_path / "story.db"
    store = StoryEventStore(database, snapshot_every=2)
    store.create_session(_state())

    state, first_batch = store.append(
        "session_01",
        expected_revision=0,
        events=[
            RelationshipChanged(character_id="alice", axis="trust", delta=5),
            RelationshipChanged(character_id="alice", axis="trust", delta=4),
        ],
    )
    state, second_batch = store.append(
        "session_01",
        expected_revision=state.revision,
        events=[RelationshipChanged(character_id="alice", axis="trust", delta=3)],
    )

    assert [event.sequence for event in first_batch] == [1, 2]
    assert [event.sequence for event in second_batch] == [3]
    assert state.world.relationships["alice"]["trust"] == 47

    reopened = StoryEventStore(database, snapshot_every=2)
    assert reopened.load_session("session_01") == state
    assert reopened.event_count("session_01") == 3


def test_stale_expected_revision_is_rejected(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    store.append(
        "session_01",
        expected_revision=0,
        events=[RelationshipChanged(character_id="alice", axis="trust", delta=1)],
    )

    with pytest.raises(RevisionConflict, match="expected 0, current 1"):
        store.append(
            "session_01",
            expected_revision=0,
            events=[RelationshipChanged(character_id="alice", axis="trust", delta=1)],
        )


def test_invalid_batch_rolls_back_every_event(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    original = _state()
    store.create_session(original)

    with pytest.raises(StateTransitionError):
        store.append(
            "session_01",
            expected_revision=0,
            events=[
                RelationshipChanged(character_id="alice", axis="trust", delta=5),
                FactRevealed(fact_id="who_took_notebook"),
            ],
        )

    assert store.load_session("session_01") == original
    assert store.event_count("session_01") == 0


def test_duplicate_session_is_rejected(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    state = _state()
    store.create_session(state)

    with pytest.raises(SessionAlreadyExists):
        store.create_session(state)
