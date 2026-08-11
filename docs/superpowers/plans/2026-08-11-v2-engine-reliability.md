# V2 Engine Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make the V2 Galgame backend reproducible, strict-schema compatible, idempotent, fail-closed on generation errors, and able to expose a safe player-facing session projection for a later browser player.

**Architecture:** Retain the existing compiler, Planner/Writer ports, Validator, Simulator, reducer, and SQLite event stream as the sole state authority. Add a strict Planner contract, command receipts stored atomically with events, and a projection layer that translates internal state into public gameplay data. The OpenCode Go / `deepseek-v4-flash` Responses configuration remains the default provider implementation.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, OpenAI Agents SDK Responses model, SQLite, pytest, uv.

---

## Scope Boundary

This plan intentionally ends at a stable backend contract. It does not implement the React player, browser local storage, pixel placeholders, 2D assets, free-text play, streaming, WebSockets, accounts, or background agents. Those require a follow-up frontend plan after the API below is verified with a real model.

## Planned File Structure

| File | Responsibility |
| --- | --- |
| `.gitignore` | Stop ignoring the reproducible `uv.lock` file. |
| `backend/uv.lock` | Checked-in resolved dependency graph verified by offline and live tests. |
| `backend/tests/live/conftest.py` | Load local `.env` only for explicitly enabled live tests. |
| `backend/tests/test_live_env.py` | Unit-test dotenv precedence without a provider request. |
| `backend/src/story/runtime/contracts.py` | Strict-schema-safe `LearnedFactPlan` and typed generation error. |
| `backend/src/story/runtime/validator.py` | Validate explicit learned-fact entries and reject duplicates. |
| `backend/src/story/runtime/simulator.py` | Convert explicit learned-fact entries to deterministic events. |
| `backend/src/story/runtime/service.py` | Build complete candidate turns, make command execution idempotent, and fail closed. |
| `backend/src/story/runtime/fallbacks.py` | Remove player-facing generic fallback helpers once no runtime path uses them. |
| `backend/src/story/storage/event_store.py` | Persist, lease, replay, release, and atomically complete command receipts. |
| `backend/src/story/storage/__init__.py` | Export command receipt exceptions and data types. |
| `backend/src/story/projection.py` | Construct public pack and session projections without internal-state leakage. |
| `backend/src/story/api.py` | Require idempotency keys for mutations, return projections, and map typed errors. |
| `backend/tests/test_runtime_agents.py` | Prove Planner and Writer contracts construct strict SDK schemas. |
| `backend/tests/test_runtime_validator.py` | Cover duplicate learned-fact validation. |
| `backend/tests/test_runtime_simulator.py` | Cover deterministic learned-fact event expansion. |
| `backend/tests/test_runtime_service.py` | Cover no-commit generation failures, idempotent advance/choice, and combined acknowledgement commits. |
| `backend/tests/test_story_event_store.py` | Cover receipt claim, replay, mismatch, lease expiry, and atomic event-plus-receipt commit. |
| `backend/tests/test_story_projection.py` | Cover public projection shape and hidden-state non-leakage. |
| `backend/tests/test_v2_api.py` | Cover mutation request/response contracts, errors, and projection behavior. |
| `backend/tests/live/test_opencode_go_v2_runtime.py` | Keep the bounded real Planner -> Writer -> choice live capability roundtrip. |
| `README.md` and `backend/README.md` | Document the tracked lockfile, live test environment loading, and revised V2 response contract. |

## Task 1: Make Dependency and Live-Test Configuration Reproducible

**Files:**
- Modify: `.gitignore:1-27`
- Create: `backend/tests/live/conftest.py`
- Create: `backend/tests/test_live_env.py`
- Create: `backend/uv.lock`
- Modify: `README.md:210-226`
- Modify: `backend/README.md:83-97`

- [x] **Step 1: Write a failing dotenv precedence test.**

