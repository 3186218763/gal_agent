from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.service import RuntimeService
from src.story.state import NarrativeBlock, initial_session_state
from src.story.storage import StoryEventStore
from tests.story_factories import minimal_script_pack_dict


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


class FailingPlanner:
    async def plan_scene(self, pack, state):
        raise OpenAIError("provider secret token leaked")

    async def resolve_action(self, pack, state, choice):
        raise OpenAIError("provider secret token leaked")


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
) -> AppDependencies:
    packs_root = write_test_pack(tmp_path)
    store = StoryEventStore(tmp_path / "story.db")
    registry = ScriptPackRegistry(packs_root)
    runtime = RuntimeService(
        store,
        planner if planner is not None else FakePlanner(),
        writer if writer is not None else FakeWriter(),
    )
    return AppDependencies(store=store, registry=registry, runtime=runtime)


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
    scene = http.post(
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0},
    )
    assert scene.status_code == 200
    payload = scene.json()
    session = SimpleNamespace(
        id=session_id,
        revision=payload["revision"],
        choices=payload["choices"],
    )
    return http, session


@pytest.fixture
def decision_client(_decision_bundle) -> TestClient:
    return _decision_bundle[0]


@pytest.fixture
def decision_session(_decision_bundle) -> SimpleNamespace:
    return _decision_bundle[1]


@pytest.fixture
def provider_failure_client(tmp_path: Path) -> TestClient:
    deps = build_test_dependencies(tmp_path, planner=FailingPlanner(), writer=FakeWriter())
    pack = deps.registry.get("test_pack")
    state = initial_session_state(pack, "session_01", session_seed=1)
    deps.store.create_session(state)
    return TestClient(create_app(deps))


def test_create_advance_and_choose_v2_session(tmp_path: Path):
    app = create_app(build_test_dependencies(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 17},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    scene = client.post(
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0},
    )
    assert scene.status_code == 200
    payload = scene.json()
    assert len(payload["choices"]) == 2

    chosen = client.post(
        f"/api/v2/sessions/{session_id}/choices/{payload['choices'][0]['id']}",
        json={"expected_revision": payload["revision"], "idempotency_key": "req-01"},
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


def test_pending_decision_returns_409(decision_client: TestClient, decision_session: SimpleNamespace):
    response = decision_client.post(
        f"/api/v2/sessions/{decision_session.id}/advance",
        json={"expected_revision": decision_session.revision},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "command_conflict"}}


def test_stale_revision_returns_409(decision_client: TestClient, decision_session: SimpleNamespace):
    choice_id = decision_session.choices[0]["id"]
    response = decision_client.post(
        f"/api/v2/sessions/{decision_session.id}/choices/{choice_id}",
        json={"expected_revision": 0, "idempotency_key": "stale-01"},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "command_conflict"}}


def test_missing_runtime_configuration_fails_default_app_start(monkeypatch):
    monkeypatch.delenv("GAL_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        create_app()


def test_provider_failure_is_redacted(provider_failure_client: TestClient):
    response = provider_failure_client.post(
        "/api/v2/sessions/session_01/advance", json={"expected_revision": 0}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "model_provider_unavailable"}}
    assert "secret" not in response.text


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
    runtime = RuntimeService(store, FakePlanner(), FakeWriter())
    pack = registry.get("test_pack")
    state = initial_session_state(pack, "session_ending", session_seed=9)
    world = state.world.model_copy(update={"scene_count": state.world.max_scenes})
    store.create_session(state.model_copy(update={"world": world}))
    http = TestClient(create_app(AppDependencies(store=store, registry=registry, runtime=runtime)))

    scene = http.post(
        "/api/v2/sessions/session_ending/advance",
        json={"expected_revision": 0},
    )
    assert scene.status_code == 200
    advance_body = scene.json()
    assert advance_body["ending_id"] == "safe_exit"
    assert advance_body["ending_title"] == "Closing Time"
    assert advance_body["blocks"][0]["text"] == "Ending: Closing Time"

    loaded = http.get("/api/v2/sessions/session_ending")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["status"] == "ended"
    assert body["ending_id"] == "safe_exit"
    assert body["ending_title"] == "Closing Time"
    assert body["blocks"][0]["text"] == "Ending: Closing Time"
    assert body["scene_id"] is None
