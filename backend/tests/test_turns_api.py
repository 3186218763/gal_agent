from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from src.story.api import AppDependencies, ScriptPackRegistry, create_app
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)


def _parse_sse(response) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current_event = "message"
    current_data = ""
    for line in response.iter_lines():
        line = line.strip()
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            current_data = line[6:]
        elif line == "":
            if current_data:
                events.append((current_event, json.loads(current_data)))
            current_event = "message"
            current_data = ""
    return events


def _write_v2_pack(root: Path) -> Path:
    packs_root = root / "script_packs"
    pack_dir = packs_root / "test_pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump(budget_test_pack_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return packs_root


def _build_deps(tmp_path: Path, planner=None) -> AppDependencies:
    packs_root = _write_v2_pack(tmp_path)
    store = StoryEventStore(tmp_path / "turns_api.db")
    registry = ScriptPackRegistry(packs_root)
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=planner if planner is not None else FakePlanner(),
    )
    return AppDependencies(
        store=store,
        registry=registry,
        orchestrator=orchestrator,
    )


def _opening_ready(http: TestClient, session_id: str) -> dict:
    """Run the opening turn and return the segment_ready payload."""
    with http.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/turns",
        json={"expected_revision": 0, "idempotency_key": "cmd-opening", "choice_id": None},
    ) as resp:
        assert resp.status_code == 200
        events = _parse_sse(resp)
    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "decision"
    return ready


def test_turns_endpoint_streams_segment(tmp_path: Path):
    http = TestClient(create_app(_build_deps(tmp_path)))
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 1})
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    with http.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/turns",
        json={"expected_revision": 0, "idempotency_key": "cmd-01", "choice_id": None},
    ) as resp:
        assert resp.status_code == 200
        events = _parse_sse(resp)

    types = [e[0] for e in events]
    assert "segment_started" in types
    assert "block" in types
    assert "segment_ready" in types

    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "decision"
    assert len(ready["choices"]) == 2


def test_turns_endpoint_ending(tmp_path: Path):
    http = TestClient(create_app(_build_deps(tmp_path)))
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 2})
    assert created.status_code == 201

    # Manually set scene_count to max to trigger ending.
    from src.story.script_pack.compiler import compile_source

    pack = compile_source(budget_test_pack_dict())
    from src.story.state import initial_session_state

    store = _build_deps(tmp_path).store
    state = initial_session_state(pack, "force_ending", session_seed=1)
    state = state.model_copy(
        update={"world": state.world.model_copy(update={"scene_count": state.world.max_scenes})}
    )
    store.create_session(state)

    with http.stream(
        "POST",
        "/api/v2/sessions/force_ending/turns",
        json={"expected_revision": 0, "idempotency_key": "cmd-end", "choice_id": None},
    ) as resp:
        events = _parse_sse(resp)

    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "ending"
    assert "ending" in ready


def test_turns_endpoint_idempotent_replay(tmp_path: Path):
    http = TestClient(create_app(_build_deps(tmp_path)))
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 3})
    session_id = created.json()["session_id"]

    with http.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/turns",
        json={"expected_revision": 0, "idempotency_key": "cmd-replay", "choice_id": None},
    ) as resp:
        events1 = _parse_sse(resp)

    with http.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/turns",
        json={"expected_revision": 0, "idempotency_key": "cmd-replay", "choice_id": None},
    ) as resp:
        events2 = _parse_sse(resp)

    ready1 = next(data for t, data in events1 if t == "segment_ready")
    ready2 = next(data for t, data in events2 if t == "segment_ready")
    assert ready1["segment_id"] == ready2["segment_id"]
    assert ready1["revision"] == ready2["revision"]


def _post_turn(
    http: TestClient, session_id: str, revision: int, key: str, choice_id: str | None = None
) -> list[tuple[str, dict]]:
    with http.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/turns",
        json={
            "expected_revision": revision,
            "idempotency_key": key,
            "choice_id": choice_id,
        },
    ) as resp:
        assert resp.status_code == 200
        return _parse_sse(resp)