```python
# backend/tests/test_live_env.py
from __future__ import annotations

import os

from tests.live.conftest import load_live_environment


def test_live_environment_loads_dotenv_only_when_enabled(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("OPENCODE_GO_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("RUN_LIVE_ZEN_TEST", raising=False)

    load_live_environment(dotenv_path)
    assert "OPENCODE_GO_API_KEY" not in os.environ

    monkeypatch.setenv("RUN_LIVE_ZEN_TEST", "1")
    load_live_environment(dotenv_path)
    assert os.environ["OPENCODE_GO_API_KEY"] == "file-key"

    monkeypatch.setenv("OPENCODE_GO_API_KEY", "process-key")
    load_live_environment(dotenv_path)
    assert os.environ["OPENCODE_GO_API_KEY"] == "process-key"
```

- [x] **Step 2: Run the new test to verify the import fails before the live bootstrap exists.**

Run:

```bash
cd backend
uv run pytest tests/test_live_env.py -v
```

Expected: FAIL with an import error for `tests.live.conftest` or `load_live_environment`.

- [x] **Step 3: Add an opt-in live dotenv bootstrap.**

```python
# backend/tests/live/conftest.py
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_live_environment(dotenv_path: Path | None = None) -> None:
    if os.getenv("RUN_LIVE_ZEN_TEST") == "1":
        load_dotenv(dotenv_path=dotenv_path, override=False)


load_live_environment()
```

This keeps normal offline test collection independent of a local secret and lets explicitly enabled live tests match `src/main.py` and `play-live`.

- [x] **Step 4: Run the focused test and the standard offline suite.**

Run:

```bash
cd backend
uv run pytest tests/test_live_env.py -v
uv run pytest tests/ -q
```

Expected: the new test passes; the existing offline suite remains green with the live provider test skipped.

- [x] **Step 5: Track the lockfile and document the exact live invocation.**

Remove the `uv.lock` line from `.gitignore`, then resolve and check the lockfile:

```bash
cd backend
uv lock
uv sync --extra dev --locked
```

Document this real-model command in both READMEs:

```bash
cd backend
RUN_LIVE_ZEN_TEST=1 uv run pytest -m live tests/live/test_opencode_go_v2_runtime.py -v
```

State that the command reads ignored `backend/.env` with `override=False`, so an explicitly exported CI secret wins.

- [x] **Step 6: Commit the configuration boundary.**

```bash
git add .gitignore backend/uv.lock backend/tests/live/conftest.py \
  backend/tests/test_live_env.py README.md backend/README.md
git commit -m "build: track live runtime dependencies"
```

## Task 2: Replace the Non-Strict Planner Mapping Contract

**Files:**
- Modify: `backend/src/story/runtime/contracts.py:18-88`
- Modify: `backend/src/story/runtime/validator.py:84-146`
- Modify: `backend/src/story/runtime/simulator.py:92-132`
- Modify: `backend/tests/test_runtime_agents.py:1-220`
- Modify: `backend/tests/test_runtime_validator.py`
- Modify: `backend/tests/test_runtime_simulator.py:90-140`

- [x] **Step 1: Add strict schema and duplicate-entry tests.**

```python
# backend/tests/test_runtime_agents.py
from agents.agent_output import AgentOutputSchema


def test_planner_and_writer_outputs_support_strict_json_schema():
    assert AgentOutputSchema(PlannerOutput).is_strict_json_schema() is True
    assert AgentOutputSchema(WriterOutput).is_strict_json_schema() is True
```

```python
# backend/tests/test_runtime_validator.py
def test_action_resolution_rejects_duplicate_learned_fact_characters_and_ids(pack, state):
    resolution = ActionResolution(
        action_id="ask",
        outcome="success",
        learned_facts=(
            LearnedFactPlan(character_id="alice", fact_ids=("cafe_is_open", "cafe_is_open")),
            LearnedFactPlan(character_id="alice", fact_ids=("cafe_is_open",)),
        ),
    )
    with pytest.raises(ProposalRejected, match="learned fact"):
        validate_action_resolution(pack, state, resolution, expected_action_id="ask")
```

- [x] **Step 2: Run the strict-schema test to reproduce the current SDK failure.**

Run:

