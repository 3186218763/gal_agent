"""V2 API contract tests: sessions, projections, and the /turns route.

The legacy mutation routes (``/advance``, ``/choices/{id}``) are deleted;
``/turns`` is the one production mutation interface.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from src.story.api import AppDependencies, ScriptPackRegistry, create_app
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.config import ConfigurationError
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def write_test_pack(root: Path) -> Path:
    packs_root = root / "script_packs"
    pack_dir = packs_root / "test_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump(budget_test_pack_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return packs_root


def build_test_dependencies(tmp_path: Path, planner=None) -> AppDependencies:
    packs_root = write_test_pack(tmp_path)
    store = StoryEventStore(tmp_path / "story.db")
    registry = ScriptPackRegistry(packs_root)
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=planner if planner is not None else FakePlanner(),
    )
    return AppDependencies(store=store, registry=registry, orchestrator=orchestrator)


def _parse_sse_lines(response) -> list[tuple[str, dict]]:
    """Parse SSE frames from a streaming TestClient response."""
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


def _sse_turn(
    client: TestClient,
    session_id: str,
    revision: int,
    key: str,
    choice_id: str | None = None,
) -> list[tuple[str, dict]]:
    """POST /turns and return parsed SSE events."""
    with client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/turns",
        json={
            "expected_revision": revision,
            "idempotency_key": key,
            "choice_id": choice_id,
        },
    ) as resp:
        assert resp.status_code == 200
        return _parse_sse_lines(resp)


def _turn_result(events) -> dict[str, Any]:
    """Flatten an SSE turn into {revision, choices, error, ...}."""
    result: dict[str, Any] = {"blocks": [], "choices": [], "events": events}
    for evt_type, data in events:
        if evt_type == "block":
            result["blocks"].append(data)
        elif evt_type == "segment_ready":
            result.update(data)
        elif evt_type == "error":
            result["error"] = data["code"]
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(build_test_dependencies(tmp_path)))


@pytest.fixture
def _decision_bundle(tmp_path: Path) -> tuple[TestClient, SimpleNamespace]:
    http = TestClient(create_app(build_test_dependencies(tmp_path)))
    created = http.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 11},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    turn = _turn_result(_sse_turn(http, session_id, 0, "req-00"))
    assert "error" not in turn
    session = SimpleNamespace(
        id=session_id,
        revision=turn["revision"],
        choices=turn["choices"],
    )
    return http, session


@pytest.fixture
def decision_client(_decision_bundle) -> TestClient:
    return _decision_bundle[0]


@pytest.fixture
def decision_session(_decision_bundle) -> SimpleNamespace:
    return _decision_bundle[1]


# ---------------------------------------------------------------------------
# Tests: full lifecycle
# ---------------------------------------------------------------------------


def test_create_turn_and_choose_v2_session(tmp_path: Path):
    app = create_app(build_test_dependencies(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 17},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    opening = _turn_result(_sse_turn(client, session_id, 0, "req-00"))
    assert len(opening["choices"]) == 2

    choice_id = opening["choices"][0]["id"]
    followup = _turn_result(_sse_turn(client, session_id, opening["revision"], "req-01", choice_id))
    assert "error" not in followup
    assert followup["revision"] > opening["revision"]


def test_v1_routes_are_gone(client: TestClient):
    assert client.post("/api/sessions", json={}).status_code == 404
    assert client.get("/api/sessions/example").status_code == 404


def test_legacy_mutation_routes_are_gone(client: TestClient):
    """The old production mutation surfaces must not exist."""
    created = client.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 5})
    session_id = created.json()["session_id"]
    assert (
        client.post(
            f"/api/v2/sessions/{session_id}/advance",
            json={"expected_revision": 0, "idempotency_key": "k"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v2/sessions/{session_id}/choices/invented",
            json={"expected_revision": 0, "idempotency_key": "k"},
        ).status_code
        == 404
    )


def test_unknown_pack_and_session_return_404(client: TestClient):
    assert (
        client.post("/api/v2/sessions", json={"pack_id": "missing", "session_seed": 1}).status_code
        == 404
    )
    assert client.get("/api/v2/sessions/missing").status_code == 404


# ---------------------------------------------------------------------------
# Tests: choice turn errors
# ---------------------------------------------------------------------------


def test_unoffered_choice_yields_invalid_choice_event(
    decision_client: TestClient, decision_session: SimpleNamespace
):
    events = _sse_turn(
        decision_client,
        decision_session.id,
        decision_session.revision,
        "bad-01",
        "invented",
    )
    assert any(t == "error" and d["code"] == "invalid_choice" for t, d in events)


def test_stale_revision_yields_revision_conflict_event(
    decision_client: TestClient, decision_session: SimpleNamespace
):
    choice_id = decision_session.choices[0]["id"]
    events = _sse_turn(
        decision_client,
        decision_session.id,
        0,
        "stale-01",
        choice_id,
    )
    assert any(t == "error" and d["code"] == "revision_conflict" for t, d in events)


def test_decision_required_when_opening_with_pending_decision(
    decision_client: TestClient, decision_session: SimpleNamespace
):
    """Opening with a pending decision must not generate a new segment."""
    events = _sse_turn(
        decision_client,
        decision_session.id,
        decision_session.revision,
        "open-again",
    )
    assert any(t == "error" and d["code"] == "decision_required" for t, d in events)


# ---------------------------------------------------------------------------
# Tests: /turns SSE failure semantics
# ---------------------------------------------------------------------------


def test_turn_requires_idempotency_key(client: TestClient):
    created = client.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 1})
    session_id = created.json()["session_id"]
    response = client.post(
        f"/api/v2/sessions/{session_id}/turns",
        json={"expected_revision": 0, "choice_id": None},
    )
    assert response.status_code == 422


def test_repeated_turn_with_same_key_replays_identical_events(tmp_path: Path):
    app = create_app(build_test_dependencies(tmp_path))
    http = TestClient(app)
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 2})
    session_id = created.json()["session_id"]
    first = _turn_result(_sse_turn(http, session_id, 0, "turn-1"))
    replay = _turn_result(_sse_turn(http, session_id, 0, "turn-1"))

    assert replay["revision"] == first["revision"]
    assert [b["text"] for b in replay["blocks"]] == [b["text"] for b in first["blocks"]]
    assert replay["choices"] == first["choices"]

    session = http.get(f"/api/v2/sessions/{session_id}").json()
    assert session["revision"] == first["revision"]


# ---------------------------------------------------------------------------
# Tests: dependency wiring
# ---------------------------------------------------------------------------


def test_default_dependencies_include_segment_pipeline(monkeypatch, tmp_path):
    """Verify default_dependencies() wires the segment pipeline agents.

    The test runs fully offline: the LLM client and settings are stubbed,
    and the store points at a tmp_path database. Construction of the real
    LLMDirector/LLMSegmentWriter/Guard/CompletionJudge/TurnOrchestrator proves
    the wiring path is sound without any provider credentials.
    """
    import dataclasses

    import src.story.api as api_module
    from src.story.runtime.completion_judge import CompletionJudge
    from src.story.runtime.director import LLMDirector
    from src.story.runtime.guard import Guard
    from src.story.runtime.planner import LLMPlanner
    from src.story.runtime.segment_writer import LLMSegmentWriter
    from src.story.runtime.turn_orchestrator import TurnOrchestrator

    fields = {f.name for f in dataclasses.fields(AppDependencies)}
    assert {"orchestrator", "director", "segment_writer", "guard"} <= fields
    assert "runtime" not in fields
    assert "pregen_manager" not in fields

    class FakeLLMClient:
        """Stub stand-in for LLMClient: never touches the network."""

        def __init__(self, settings: Any) -> None:
            self.settings = settings

        async def complete_structured(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("network model calls are not allowed in offline tests")

        async def stream_text(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("network model calls are not allowed in offline tests")
            if False:  # pragma: no cover - signature-compatible async generator
                yield None

    monkeypatch.setattr(api_module, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(
        api_module.LLMSettings,
        "from_env",
        staticmethod(lambda: SimpleNamespace(model="fake-model")),
    )
    monkeypatch.setenv("GAL_DATABASE_PATH", str(tmp_path / "story.db"))
    monkeypatch.setenv("GAL_SCRIPT_PACK_ROOT", str(tmp_path / "script_packs"))

    deps = api_module.default_dependencies()

    assert isinstance(deps.director, LLMDirector)
    assert isinstance(deps.segment_writer, LLMSegmentWriter)
    assert isinstance(deps.guard, Guard)
    assert isinstance(deps.orchestrator, TurnOrchestrator)
    assert deps.orchestrator.director is deps.director
    assert deps.orchestrator.writer is deps.segment_writer
    assert deps.orchestrator.guard is deps.guard
    assert isinstance(deps.orchestrator.completion_judge, CompletionJudge)
    assert isinstance(deps.orchestrator.planner, LLMPlanner)


# ---------------------------------------------------------------------------
# Tests: misc endpoints
# ---------------------------------------------------------------------------


def test_missing_runtime_configuration_fails_default_app_start(monkeypatch):
    monkeypatch.delenv("GAL_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        create_app()


def test_health_reports_v2(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "runtime": "v2"}


def test_get_session_returns_created_state(tmp_path: Path):
    http = TestClient(create_app(build_test_dependencies(tmp_path)))
    created = http.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 3},
    )
    session_id = created.json()["session_id"]
    loaded = http.get(f"/api/v2/sessions/{session_id}")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["session_id"] == session_id
    assert body["pack_id"] == "test_pack"
    assert body["revision"] == 0
    assert body["status"] == "active"


def test_get_session_returns_public_projection_without_internal_state(client: TestClient):
    created = client.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 4})
    session_id = created.json()["session_id"]
    body = client.get(f"/api/v2/sessions/{session_id}").json()
    assert body["status"] == "active"
    assert body["location_id"] == "cafe"
    assert body["time_label"] == "opening"
    assert body["present_character_ids"] == ["alice"]
    assert "truth_status" not in body
    assert "knowledge" not in body
    assert "suspicions" not in body
    assert "pack_hash" not in body
    assert "session_seed" not in body


def test_pack_projection_endpoint_exposes_public_metadata(client: TestClient):
    response = client.get("/api/v2/packs/test_pack")
    assert response.status_code == 200
    body = response.json()
    assert body["pack_id"] == "test_pack"
    assert body["title"] == "Test Pack"
    assert body["locations"][0]["location_id"] == "cafe"
    assert "secrets" not in body
    assert "personality" not in body


def test_unknown_pack_projection_returns_404(client: TestClient):
    assert client.get("/api/v2/packs/missing").status_code == 404