# ---------------------------------------------------------------------------
# Recovery tests: Pending Consequence durability, idempotency, coherence
# ---------------------------------------------------------------------------


class FailingPlanner(FakePlanner):
    """Planner whose resolve_action always fails like a model outage."""

    async def resolve_action(self, pack, state, choice):
        raise RuntimeError("model failed")


class FailsOncePlanner(FakePlanner):
    """Planner that fails the first resolve_action only (transient outage)."""

    def __init__(self):
        self._failed = False

    async def resolve_action(self, pack, state, choice):
        if not self._failed:
            self._failed = True
            raise RuntimeError("transient model failure")
        return await super().resolve_action(pack, state, choice)


def test_failed_consequence_turn_leaves_pending_consequence_visible_in_projection(
    tmp_path: Path,
):
    """A failed /turns consequence attempt commits the Choice Meaning, and the
    durable GET projection shows awaiting_resolution — no re-offered choices,
    no provisional blocks, revision advanced by exactly one."""
    http = TestClient(create_app(_build_deps(tmp_path, planner=FailingPlanner())))
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 21})
    session_id = created.json()["session_id"]
    ready = _opening_ready(http, session_id)
    choice = ready["choices"][0]

    events = _post_turn(http, session_id, ready["revision"], "select-fail", choice["id"])
    assert any(t == "error" and d["code"] == "generation_unavailable" for t, d in events)
    assert not any(t == "segment_ready" for t, d in events)

    body = http.get(f"/api/v2/sessions/{session_id}").json()
    assert body["pending_consequence_status"] == "awaiting_resolution"
    assert body["revision"] == ready["revision"] + 1
    assert body["status"] == "active"
    assert body["choices"] == []
    assert body["segment_choices"] == []
    assert body["segment_blocks"] == []


def test_stale_revision_on_choice_turn_yields_error_and_commits_nothing(tmp_path: Path):
    """A consequence turn against a stale expected_revision fails with a
    revision_conflict SSE error and leaves no trace in the projection."""
    http = TestClient(create_app(_build_deps(tmp_path)))
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 22})
    session_id = created.json()["session_id"]
    ready = _opening_ready(http, session_id)

    events = _post_turn(http, session_id, 0, "stale-select", ready["choices"][0]["id"])
    assert any(t == "error" and d["code"] == "revision_conflict" for t, d in events)

    body = http.get(f"/api/v2/sessions/{session_id}").json()
    assert body["revision"] == ready["revision"]
    assert body["pending_consequence_status"] is None
    # The pending decision is still offered; nothing was committed.
    assert [c["id"] for c in body["choices"]] == [c["id"] for c in ready["choices"]]


def test_repeated_recovery_with_same_key_replays_one_result(tmp_path: Path):
    """Recovery retried with the same key replays the winning committed
    segment; no duplicate consequence or segment ever appends."""
    deps = _build_deps(tmp_path, planner=FailsOncePlanner())
    http = TestClient(create_app(deps))
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 23})
    session_id = created.json()["session_id"]
    ready = _opening_ready(http, session_id)
    choice = ready["choices"][0]

    # Choice turn fails after committing the choice.
    events = _post_turn(http, session_id, ready["revision"], "select-once", choice["id"])
    assert any(t == "error" and d["code"] == "generation_unavailable" for t, d in events)
    pending_revision = ready["revision"] + 1

    # First recovery wins and commits a segment.
    first = _post_turn(http, session_id, pending_revision, "resume-same", None)
    ready1 = next(data for t, data in first if t == "segment_ready")

    # Retry with the same key — replays the stored result, appends nothing.
    second = _post_turn(http, session_id, pending_revision, "resume-same", None)
    ready2 = next(data for t, data in second if t == "segment_ready")
    assert ready2 == ready1

    # A different key on the same pending consequence also replays the one
    # stable consequence command (derived from the choice event, not the key).
    third = _post_turn(http, session_id, pending_revision, "another-device", None)
    ready3 = next(data for t, data in third if t == "segment_ready")
    assert ready3 == ready1

    envelopes = deps.store.load_events(session_id)
    assert len([e for e in envelopes if e.event.type == "player_action_selected"]) == 1
    assert len([e for e in envelopes if e.event.type == "action_resolved"]) == 1
    assert len([e for e in envelopes if e.event.type == "scene_committed"]) == 2  # opening + one

    body = http.get(f"/api/v2/sessions/{session_id}").json()
    assert body["pending_consequence_status"] is None
    assert body["revision"] == ready1["revision"]