```bash
cd backend
uv run pytest tests/test_runtime_agents.py::test_planner_and_writer_outputs_support_strict_json_schema -v
```

Expected: FAIL while building `AgentOutputSchema(PlannerOutput)` because `learned_facts` emits dynamic `additionalProperties`.

- [x] **Step 3: Introduce an explicit learned-fact entry type and update all consumers.**

```python
# backend/src/story/runtime/contracts.py
class LearnedFactPlan(RuntimeModel):
    character_id: str
    fact_ids: tuple[str, ...] = ()


class ActionResolution(RuntimeModel):
    action_id: str
    outcome: Literal["success", "partial", "resisted", "backfire"]
    relationship_deltas: tuple[RelationshipDelta, ...] = ()
    goal_deltas: tuple[GoalDelta, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()
    reveal_fact_ids: tuple[str, ...] = ()
    learned_facts: tuple[LearnedFactPlan, ...] = ()
```

In `validate_action_resolution`, replace `resolution.learned_facts.items()` with entry iteration. Reject duplicate `character_id` entries; reject duplicate fact IDs inside an entry; retain the existing checks that the character exists and each fact is committed.

In `resolution_effect_events`, sort entries by `character_id`, then sort each entry's fact IDs before emitting `CharacterLearnedFact`. Update all fixtures to construct `LearnedFactPlan(...)`, never a dictionary.

- [x] **Step 4: Run the focused strict contract, validator, and simulator tests.**

Run:

```bash
cd backend
uv run pytest tests/test_runtime_agents.py tests/test_runtime_validator.py \
  tests/test_runtime_simulator.py -q
```

Expected: PASS. The strict schema regression test proves the current locked SDK can create both output schemas before any provider request.

- [x] **Step 5: Commit the strict contract migration.**

```bash
git add backend/src/story/runtime/contracts.py backend/src/story/runtime/validator.py \
  backend/src/story/runtime/simulator.py backend/tests/test_runtime_agents.py \
  backend/tests/test_runtime_validator.py backend/tests/test_runtime_simulator.py
git commit -m "fix: make planner output strict-schema compatible"
```

## Task 3: Fail Closed Instead of Committing Generic Fallback Turns

**Files:**
- Modify: `backend/src/story/runtime/contracts.py:190-214`
- Modify: `backend/src/story/runtime/service.py:1-198`
- Delete: `backend/src/story/runtime/fallbacks.py`
- Modify: `backend/src/story/runtime/__init__.py`
- Modify: `backend/tests/test_runtime_service.py`
- Modify: `backend/tests/test_v2_api.py`

- [x] **Step 1: Add failing tests for scene and choice generation failures.**

```python
# backend/tests/test_runtime_service.py
class ContractFailingPlanner:
    async def plan_scene(self, pack, state):
        raise ModelContractError("planner contract failed")

    async def resolve_action(self, pack, state, choice):
        raise ModelContractError("resolution contract failed")


@pytest.mark.asyncio
async def test_scene_generation_failure_leaves_session_unmodified(tmp_path):
    service, pack, store = service_fixture(tmp_path, ContractFailingPlanner(), FakeWriter())
    with pytest.raises(RuntimeGenerationUnavailable):
        await service.advance(pack, "session_01", expected_revision=0, idempotency_key="advance-1")
    assert store.load_session("session_01").revision == 0
    assert store.event_count("session_01") == 0
```

Add the analogous decision-state choice test. It must assert that the pending choice remains available after resolution generation fails.

- [x] **Step 2: Run the focused tests to verify existing fallback behavior fails the expectation.**

Run:

```bash
cd backend
uv run pytest tests/test_runtime_service.py -k "generation_failure" -v
```

Expected: FAIL because the current service falls back to generated deterministic scene and resolution data instead of raising a typed error.

- [x] **Step 3: Add a typed generation failure and remove fallback commits.**

```python
# backend/src/story/runtime/contracts.py
class RuntimeGenerationUnavailable(RuntimeError):
    """The real model could not produce a valid, committable turn."""
```

