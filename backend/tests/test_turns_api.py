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


def _build_deps(tmp_path: Path) -> AppDependencies:
    packs_root = _write_v2_pack(tmp_path)
    store = StoryEventStore(tmp_path / "turns_api.db")
    registry = ScriptPackRegistry(packs_root)
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    return AppDependencies(
        store=store,
        registry=registry,
        runtime=None,
        orchestrator=orchestrator,
    )


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