def test_failed_recovery_error_is_coherent_with_projection(tmp_path: Path):
    """If the recovery attempt itself fails, the SSE error frame agrees with
    the durable projection: the consequence is still awaiting resolution and
    the failed attempt committed nothing."""
    http = TestClient(create_app(_build_deps(tmp_path, planner=FailingPlanner())))
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 24})
    session_id = created.json()["session_id"]
    ready = _opening_ready(http, session_id)
    choice = ready["choices"][0]

    _post_turn(http, session_id, ready["revision"], "select-fail", choice["id"])
    pending_revision = ready["revision"] + 1

    events = _post_turn(http, session_id, pending_revision, "resume-fail", None)
    assert any(t == "error" and d["code"] == "generation_unavailable" for t, d in events)

    body = http.get(f"/api/v2/sessions/{session_id}").json()
    assert body["pending_consequence_status"] == "awaiting_resolution"
    assert body["revision"] == pending_revision


def test_replaying_failed_choice_turn_with_same_key_replays_winning_result(
    tmp_path: Path,
):
    """Re-POSTing the failed choice turn with its original idempotency key
    replays the full committed result instead of committing a second choice."""
    deps = _build_deps(tmp_path, planner=FailsOncePlanner())
    http = TestClient(create_app(deps))
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 25})
    session_id = created.json()["session_id"]
    ready = _opening_ready(http, session_id)
    choice = ready["choices"][0]

    first = _post_turn(http, session_id, ready["revision"], "select-retry", choice["id"])
    assert any(t == "error" and d["code"] == "generation_unavailable" for t, d in first)

    replay = _post_turn(http, session_id, ready["revision"], "select-retry", choice["id"])
    ready1 = next(data for t, data in replay if t == "segment_ready")
    assert ready1["revision"] > ready["revision"] + 1

    envelopes = deps.store.load_events(session_id)
    assert len([e for e in envelopes if e.event.type == "player_action_selected"]) == 1
    assert len([e for e in envelopes if e.event.type == "action_resolved"]) == 1


def test_existing_session_uses_its_pinned_script_pack_version(tmp_path: Path):
    packs_root = _write_v2_pack(tmp_path)
    store = StoryEventStore(tmp_path / "pinned_pack.db")
    registry = ScriptPackRegistry(packs_root)
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    http = TestClient(
        create_app(
            AppDependencies(
                store=store,
                registry=registry,
                orchestrator=orchestrator,
            )
        )
    )
    created = http.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 7},
    )
    session_id = created.json()["session_id"]
    session = store.load_session(session_id)

    revised_source = budget_test_pack_dict()
    revised_source["identity"]["title"] = "A revised work"
    pack_file = packs_root / "test_pack" / "pack.yaml"
    pack_file.write_text(
        yaml.safe_dump(revised_source, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    registry._cache.clear()
    assert registry.get("test_pack").pack_hash != session.pack_hash

    with http.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/turns",
        json={
            "expected_revision": 0,
            "idempotency_key": "pinned-opening",
            "choice_id": None,
        },
    ) as response:
        events = _parse_sse(response)

    assert any(event_type == "segment_ready" for event_type, _ in events)
    assert store.load_pack_version(session.pack_hash).source.identity.title != "A revised work"