In `RuntimeService`, catch only `ModelContractError` and `ProposalRejected` around Planner/Writer work, then raise `RuntimeGenerationUnavailable` with a safe fixed message. Do not call `fallback_scene_plan`, `fallback_scene_draft`, `fallback_resolution`, or `fallback_ending_draft`.

Keep `OpenAIError` unwrapped so `api.py` continues mapping provider outages to `model_provider_unavailable`. A malformed or validator-rejected result maps to `generation_unavailable`. Delete `fallbacks.py` and remove its package exports once no imports remain.

- [x] **Step 4: Add the API no-commit assertion and run focused tests.**

```python
# backend/tests/test_v2_api.py
def test_generation_contract_failure_is_retryable_and_redacted(tmp_path):
    deps = build_test_dependencies(tmp_path, planner=ContractFailingPlanner(), writer=FakeWriter())
    # Create a session through the app, then advance with idempotency_key="advance-1".
    # Assert 503, {"detail": {"code": "generation_unavailable"}}, and revision 0 on GET.
```

Run:

```bash
cd backend
uv run pytest tests/test_runtime_service.py tests/test_v2_api.py -q
```

Expected: PASS. No generic player-facing prose is committed on Planner, Writer, or resolution contract failure.

- [x] **Step 5: Commit fail-closed generation.**

```bash
git add backend/src/story/runtime backend/tests/test_runtime_service.py backend/tests/test_v2_api.py
git rm backend/src/story/runtime/fallbacks.py
git commit -m "fix: fail closed on invalid model turns"
```

## Task 4: Add Atomic SQLite Command Receipts

**Files:**
- Modify: `backend/src/story/storage/event_store.py:1-214`
- Modify: `backend/src/story/storage/__init__.py`
- Modify: `backend/tests/test_story_event_store.py`

- [x] **Step 1: Add failing receipt lifecycle tests.**

```python
# backend/tests/test_story_event_store.py
def test_command_receipt_replays_completed_result(tmp_path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    claim = store.claim_command("session_01", "command-1", "advance", "fingerprint", now=_NOW)
    assert claim.replay_json is None
    store.commit_command(
        "session_01", "command-1", "advance", "fingerprint", 0,
        [RelationshipChanged(character_id="alice", axis="trust", delta=1)],
        lambda state, _: '{"revision": %d}' % state.revision,
        now=_NOW,
    )
    replay = store.claim_command("session_01", "command-1", "advance", "fingerprint", now=_NOW)
    assert replay.replay_json == '{"revision": 1}'
    assert store.event_count("session_01") == 1
```

Add separate tests that assert: a changed fingerprint raises `CommandRequestMismatch`; an unexpired receipt raises `CommandInProgress`; an expired lease can be reclaimed; and a failed state transition rolls back both events and the completed receipt.

- [x] **Step 2: Run receipt tests to verify the store API is missing.**

Run:

```bash
cd backend
uv run pytest tests/test_story_event_store.py -k "command_receipt" -v
```

Expected: FAIL with missing `claim_command` and `commit_command` methods.

- [x] **Step 3: Add receipt types, schema, and storage methods.**

Add these storage-level types next to the existing store exceptions:

```python
@dataclass(frozen=True)
class CommandClaim:
    replay_json: str | None = None


class CommandInProgress(StoryStoreError):
    pass


class CommandRequestMismatch(StoryStoreError):
    pass
```

Create `story_command_receipts` during `_initialize` with a composite primary key `(session_id, command_id)`, `command_kind`, `request_fingerprint`, `status`, `lease_expires_at`, `result_json`, `result_revision`, and timestamps.

Implement these methods on `StoryEventStore`:

```python
def claim_command(
    self, session_id: str, command_id: str, command_kind: str,
    request_fingerprint: str, *, now: datetime | None = None,
    lease_seconds: int = 120,
) -> CommandClaim: ...

def release_command(
    self, session_id: str, command_id: str, command_kind: str,
    request_fingerprint: str,
) -> None: ...

def commit_command(
    self, session_id: str, command_id: str, command_kind: str,
    request_fingerprint: str, expected_revision: int,
    events: Iterable[StoryEvent],
    result_factory: Callable[[SessionState, tuple[EventEnvelope, ...]], str],
    *, now: datetime | None = None,
) -> tuple[SessionState, tuple[EventEnvelope, ...], str]: ...
```

