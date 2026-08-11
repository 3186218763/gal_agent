from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from openai import OpenAIError

from src.story.api import AppDependencies, ScriptPackRegistry, create_app
from src.story.runtime.config import ConfigurationError
from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    EndingDraft,
    ModelContractError,
    SceneDraft,
    ScenePlan,
    StreamingGeneratorPort,
    WrittenChoice,
)
from src.story.runtime.service import RuntimeService
from src.story.state import NarrativeBlock, initial_session_state
from src.story.storage import StoryEventStore
from tests.story_factories import minimal_script_pack_dict

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePlanner:
    async def plan_scene(self, pack, state):
        return valid_decision_plan()

    async def resolve_action(self, pack, state, choice):
        return ActionResolution(action_id=choice.action_id, outcome="success")


class FakeWriter:
    async def write_scene(self, pack, state, plan):
        return valid_scene_draft(plan)

    async def write_ending(self, pack, state, ending):
        return EndingDraft(
            ending_id=ending.id,
            title=ending.title,
            blocks=(NarrativeBlock(kind="narration", text=f"Ending: {ending.title}"),),
        )


class FakeStreamingGenerator:
    """Fake StreamingGeneratorPort that yields canned blocks + choices."""

    def __init__(
        self,
        blocks: list[dict[str, Any]] | None = None,
        complete: dict[str, Any] | None = None,
    ) -> None:
        self._blocks = blocks if blocks is not None else [
            {"kind": "narration", "text": "The cafe hums quietly."},
        ]
        self._complete = complete or {
            "scene_id": "scene_01",
            "terminal": "decision",
            "decision_id": "decision_01",
            "choices": [
                {
                    "option_id": "ask",
                    "action_id": "ask",
                    "label": "ask directly",
                    "intent": "ask directly",
                },
                {
                    "option_id": "observe",
                    "action_id": "observe",
                    "label": "watch carefully",
                    "intent": "watch carefully",
                },
            ],
        }

    async def generate_scene(
        self, pack, state
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        for blk in self._blocks:
            yield ("block", blk)
        yield ("complete", self._complete)


class ProviderFailingGenerator:
    async def generate_scene(self, pack, state):
        raise OpenAIError("provider secret token leaked")
        yield  # type: ignore[unreachable]


class ContractFailingGenerator:
    async def generate_scene(self, pack, state):
        raise ModelContractError("planner contract failed")
        yield  # type: ignore[unreachable]


def valid_decision_plan() -> ScenePlan:
    return ScenePlan(
        scene_id="scene_01",
        summary="Alice waits for the protagonist to choose.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="decision_01",
        choices=(
            ChoicePlan(option_id="ask", action_id="ask", intent="ask directly"),
            ChoicePlan(option_id="observe", action_id="observe", intent="watch carefully"),
        ),
    )


def valid_scene_draft(plan: ScenePlan) -> SceneDraft:
    return SceneDraft(
        scene_id=plan.scene_id,
        blocks=(NarrativeBlock(kind="narration", text="The cafe hums quietly."),),
        choices=tuple(
            WrittenChoice(option_id=item.option_id, label=item.intent[:80])
            for item in plan.choices
        ),
    )


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def write_test_pack(root: Path) -> Path:
    packs_root = root / "script_packs"
    pack_dir = packs_root / "test_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump(minimal_script_pack_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return packs_root


def build_test_dependencies(
    tmp_path: Path,
    planner=None,
    writer=None,
    generator: StreamingGeneratorPort | None = None,
) -> AppDependencies:
    packs_root = write_test_pack(tmp_path)
    store = StoryEventStore(tmp_path / "story.db")
    registry = ScriptPackRegistry(packs_root)
    runtime = RuntimeService(
        store,
        planner if planner is not None else FakePlanner(),
        writer if writer is not None else FakeWriter(),
        generator if generator is not None else FakeStreamingGenerator(),
    )
    return AppDependencies(store=store, registry=registry, runtime=runtime)


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


def _sse_advance(
    client: TestClient, session_id: str, revision: int, key: str
) -> dict[str, Any]:
    """Call the SSE advance endpoint and return a dict similar to the old JSON response."""
    with client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": revision, "idempotency_key": key},
    ) as resp:
        raw_text = resp.read().decode()
        events = _parse_sse_lines(resp)

    result: dict[str, Any] = {"blocks": [], "choices": [], "_raw": raw_text}
    for evt_type, data in events:
        if evt_type == "block":
            result["blocks"].append(data)
        elif evt_type == "choices":
            result["choices"] = data
        elif evt_type == "done":
            result.update(data)
        elif evt_type == "error":
            result["error"] = data["code"]
    return result


def _sse_advance_raw(
    client: TestClient, session_id: str, revision: int, key: str
) -> str:
    """Return the raw SSE response text for inspection."""
    with client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": revision, "idempotency_key": key},
    ) as resp:
        return resp.read().decode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(build_test_dependencies(tmp_path)))


