from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.story.script_pack import compile_source
from src.story.state import (
    EventEnvelope,
    FactRevealed,
    NarrativeBlock,
    PresentedChoice,
    ProposedEvent,
    RelationshipChanged,
    SceneCommitted,
    initial_session_state,
    prepare_event_batch,
)
from src.story.state.reducer import StateTransitionError
from src.story.storage import (
    CommandInProgress,
    CommandRequestMismatch,
    RevisionConflict,
    SessionAlreadyExists,
    StoryEventStore,
    StoryStoreError,
)
from tests.story_factories import minimal_script_pack_dict

_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _state():
    return initial_session_state(
        compile_source(minimal_script_pack_dict()),
        "session_01",
        session_seed=42,
    )


def _decision_scene_event() -> SceneCommitted:
    return SceneCommitted(
        scene_id="scene_01",
        terminal="decision",
        location_id="cafe",
        present_character_ids=("alice",),
        blocks=(NarrativeBlock(kind="narration", text="Alice waits."),),
        decision_id="decision_01",
        choices=(
            PresentedChoice(
                id="ask_alice",
                action_id="ask",
                label="Ask Alice",
                intent="ask directly",
            ),
            PresentedChoice(
                id="observe_alice",
                action_id="observe",
                label="Watch quietly",
                intent="observe",
            ),
        ),
    )


def test_create_and_load_initial_session(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    state = _state()

    store.create_session(state)
    loaded = store.load_session(state.session_id)

    assert loaded == state
    assert store.list_sessions() == ["session_01"]
    assert store.event_count("session_01") == 0


def test_session_can_persist_and_reload_its_exact_script_pack_version(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "versioned", session_seed=42)

    store.create_session(state, pack=pack)
    loaded_pack = store.load_pack_version(pack.pack_hash)

    assert loaded_pack.pack_hash == pack.pack_hash
    assert loaded_pack.source == pack.source


def test_pack_version_hash_must_match_session(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "versioned", session_seed=42).model_copy(
        update={"pack_hash": "0" * 64}
    )

    with pytest.raises(StoryStoreError, match="pack version"):
        store.create_session(state, pack=pack)


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


def test_append_envelopes_preserves_preallocated_ids(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    envelopes = prepare_event_batch(
        "session_01",
        0,
        (
            ProposedEvent(
                local_ref="effect:one",
                event=RelationshipChanged(character_id="alice", axis="trust", delta=1),
            ),
        ),
        event_id_factory=lambda: "preallocated-effect-id",
    )

    state, persisted = store.append_envelopes("session_01", 0, envelopes)

    assert persisted == envelopes
    assert store.load_events("session_01") == envelopes
    assert state.revision == 1


@pytest.mark.parametrize("mutation", ["wrong_session", "gap", "duplicate_id"])
def test_append_envelopes_rejects_invalid_preallocated_batch(tmp_path: Path, mutation: str):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    first = EventEnvelope(
        event_id="effect-1",
        session_id="session_01",
        sequence=1,
        event=RelationshipChanged(character_id="alice", axis="trust", delta=1),
    )
    second = EventEnvelope(
        event_id="effect-2",
        session_id="session_01",
        sequence=2,
        event=RelationshipChanged(character_id="alice", axis="trust", delta=1),
    )
    if mutation == "wrong_session":
        second = second.model_copy(update={"session_id": "other"})
    elif mutation == "gap":
        second = second.model_copy(update={"sequence": 3})
    else:
        second = second.model_copy(update={"event_id": "effect-1"})

    with pytest.raises(StoryStoreError):
        store.append_envelopes("session_01", 0, (first, second))

    assert store.event_count("session_01") == 0
    assert store.load_session("session_01").revision == 0


def test_append_envelopes_rejects_empty_batch(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())

    with pytest.raises(StoryStoreError, match="at least one"):
        store.append_envelopes("session_01", 0, ())


def test_append_envelopes_rejects_event_id_already_in_history_atomically(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    first = EventEnvelope(
        event_id="shared-id",
        session_id="session_01",
        sequence=1,
        event=RelationshipChanged(character_id="alice", axis="trust", delta=1),
    )
    store.append_envelopes("session_01", 0, (first,))
    duplicate = EventEnvelope(
        event_id="shared-id",
        session_id="session_01",
        sequence=2,
        event=FactRevealed(fact_id="who_took_notebook"),
    )

    with pytest.raises(StoryStoreError, match="uniqueness"):
        store.append_envelopes("session_01", 1, (duplicate,))

    state = store.load_session("session_01")
    assert state.revision == 1
    assert state.world.relationships["alice"]["trust"] == 36
    assert store.event_count("session_01") == 1


def test_duplicate_session_is_rejected(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    state = _state()
    store.create_session(state)

    with pytest.raises(SessionAlreadyExists):
        store.create_session(state)


def test_load_events_returns_persisted_scene_payload(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    state = _state()
    store.create_session(state)
    store.append(state.session_id, 0, [_decision_scene_event()])
    events = store.load_events(state.session_id)
    assert events[0].event.blocks[0].text == "Alice waits."


def test_command_receipt_replays_completed_result(tmp_path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    claim = store.claim_command("session_01", "command-1", "advance", "fingerprint", now=_NOW)
    assert claim.replay_json is None
    store.commit_command(
        "session_01",
        "command-1",
        "advance",
        "fingerprint",
        0,
        [RelationshipChanged(character_id="alice", axis="trust", delta=1)],
        lambda state, _: '{"revision": ' + str(state.revision) + "}",
        now=_NOW,
    )
    replay = store.claim_command("session_01", "command-1", "advance", "fingerprint", now=_NOW)
    assert replay.replay_json == '{"revision": 1}'
    assert store.event_count("session_01") == 1


def test_command_receipt_mismatched_fingerprint_is_rejected(tmp_path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    store.claim_command("session_01", "command-1", "advance", "fingerprint-a", now=_NOW)
    with pytest.raises(CommandRequestMismatch):
        store.claim_command("session_01", "command-1", "advance", "fingerprint-b", now=_NOW)


def test_command_receipt_unexpired_lease_is_busy(tmp_path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    store.claim_command("session_01", "command-1", "advance", "fingerprint", now=_NOW)
    with pytest.raises(CommandInProgress):
        store.claim_command("session_01", "command-1", "advance", "fingerprint", now=_NOW)


def test_command_receipt_expired_lease_can_be_reclaimed(tmp_path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    store.claim_command("session_01", "command-1", "advance", "fingerprint", now=_NOW)
    claim = store.claim_command(
        "session_01",
        "command-1",
        "advance",
        "fingerprint",
        now=_NOW + timedelta(seconds=121),
    )
    assert claim.replay_json is None


def test_command_receipt_failed_transition_rolls_back_events_and_receipt(tmp_path):
    store = StoryEventStore(tmp_path / "story.db")
    original = _state()
    store.create_session(original)
    store.claim_command("session_01", "command-1", "advance", "fingerprint", now=_NOW)
    with pytest.raises(StateTransitionError):
        store.commit_command(
            "session_01",
            "command-1",
            "advance",
            "fingerprint",
            0,
            [
                RelationshipChanged(character_id="alice", axis="trust", delta=5),
                FactRevealed(fact_id="who_took_notebook"),
            ],
            lambda state, _: '{"revision": ' + str(state.revision) + "}",
            now=_NOW,
        )
    assert store.load_session("session_01") == original
    assert store.event_count("session_01") == 0
    claim = store.claim_command(
        "session_01",
        "command-1",
        "advance",
        "fingerprint",
        now=_NOW + timedelta(seconds=121),
    )
    assert claim.replay_json is None