`claim_command` uses `BEGIN IMMEDIATE`, returns a replay only when kind and fingerprint match, and updates an expired in-progress lease. `commit_command` uses one `BEGIN IMMEDIATE` transaction to load the current state, enforce revision, apply and insert events, build the result JSON from the updated state, update the snapshot, and mark the receipt completed. `release_command` deletes only a matching in-progress receipt so a generation failure can retry safely.

- [x] **Step 4: Export receipt types and run all storage tests.**

Run:

```bash
cd backend
uv run pytest tests/test_story_event_store.py -q
```

Expected: PASS. The atomic rollback test proves receipt completion cannot exist without the corresponding event batch.

- [x] **Step 5: Commit command receipt storage.**

```bash
git add backend/src/story/storage/event_store.py backend/src/story/storage/__init__.py \
  backend/tests/test_story_event_store.py
git commit -m "feat: persist idempotent story command receipts"
```

## Task 5: Execute Runtime Turns Through Command Receipts

**Files:**
- Modify: `backend/src/story/runtime/service.py:1-198`
- Modify: `backend/tests/test_runtime_service.py`

- [x] **Step 1: Add failing idempotent advance and choice tests.**

```python
# backend/tests/test_runtime_service.py
@pytest.mark.asyncio
async def test_repeated_advance_replays_the_same_scene_without_extra_events(tmp_path):
    planner = CountingPlanner()
    service, pack, store = service_fixture(tmp_path, planner, FakeWriter())
    first = await service.advance(pack, "session_01", 0, "advance-1")
    replay = await service.advance(pack, "session_01", 0, "advance-1")
    assert replay == first
    assert planner.scene_calls == 1
    assert store.event_count("session_01") == first.revision
```

Add an equivalent choice test that asserts `resolve_action` executes once. Add a test where a pending non-decision scene is acknowledged and its next scene is committed in the same receipt transaction; when generation fails, assert the original pending scene is still present.

- [x] **Step 2: Run the focused tests to verify method signatures and replay behavior are absent.**

Run:

```bash
cd backend
uv run pytest tests/test_runtime_service.py -k "repeated_advance or pending_scene" -v
```

Expected: FAIL because `advance` has no command ID and currently writes a scene acknowledgement before generating the next scene.

- [x] **Step 3: Refactor `RuntimeService` to build complete candidate event batches.**

Add a stable fingerprint helper:

```python
def _command_fingerprint(kind: str, expected_revision: int, choice_id: str | None = None) -> str:
    payload = {"kind": kind, "expected_revision": expected_revision, "choice_id": choice_id}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
```

Change signatures to:

```python
async def advance(self, pack, session_id, expected_revision, idempotency_key) -> RuntimeScene: ...
async def select_choice(self, pack, session_id, choice_id, expected_revision, idempotency_key) -> ActionResult: ...
```

Claim the receipt before loading the expected revision. If the receipt has `replay_json`, deserialize it with `RuntimeScene.model_validate_json` or `ActionResult.model_validate_json` and return immediately.

For advance, do not append `SceneAcknowledged` eagerly. When a non-decision scene is pending, construct an in-memory acknowledged state with a synthetic `EventEnvelope`, then use that state for ending selection, planning, writing, validation, and simulation. Commit a single final batch:

```python
events = (
    SceneAcknowledged(scene_id=initial.pending_scene.scene_id),
    *scene_events_built_against_acknowledged_state,
)
```

Pass that batch to `store.commit_command`. Use its `result_factory` to serialize the `RuntimeScene` made from the committed scene event and updated state. On `RevisionConflict`, `RuntimeGenerationUnavailable`, `OpenAIError`, or any unexpected exception before completion, call `release_command` and re-raise.