@pytest.fixture
def _decision_bundle(tmp_path: Path) -> tuple[TestClient, SimpleNamespace]:
    app = create_app(build_test_dependencies(tmp_path))
    http = TestClient(app)
    created = http.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 11},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    scene = _sse_advance(http, session_id, 0, "req-00")
    assert "error" not in scene
    session = SimpleNamespace(
        id=session_id,
        revision=scene["revision"],
        choices=scene["choices"],
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


def test_create_advance_and_choose_v2_session(tmp_path: Path):
    app = create_app(build_test_dependencies(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 17},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    scene = _sse_advance(client, session_id, 0, "req-00")
    assert len(scene["choices"]) == 2

    chosen = client.post(
        f"/api/v2/sessions/{session_id}/choices/{scene['choices'][0]['id']}",
        json={"expected_revision": scene["revision"], "idempotency_key": "req-01"},
    )
    assert chosen.status_code == 200


def test_v1_routes_are_gone(client: TestClient):
    assert client.post("/api/sessions", json={}).status_code == 404
    assert client.get("/api/sessions/example").status_code == 404


def test_unknown_pack_and_session_return_404(client: TestClient):
    assert (
        client.post("/api/v2/sessions", json={"pack_id": "missing", "session_seed": 1}).status_code
        == 404
    )
    assert client.get("/api/v2/sessions/missing").status_code == 404


# ---------------------------------------------------------------------------
# Tests: choice endpoint (unchanged JSON)
# ---------------------------------------------------------------------------


def test_unoffered_choice_returns_422(decision_client: TestClient, decision_session: SimpleNamespace):
    response = decision_client.post(
        f"/api/v2/sessions/{decision_session.id}/choices/invented",
        json={
            "expected_revision": decision_session.revision,
            "idempotency_key": "bad-01",
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_choice"}}


def test_stale_revision_returns_409(decision_client: TestClient, decision_session: SimpleNamespace):
    choice_id = decision_session.choices[0]["id"]
    response = decision_client.post(
        f"/api/v2/sessions/{decision_session.id}/choices/{choice_id}",
        json={"expected_revision": 0, "idempotency_key": "stale-01"},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "command_conflict"}}


# ---------------------------------------------------------------------------
# Tests: advance SSE error handling
# ---------------------------------------------------------------------------


def test_pending_decision_returns_error_event(decision_client: TestClient, decision_session: SimpleNamespace):
    """Advancing when a decision is pending yields an SSE error event."""
    result = _sse_advance(
        decision_client, decision_session.id, decision_session.revision, "advance-09"
    )
    assert result["error"] == "decision_required"


def test_provider_failure_sends_generation_error(tmp_path: Path):
    deps = build_test_dependencies(
        tmp_path, generator=ProviderFailingGenerator()
    )
    pack = deps.registry.get("test_pack")
    state = initial_session_state(pack, "session_01", session_seed=1)
    deps.store.create_session(state)
    http = TestClient(create_app(deps))

    raw = _sse_advance_raw(http, "session_01", 0, "provider-advance")
    assert "generation_unavailable" in raw
    assert "secret" not in raw


def test_generation_contract_failure_is_retryable_and_redacted(tmp_path: Path):
    deps = build_test_dependencies(
        tmp_path, generator=ContractFailingGenerator()
    )
    http = TestClient(create_app(deps))
    created = http.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 13},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    result = _sse_advance(http, session_id, 0, "advance-1")
    assert result["error"] == "generation_unavailable"
    assert "contract failed" not in result["_raw"]

    loaded = http.get(f"/api/v2/sessions/{session_id}")
    assert loaded.status_code == 200
    assert loaded.json()["revision"] == 0


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


def test_get_session_keeps_ending_title_and_epilogue_after_end(tmp_path: Path):
    packs_root = write_test_pack(tmp_path)
    pack_yaml = packs_root / "test_pack" / "pack.yaml"
    raw = yaml.safe_load(pack_yaml.read_text(encoding="utf-8"))
    for ending in raw["endings"]:
        if ending["type"] == "fallback":
            ending["id"] = "safe_exit"
    pack_yaml.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    store = StoryEventStore(tmp_path / "story.db")
    registry = ScriptPackRegistry(packs_root)
    runtime = RuntimeService(store, FakePlanner(), FakeWriter(), FakeStreamingGenerator())
    pack = registry.get("test_pack")
    state = initial_session_state(pack, "session_ending", session_seed=9)
    world = state.world.model_copy(update={"scene_count": state.world.max_scenes})
    store.create_session(state.model_copy(update={"world": world}))
    http = TestClient(create_app(AppDependencies(store=store, registry=registry, runtime=runtime)))

    scene = _sse_advance(http, "session_ending", 0, "ending-advance")
    assert scene["ending_id"] == "safe_exit"
    assert scene["ending_title"] == "Closing Time"
    assert scene["blocks"][0]["text"] == "Ending: Closing Time"

    loaded = http.get("/api/v2/sessions/session_ending")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["status"] == "ended"
    assert body["ending_id"] == "safe_exit"
    assert body["ending_title"] == "Closing Time"
    assert body["blocks"][0]["text"] == "Ending: Closing Time"
    assert body["scene_id"] is None


def test_advance_requires_idempotency_key(client: TestClient):
    created = client.post(
        "/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 1}
    )
    session_id = created.json()["session_id"]
    response = client.post(
        f"/api/v2/sessions/{session_id}/advance", json={"expected_revision": 0}
    )
    assert response.status_code == 422


def test_repeated_advance_with_same_key_replays_identical_events(tmp_path: Path):
    app = create_app(build_test_dependencies(tmp_path))
    http = TestClient(app)
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 2})
    session_id = created.json()["session_id"]
    first = _sse_advance(http, session_id, 0, "advance-1")
    replay = _sse_advance(http, session_id, 0, "advance-1")

    assert replay["revision"] == first["revision"]
    assert [b["text"] for b in replay["blocks"]] == [b["text"] for b in first["blocks"]]
    assert replay["choices"] == first["choices"]

    session = http.get(f"/api/v2/sessions/{session_id}").json()
    assert session["revision"] == first["revision"]


def test_get_session_returns_public_projection_without_internal_state(client: TestClient):
    created = client.post(
        "/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 4}
    )
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