For choice selection, build the existing selection/resolution events, commit them through the same receipt method, and serialize `ActionResult` from the updated revision. Preserve the existing rule that only the offered choice ID is valid.

- [x] **Step 4: Run all runtime service tests.**

Run:

```bash
cd backend
uv run pytest tests/test_runtime_service.py -q
```

Expected: PASS. Verify exact-once behavior, fail-closed behavior, stale command conflicts, ending commits, and no pre-generation acknowledgement commit.

- [x] **Step 5: Commit idempotent runtime execution.**

```bash
git add backend/src/story/runtime/service.py backend/tests/test_runtime_service.py
git commit -m "feat: make story mutations idempotent"
```

## Task 6: Add Public Pack and Session Projections

**Files:**
- Create: `backend/src/story/projection.py`
- Create: `backend/tests/test_story_projection.py`
- Modify: `backend/tests/test_v2_api.py`

- [x] **Step 1: Add failing projection shape and non-leakage tests.**

```python
# backend/tests/test_story_projection.py
def test_pack_projection_exposes_public_metadata_only(pack):
    projection = project_pack(pack)
    assert projection.pack_id == pack.source.identity.id
    assert projection.title == pack.source.identity.title
    alice = next(c for c in projection.characters if c.character_id == "alice")
    assert alice.name == "Alice"
    assert projection.locations[0].location_id == "cafe"
    body = projection.model_dump_json()
    assert "secrets" not in body
    assert "beliefs" not in body


def test_session_projection_never_leaks_internal_state(state):
    projection = project_session(state)
    assert projection.session_id == state.session_id
    assert projection.revision == state.revision
    body = projection.model_dump_json()
    assert "truth_status" not in body
    assert "knowledge" not in body
    assert "suspicions" not in body
    assert "pack_hash" not in body
    assert "session_seed" not in body
```

- [x] **Step 2: Run the projection tests to verify the module is missing.**

Run:

```bash
cd backend
uv run pytest tests/test_story_projection.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `src.story.projection`.

- [x] **Step 3: Implement the projection layer.**

```python
# backend/src/story/projection.py
class PackCharacterProjection(FrozenModel):
    character_id: str
    name: str
    public_profile: str


class PackLocationProjection(FrozenModel):
    location_id: str
    name: str


class PackProjection(FrozenModel):
    pack_id: str
    title: str
    language: str
    characters: tuple[PackCharacterProjection, ...]
    locations: tuple[PackLocationProjection, ...]


class SessionProjection(FrozenModel):
    session_id: str
    pack_id: str
    revision: int
    status: str
    phase: str
    scene_count: int
    pending_decision_id: str | None
    scene_id: str | None
    blocks: tuple[NarrativeBlock, ...] = ()
    choices: tuple[PresentedChoice, ...] = ()
    ending_id: str | None = None
    ending_title: str | None = None
    location_id: str
    time_label: str
    present_character_ids: tuple[str, ...]
```

`project_pack(pack)` copies only identity, title, language, and each character's id/name/public_profile plus each location's id/name. `project_session(state)` copies session identity, revision, status, phase, scene count, pending scene blocks/choices, pending decision id, ending id/title, and world location/time/present characters. It never copies facts, characters runtime, threads, goals, seed, or pack hash.

- [x] **Step 4: Run projection tests and the full offline suite.**

Run:

```bash
cd backend
uv run pytest tests/test_story_projection.py -v
uv run pytest tests/ -q
```

Expected: PASS; the whole offline suite stays green.

- [x] **Step 5: Commit the projection layer.**

```bash
git add backend/src/story/projection.py backend/tests/test_story_projection.py
git commit -m "feat: add safe public story projections"
```

## Task 7: Require Idempotency Keys, Return Projections, and Map Typed Errors

**Files:**
- Modify: `backend/src/story/api.py`
- Modify: `backend/tests/test_v2_api.py`

- [x] **Step 1: Add failing API tests for required advance key, projection responses, and typed errors.**

```python
# backend/tests/test_v2_api.py
def test_advance_requires_idempotency_key(client: TestClient):
    created = client.post(
        "/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 1}
    )
    session_id = created.json()["session_id"]
    response = client.post(
        f"/api/v2/sessions/{session_id}/advance", json={"expected_revision": 0}
    )
    assert response.status_code == 422


def test_repeated_advance_with_same_key_replays_without_extra_events(tmp_path: Path):
    app = create_app(build_test_dependencies(tmp_path))
    http = TestClient(app)
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 2})
    session_id = created.json()["session_id"]
    first = http.post(
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "advance-1"},
    )
    replay = http.post(
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "advance-1"},
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()


def test_get_session_returns_public_projection_without_internal_state(client: TestClient):
    created = client.post(
        "/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 4}
    )
    session_id = created.json()["session_id"]
    body = client.get(f"/api/v2/sessions/{session_id}").json()
    assert body["status"] == "active"
    assert "truth_status" not in body
    assert "knowledge" not in body


def test_generation_contract_failure_is_retryable_and_redacted(tmp_path: Path):
    deps = build_test_dependencies(tmp_path, planner=ContractFailingPlanner(), writer=FakeWriter())
    http = TestClient(create_app(deps))
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 5})
    session_id = created.json()["session_id"]
    response = http.post(
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "advance-1"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "generation_unavailable"}}
    loaded = http.get(f"/api/v2/sessions/{session_id}")
    assert loaded.json()["revision"] == 0
```

- [x] **Step 2: Run the new API tests to verify they fail.**

Run:

```bash
cd backend
uv run pytest tests/test_v2_api.py -k "idempotency or projection or generation_contract_failure" -v
```

Expected: FAIL because advance lacks an idempotency key field, GET returns the raw state shape, and no handler maps `RuntimeGenerationUnavailable`.

- [x] **Step 3: Update `api.py` to require keys, return projections, and map errors.**

Change `RevisionRequest` to require `idempotency_key: str = Field(min_length=1, max_length=120)` and pass it into `runtime.advance`. Replace `SessionResponse.from_state` usage with `project_session(state)`; if `SessionResponse` is kept as the wire model, build it from `SessionProjection`. Add a `GET /api/v2/packs/{pack_id}` endpoint returning `project_pack(pack)`. Add an exception handler mapping `RuntimeGenerationUnavailable` to `503 {"detail": {"code": "generation_unavailable"}}`. Ensure `OpenAIError` still maps to `model_provider_unavailable`.

- [x] **Step 4: Run the focused and full API suites.**

Run:

```bash
cd backend
uv run pytest tests/test_v2_api.py -q
uv run pytest tests/ -q
```

Expected: PASS with all pre-existing API behavior preserved (unknown pack 404, unoffered choice 422, stale revision 409, provider failure 503, health v2).

- [x] **Step 5: Commit the API contract.**

```bash
git add backend/src/story/api.py backend/tests/test_v2_api.py
git commit -m "feat: require idempotency keys and expose safe projections"
```

## Task 8: Verify Full Suite, Update Docs, and Finish

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`

- [x] **Step 1: Run the entire offline suite one final time.**

Run:

```bash
cd backend
uv run pytest tests/ -q
```

Expected: PASS (all offline tests; the live test stays skipped without `RUN_LIVE_ZEN_TEST=1`).

- [x] **Step 2: Update documentation.**

Document in `README.md` and `backend/README.md`: mutation endpoints require `idempotency_key`; GET session returns a safe public projection; generation failures map to `503 generation_unavailable` and never commit a turn; the strict Planner `learned_facts` contract; tracked `uv.lock`; live test invocation.

- [x] **Step 3: Run the live test against the real model if configured.**

Run:

```bash
cd backend
RUN_LIVE_ZEN_TEST=1 uv run pytest -m live tests/live/test_opencode_go_v2_runtime.py -v
```

Expected: PASS with a real `OPENCODE_GO_API_KEY` in `backend/.env`. If no key is available, report the live test as unverified.

- [x] **Step 4: Commit documentation updates.**

```bash
git add README.md backend/README.md
git commit -m "docs: document idempotent v2 API and safe projections"
```
