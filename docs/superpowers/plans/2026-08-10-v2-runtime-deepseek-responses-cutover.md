# V2 Runtime DeepSeek Responses Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the executable V1 stack and deliver a V2-only text runtime that uses OpenAI Agents SDK `OpenAIResponsesModel` with OpenCode Go DeepSeek V4 Flash, deterministic state validation, V2 HTTP APIs, offline tests, and an opt-in live test.

**Architecture:** Preserve the existing V2 script-pack compiler, immutable session state, typed reducer, and SQLite event store. Add persisted scene/choice presentation contracts, then layer deterministic context/validation/simulation around two model-facing roles, Planner and Writer. Both roles share one `OpenAIResponsesModel`; only typed events accepted by the Kernel may change state.

**Tech Stack:** Python 3.11, Pydantic 2, OpenAI Agents SDK, OpenAI Responses API compatibility, FastAPI, SQLite, pytest, Ruff, React 18, TypeScript, Vite.

**Design spec:** `docs/superpowers/specs/2026-08-10-v2-runtime-deepseek-cutover-design.md`

---

## File Map

### Remove

- `backend/src/agents/`, `content/`, `core/`, `domain/`, `kernel/`, `models/`, `rules/`
- `backend/src/models.py`
- `backend/scripts/chapter_01/`
- V1-only backend tests listed in Task 1
- `frontend/src/api.ts`, `types.ts`, `hooks/`, `components/`

### Create

- `backend/src/story/runtime/contracts.py`: strict Planner, Writer, action, and API-neutral runtime contracts
- `backend/src/story/runtime/config.py`: secret-safe OpenCode Go Responses configuration
- `backend/src/story/runtime/model.py`: shared `AsyncOpenAI` and `OpenAIResponsesModel`
- `backend/src/story/runtime/context.py`: condition and model context assembly
- `backend/src/story/runtime/planner.py`: Planner protocol, SDK Agent, and prompt construction
- `backend/src/story/runtime/writer.py`: Writer protocol, SDK Agent, and prompt construction
- `backend/src/story/runtime/validator.py`: deterministic proposal and draft validation
- `backend/src/story/runtime/simulator.py`: validated proposal-to-event conversion on copied state
- `backend/src/story/runtime/fallbacks.py`: deterministic safe plans, resolutions, and prose
- `backend/src/story/runtime/endings.py`: deterministic ending eligibility and phase progression
- `backend/src/story/runtime/service.py`: scene advance and player-choice orchestration
- `backend/src/story/api.py`: V2 FastAPI contracts, dependency assembly, and routes
- `backend/tests/live/test_opencode_go_v2_runtime.py`: opt-in real Responses smoke test

### Modify

- `backend/src/story/state/models.py`, `events.py`, `reducer.py`: persist rendered blocks and allowed choices
- `backend/src/story/storage/event_store.py`: event retrieval for response/recovery inspection
- `backend/src/story/cli.py`: V2 live autoplay command
- `backend/src/main.py`: V2-only app entry point
- `backend/pyproject.toml`, `backend/requirements.txt`: aligned runtime/dev dependencies and live marker
- `frontend/src/App.tsx`, `App.css`: static V2 shell with no V1 network behavior
- `README.md`, `backend/README.md`: V2-only operation and security instructions

---

### Task 1: Remove the V1 Backend Authority

**Files:**
- Create: `backend/tests/test_v2_only_layout.py`
- Delete: `backend/src/agents/`, `backend/src/content/`, `backend/src/core/`, `backend/src/domain/`, `backend/src/kernel/`, `backend/src/models/`, `backend/src/rules/`, `backend/src/models.py`, `backend/scripts/chapter_01/`
- Delete: `backend/tests/test_memory.py`, `test_ending_evaluator.py`, `test_goal_tracker.py`, `test_option_validator.py`, `test_world_store.py`, `test_setting_pack_loader.py`, `test_character_prompt.py`, `test_option_trigger.py`, `test_kernel_stub.py`, `test_domain_models.py`, `test_director_prompt.py`, `test_phase_tension.py`, `test_choice_parse.py`
- Modify: `backend/src/main.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add a structural test that rejects executable V1 paths**

```python
# backend/tests/test_v2_only_layout.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATHS = (
    "src/agents",
    "src/content",
    "src/core",
    "src/domain",
    "src/kernel",
    "src/models",
    "src/rules",
    "src/models.py",
    "scripts/chapter_01",
)


def test_v1_runtime_paths_are_removed():
    remaining = [path for path in LEGACY_PATHS if (ROOT / path).exists()]
    assert remaining == []


def test_main_does_not_expose_v1_protocol():
    source = (ROOT / "src/main.py").read_text(encoding="utf-8")
    assert '"/api/sessions"' not in source
    assert '"/ws/game/' not in source
    assert "GAL_USE_STUBS" not in source
```

- [ ] **Step 2: Run the structural test and verify it fails**

Run: `cd backend && uv run pytest tests/test_v2_only_layout.py -q`

Expected: FAIL listing the V1 directories and routes.

- [ ] **Step 3: Remove the exact V1 paths and tests**

Run `git rm -r` only against the explicit paths in the Files section. Do not delete `backend/src/story`, `backend/script_packs`, `backend/tests/story_factories.py`, or any `test_story_*`, `test_script_pack_*`, `test_condition_dsl.py`, `test_cafe_mystery_pack.py` file.

- [ ] **Step 4: Replace `backend/src/main.py` with a temporary V2-only health app**

```python
from fastapi import FastAPI

app = FastAPI(title="Galgame AI V2")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "runtime": "v2-foundation"}
```

- [ ] **Step 5: Align dependencies with the V2 foundation**

In `backend/pyproject.toml`, remove SQLAlchemy and the standalone `websockets` dependency. Add the package actually used later by this plan:

```toml
dependencies = [
    "openai-agents>=0.1.3",
    "openai>=1.93.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pyyaml>=6.0.2",
    "pydantic>=2.9.2",
    "python-dotenv>=1.0.1",
]
```

Keep `pytest`, `pytest-asyncio`, and `ruff` in the `dev` extra. Make `backend/requirements.txt` contain the same runtime packages plus test packages; do not pin a second incompatible dependency set.

- [ ] **Step 6: Sync dependencies and run the remaining suite**

Run: `cd backend && uv sync --extra dev`

Expected: lock/sync succeeds with both `openai-agents` and the directly imported `openai` package installed.

Run: `cd backend && uv run pytest tests/ -q`

Expected: the remaining V2 tests and `test_v2_only_layout.py` pass.

- [ ] **Step 7: Commit the V1 backend removal**

```bash
git add backend
git commit -m "refactor: remove legacy v1 backend runtime"
```

---

### Task 2: Preserve Only the React/Vite Shell

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`
- Delete: `frontend/src/api.ts`
- Delete: `frontend/src/types.ts`
- Delete: `frontend/src/hooks/useWebSocketGame.ts`
- Delete: `frontend/src/components/Game.tsx`
- Delete: `frontend/src/components/Game.css`

- [ ] **Step 1: Replace the app with a network-free V2 shell**

```tsx
// frontend/src/App.tsx
import './App.css'

export default function App() {
  return (
    <main className="app-shell">
      <section className="runtime-status" aria-labelledby="app-title">
        <h1 id="app-title">Galgame AI</h1>
        <p>V2 Runtime 尚未连接</p>
      </section>
    </main>
  )
}
```

Replace `App.css` with stable, responsive dimensions:

```css
.app-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: #111311;
  color: #f2f3ef;
}

.runtime-status {
  width: min(100%, 560px);
  border-left: 3px solid #c85d4a;
  padding: 20px 24px;
}

.runtime-status h1 {
  margin: 0 0 8px;
  font-size: 32px;
  letter-spacing: 0;
}

.runtime-status p {
  margin: 0;
  color: #b9beb6;
  overflow-wrap: anywhere;
}

@media (max-width: 360px) {
  .app-shell { padding: 16px; }
  .runtime-status { padding: 16px; }
  .runtime-status h1 { font-size: 26px; }
}
```

- [ ] **Step 2: Delete V1 frontend networking and protocol files**

Use `git rm` on the exact files in the Files section. Do not remove `frontend/package.json`, TypeScript/Vite configs, `main.tsx`, or global CSS.

- [ ] **Step 3: Verify no V1 protocol identifiers remain**

Run: `rg -n 'chapter_01|option_index|player_choice|ws/game|api/sessions|useWebSocketGame' frontend/src`

Expected: no output.

- [ ] **Step 4: Build the preserved frontend shell**

Run: `cd frontend && npm run build`

Expected: TypeScript and Vite build succeed.

- [ ] **Step 5: Commit the frontend cleanup**

```bash
git add frontend
git commit -m "refactor: retain v2 frontend shell only"
```

---

### Task 3: Persist Presented Scene Content and Allowed Choices

**Files:**
- Modify: `backend/src/story/state/models.py`
- Modify: `backend/src/story/state/events.py`
- Modify: `backend/src/story/state/reducer.py`
- Modify: `backend/src/story/state/__init__.py`
- Modify: `backend/src/story/storage/event_store.py`
- Test: `backend/tests/test_story_state.py`
- Test: `backend/tests/test_story_reducer.py`
- Test: `backend/tests/test_story_event_store.py`

- [ ] **Step 1: Write failing tests for persisted presentation and choice validation**

```python
def test_decision_scene_persists_only_allowed_choices():
    state = _state()
    event = SceneCommitted(
        scene_id="scene_01",
        terminal="decision",
        location_id="cafe",
        present_character_ids=("alice",),
        blocks=(NarrativeBlock(kind="narration", text="Alice waits."),),
        decision_id="decision_01",
        choices=(
            PresentedChoice(id="ask_alice", action_id="ask", label="Ask Alice", intent="ask directly"),
            PresentedChoice(id="observe_alice", action_id="observe", label="Watch quietly", intent="observe"),
        ),
    )
    committed = apply_event(state, _envelope(state, event))
    assert committed.pending_scene.blocks[0].text == "Alice waits."
    assert [item.id for item in committed.pending_decision.choices] == ["ask_alice", "observe_alice"]


def test_player_cannot_select_unpresented_choice():
    committed = _decision_state()
    with pytest.raises(StateTransitionError, match="not offered"):
        apply_event(
            committed,
            _envelope(
                committed,
                PlayerActionSelected(
                    decision_id="decision_01",
                    option_id="invented",
                    idempotency_key="request_01",
                ),
            ),
        )


def test_ending_scene_can_commit_at_normal_scene_limit():
    state = _state_at_max_scenes_with_ending_entered()
    committed = apply_event(
        state,
        _envelope(
            state,
            SceneCommitted(
                scene_id="ending_safe_exit",
                terminal="ending",
                location_id="cafe",
                present_character_ids=("alice",),
                blocks=(NarrativeBlock(kind="narration", text="The story closes."),),
            ),
        ),
    )
    assert committed.world.scene_count == state.world.scene_count
```

Add an event-store test:

```python
def test_load_events_returns_persisted_scene_payload(tmp_path):
    store = StoryEventStore(tmp_path / "story.db")
    state = _state()
    store.create_session(state)
    store.append(state.session_id, 0, [_decision_scene_event()])
    events = store.load_events(state.session_id)
    assert events[0].event.blocks[0].text == "Alice waits."
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `cd backend && uv run pytest tests/test_story_state.py tests/test_story_reducer.py tests/test_story_event_store.py -q`

Expected: FAIL because the presentation types and `load_events` do not exist.

- [ ] **Step 3: Add immutable presentation models**

Add to `state/models.py`:

```python
class NarrativeBlock(FrozenModel):
    kind: Literal["narration", "dialogue"]
    text: str = Field(min_length=1, max_length=4000)
    character_id: str | None = None

    @model_validator(mode="after")
    def validate_speaker(self) -> NarrativeBlock:
        if (self.kind == "dialogue") != (self.character_id is not None):
            raise ValueError("character_id is required only for dialogue blocks")
        return self


class PresentedChoice(FrozenModel):
    id: str = Field(min_length=1, max_length=100)
    action_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    intent: str = Field(min_length=1, max_length=240)
    target_character_id: str | None = None
    preview: str | None = Field(default=None, max_length=160)
```

Import `Literal` and `model_validator`. Extend `PendingSceneReference` with `blocks: tuple[NarrativeBlock, ...] = ()` and `PendingDecisionReference` with `choices: tuple[PresentedChoice, ...] = Field(min_length=2, max_length=4)`.

- [ ] **Step 4: Persist presentation on `SceneCommitted`**

```python
class SceneCommitted(FrozenModel):
    type: Literal["scene_committed"] = "scene_committed"
    scene_id: str
    terminal: Literal["continue", "decision", "ending"]
    location_id: str
    present_character_ids: tuple[str, ...]
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)
    decision_id: str | None = None
    choices: tuple[PresentedChoice, ...] = ()
```

Replace the `SceneCommitted` and selection-specific reducer branches with these invariants while retaining the existing location/presence updates:

```python
if isinstance(event, SceneCommitted):
    _require(next_state.pending_scene is None, "a scene is already pending")
    is_ending = event.terminal == "ending"
    if is_ending:
        _require(next_state.ending is not None, "ending scene requires entered ending")
        _require(event.decision_id is None and not event.choices, "ending scene cannot decide")
    else:
        _require(
            next_state.world.scene_count < next_state.world.max_scenes,
            "max scene count reached",
        )
    is_decision = event.terminal == "decision"
    _require(
        is_decision == (event.decision_id is not None),
        "decision_id must be present only for a decision scene",
    )
    _require(
        (is_decision and 2 <= len(event.choices) <= 4)
        or (not is_decision and not event.choices),
        "decision scenes require 2-4 choices and other scenes require none",
    )
    choice_ids = [item.id for item in event.choices]
    _require(len(choice_ids) == len(set(choice_ids)), "choice ids must be unique")
    world = next_state.world.model_copy(
        update={
            "location_id": event.location_id,
            "present_character_ids": event.present_character_ids,
            "scene_count": (
                next_state.world.scene_count
                if is_ending
                else next_state.world.scene_count + 1
            ),
        }
    )
    pending_scene = PendingSceneReference(
        scene_id=event.scene_id,
        revision=envelope.sequence,
        terminal=event.terminal,
        blocks=event.blocks,
    )
    pending_decision = (
        PendingDecisionReference(
            decision_id=event.decision_id,
            scene_id=event.scene_id,
            revision=envelope.sequence,
            choices=event.choices,
        )
        if is_decision
        else None
    )
    next_state = next_state.model_copy(
        update={
            "world": world,
            "pending_scene": pending_scene,
            "pending_decision": pending_decision,
        }
    )

elif isinstance(event, PlayerActionSelected):
    _require(next_state.pending_decision is not None, "no decision is pending")
    _require(
        next_state.pending_decision.decision_id == event.decision_id,
        "player action does not match pending decision",
    )
    offered_ids = {item.id for item in next_state.pending_decision.choices}
    _require(event.option_id in offered_ids, "player choice was not offered")
    next_state = next_state.model_copy(
        update={"pending_scene": None, "pending_decision": None}
    )
```

A terminal `ending` scene is epilogue presentation: it is allowed at normal `scene_count == max_scenes` and does not increment the normal scene count.

- [ ] **Step 5: Add event retrieval**

```python
def load_events(self, session_id: str, after_sequence: int = 0) -> tuple[EventEnvelope, ...]:
    with self._connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM story_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if exists is None:
            raise SessionNotFound(session_id)
        rows = connection.execute(
            """
            SELECT event_json FROM story_events
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence
            """,
            (session_id, after_sequence),
        ).fetchall()
        return tuple(EventEnvelope.model_validate_json(row["event_json"]) for row in rows)
```

Export the new models from `state/__init__.py`.

- [ ] **Step 6: Update existing scene fixtures and run tests**

Every existing `SceneCommitted` test fixture must include at least one `NarrativeBlock`. Decision fixtures must include two `PresentedChoice` objects.

Run: `cd backend && uv run pytest tests/test_story_state.py tests/test_story_reducer.py tests/test_story_event_store.py -q`

Expected: PASS.

- [ ] **Step 7: Commit persisted presentation**

```bash
git add backend/src/story/state backend/src/story/storage backend/tests
git commit -m "feat: persist v2 scenes and allowed choices"
```

---

### Task 4: Define Strict Runtime Contracts and Ports

**Files:**
- Create: `backend/src/story/runtime/__init__.py`
- Create: `backend/src/story/runtime/contracts.py`
- Test: `backend/tests/test_runtime_contracts.py`

- [ ] **Step 1: Write failing contract tests**

```python
import pytest
from pydantic import ValidationError

from src.story.runtime.contracts import ChoicePlan, PlannerOutput, ScenePlan


def test_scene_plan_requires_two_choices_for_decision():
    with pytest.raises(ValidationError, match="choices"):
        ScenePlan(
            scene_id="scene_01",
            summary="Alice confronts the protagonist.",
            location_id="cafe",
            present_character_ids=("alice",),
            terminal="decision",
            decision_id="decision_01",
            choices=(ChoicePlan(option_id="ask", action_id="ask", intent="ask"),),
        )


def test_planner_output_is_discriminated():
    plan = ScenePlan(
        scene_id="scene_01",
        summary="The protagonist takes in the cafe.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="continue",
    )
    output = PlannerOutput(kind="scene", scene=plan)
    assert output.scene is not None
    assert output.resolution is None
```

- [ ] **Step 2: Run the test and verify import failure**

Run: `cd backend && uv run pytest tests/test_runtime_contracts.py -q`

Expected: FAIL because `src.story.runtime` does not exist.

- [ ] **Step 3: Implement strict contracts**

Define these frozen, `extra="forbid"` Pydantic models in `contracts.py`:

```python
class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelContractError(RuntimeError):
    pass


class ChoicePlan(RuntimeModel):
    option_id: str
    action_id: str
    intent: str
    target_character_id: str | None = None


class FactCommitPlan(RuntimeModel):
    fact_id: str
    value: str
    reason: Literal["first_irreversible_evidence", "explicit_revelation"]
    reveal: bool = False
    learned_by: tuple[str, ...] = ()


class ScenePlan(RuntimeModel):
    scene_id: str
    summary: str
    location_id: str
    present_character_ids: tuple[str, ...]
    focus_goal_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
    fact_commits: tuple[FactCommitPlan, ...] = ()
    terminal: Literal["continue", "decision"]
    decision_id: str | None = None
    choices: tuple[ChoicePlan, ...] = ()

    @model_validator(mode="after")
    def validate_terminal(self) -> ScenePlan:
        if self.terminal == "decision":
            if self.decision_id is None or not 2 <= len(self.choices) <= 4:
                raise ValueError("decision scenes require decision_id and 2-4 choices")
        elif self.decision_id is not None or self.choices:
            raise ValueError("continue scenes cannot contain a decision")
        return self


class RelationshipDelta(RuntimeModel):
    character_id: str
    axis: str
    delta: int


class GoalDelta(RuntimeModel):
    goal_id: str
    delta: float


class ActionResolution(RuntimeModel):
    action_id: str
    outcome: Literal["success", "partial", "resisted", "backfire"]
    relationship_deltas: tuple[RelationshipDelta, ...] = ()
    goal_deltas: tuple[GoalDelta, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()
    reveal_fact_ids: tuple[str, ...] = ()
    learned_facts: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class WrittenChoice(RuntimeModel):
    option_id: str
    label: str
    preview: str | None = None


class SceneDraft(RuntimeModel):
    scene_id: str
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)
    choices: tuple[WrittenChoice, ...] = ()


class EndingDraft(RuntimeModel):
    ending_id: str
    title: str
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)


class PlannerOutput(RuntimeModel):
    kind: Literal["scene", "resolution"]
    scene: ScenePlan | None = None
    resolution: ActionResolution | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> PlannerOutput:
        if self.kind == "scene" and self.scene is not None and self.resolution is None:
            return self
        if self.kind == "resolution" and self.resolution is not None and self.scene is None:
            return self
        raise ValueError("planner kind must match exactly one payload")


class WriterOutput(RuntimeModel):
    kind: Literal["scene", "ending"]
    scene: SceneDraft | None = None
    ending: EndingDraft | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> WriterOutput:
        if self.kind == "scene" and self.scene is not None and self.ending is None:
            return self
        if self.kind == "ending" and self.ending is not None and self.scene is None:
            return self
        raise ValueError("writer kind must match exactly one payload")


class PlannerPort(Protocol):
    async def plan_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> ScenePlan:
        raise NotImplementedError

    async def resolve_action(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        choice: PresentedChoice,
    ) -> ActionResolution:
        raise NotImplementedError


class WriterPort(Protocol):
    async def write_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: ScenePlan,
    ) -> SceneDraft:
        raise NotImplementedError

    async def write_ending(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        ending: EndingSource,
    ) -> EndingDraft:
        raise NotImplementedError
```

Import `Protocol`, `BaseModel`, `ConfigDict`, `Field`, `model_validator`, `EndingSource`, `CompiledScriptPack`, `NarrativeBlock`, `PresentedChoice`, and `SessionState` at the top of the module. Export the ports, contracts, and `ModelContractError` from `runtime/__init__.py`.

- [ ] **Step 4: Run contract tests**

Run: `cd backend && uv run pytest tests/test_runtime_contracts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit runtime contracts**

```bash
git add backend/src/story/runtime backend/tests/test_runtime_contracts.py
git commit -m "feat: define v2 runtime contracts"
```

---

### Task 5: Build Condition Context, Ending Selection, and Phase Progression

**Files:**
- Create: `backend/src/story/runtime/endings.py`
- Create: `backend/src/story/runtime/context.py`
- Test: `backend/tests/test_runtime_endings.py`
- Test: `backend/tests/test_runtime_context.py`

- [ ] **Step 1: Write failing context and ending tests**

```python
def test_condition_context_matches_compiled_condition_paths():
    state, pack = compiled_state()
    context = build_condition_context(state)
    assert context["relationships"]["alice"]["trust"] == 35
    assert context["facts"]["who_took_notebook"]["truth_status"] == "possible"
    assert context["goals"]["alice_find_ally"]["completed"] is False
    assert context["session"]["scene_count"] == 0


def test_normal_ending_waits_for_minimum_scene_count():
    state, pack = state_with_relationship("alice", "trust", 80)
    assert select_ending(pack, state) is None


def test_fallback_ending_is_selected_at_max_scene_count():
    state, pack = state_at_scene_count(20)
    ending = select_ending(pack, state)
    assert ending is not None
    assert ending.type == "fallback"
```

- [ ] **Step 2: Run tests and verify missing modules fail**

Run: `cd backend && uv run pytest tests/test_runtime_context.py tests/test_runtime_endings.py -q`

Expected: FAIL with missing `context`/`endings` imports.

- [ ] **Step 3: Implement the canonical condition context**

```python
def build_condition_context(state: SessionState) -> dict[str, Any]:
    return {
        "relationships": {key: dict(value) for key, value in state.world.relationships.items()},
        "facts": {
            key: {
                "truth_status": value.truth_status.value,
                "visibility": value.visibility.value,
                "value": value.value,
            }
            for key, value in state.facts.items()
        },
        "goals": {
            key: {
                "status": value.status.value,
                "progress": value.progress,
                "completed": value.completed,
            }
            for key, value in state.world.goals.items()
        },
        "world": {
            "location_id": state.world.location_id,
            "phase": state.world.phase.value,
            "pressure": state.world.pressure,
        },
        "session": {
            "scene_count": state.world.scene_count,
            "revision": state.revision,
            "status": state.status.value,
        },
        "threads": {
            key: {"status": value.status.value, "urgency": value.urgency}
            for key, value in state.threads.items()
        },
    }
```

- [ ] **Step 4: Implement ending group evaluation**

```python
def _group(pack: CompiledScriptPack, ending_id: str, name: str, count: int, context: dict) -> list[bool]:
    return [
        pack.conditions[f"ending.{ending_id}.{name}.{index}"].evaluate(context)
        for index in range(count)
    ]


def select_ending(pack: CompiledScriptPack, state: SessionState) -> EndingSource | None:
    context = build_condition_context(state)
    at_max = state.world.scene_count >= state.world.max_scenes
    for ending in sorted(pack.source.endings, key=lambda item: item.priority, reverse=True):
        if ending.type == "fallback" and not at_max:
            continue
        if ending.type != "fallback" and state.world.scene_count < pack.source.experience.min_scenes:
            continue
        all_values = _group(pack, ending.id, "all", len(ending.eligibility.all), context)
        any_values = _group(pack, ending.id, "any", len(ending.eligibility.any), context)
        none_values = _group(pack, ending.id, "none", len(ending.eligibility.none), context)
        if all(all_values) and (not any_values or any(any_values)) and not any(none_values):
            return ending
    return None
```

Add `next_phase(state)` that returns at most the next enum member:

```python
PHASES = (
    StoryPhase.OPENING,
    StoryPhase.EXPLORATION,
    StoryPhase.ESCALATION,
    StoryPhase.CRISIS,
    StoryPhase.RESOLUTION,
)


def next_phase(state: SessionState) -> StoryPhase | None:
    usable = max(1, state.world.max_scenes - state.world.reserved_resolution_scenes)
    ratio = min(1.0, (state.world.scene_count + 1) / usable)
    if ratio >= 0.70:
        target = StoryPhase.CRISIS
    elif ratio >= 0.45:
        target = StoryPhase.ESCALATION
    elif ratio >= 0.20:
        target = StoryPhase.EXPLORATION
    else:
        target = StoryPhase.OPENING
    current_index = PHASES.index(state.world.phase)
    target_index = PHASES.index(target)
    return PHASES[current_index + 1] if target_index > current_index else None
```

- [ ] **Step 5: Run ending/context tests**

Run: `cd backend && uv run pytest tests/test_runtime_context.py tests/test_runtime_endings.py -q`

Expected: PASS.

- [ ] **Step 6: Commit deterministic progression rules**

```bash
git add backend/src/story/runtime backend/tests/test_runtime_context.py backend/tests/test_runtime_endings.py
git commit -m "feat: evaluate v2 context phases and endings"
```

---

### Task 6: Assemble Truth-Safe Planner and Writer Contexts

**Files:**
- Modify: `backend/src/story/runtime/context.py`
- Test: `backend/tests/test_runtime_context.py`

- [ ] **Step 1: Add failing tests for knowledge separation**

```python
def test_writer_context_does_not_give_alice_bobs_private_fact():
    state, pack = cafe_state()
    plan = ScenePlan(
        scene_id="scene_01",
        summary="Alice studies the protagonist's reaction.",
        location_id=state.world.location_id,
        present_character_ids=("alice",),
        terminal="continue",
    )
    context = build_writer_context(
        pack,
        state,
        present_character_ids=("alice",),
        approved_plan=plan,
    )
    alice = context["characters"][0]
    assert all(item["id"] != "bob_has_org_history" for item in alice["known_facts"])
    assert "鲍勃过去曾因隐环遭受损失" not in json.dumps(alice, ensure_ascii=False)


def test_possible_latent_fact_exposes_question_but_not_candidate_answer():
    state, pack = cafe_state()
    context = build_planner_context(pack, state)
    fact = next(item for item in context["facts"] if item["id"] == "notebook_holder")
    assert fact["question"] == "现在谁持有笔记本？"
    assert "value" not in fact
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd backend && uv run pytest tests/test_runtime_context.py -q`

Expected: FAIL because the model-facing context functions do not exist.

- [ ] **Step 3: Implement planner context without inventing truth**

Use explicit fact views so an uncommitted latent answer never appears as truth:

```python
def _planner_fact_views(pack, state) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for fact in pack.source.facts.fixed:
        runtime = state.facts[fact.id]
        views.append(
            {
                "id": fact.id,
                "kind": "fixed",
                "statement": fact.statement,
                "visibility": runtime.visibility.value,
                "known_by": sorted(runtime.known_by),
            }
        )
    for question in pack.source.facts.latent_questions:
        runtime = state.facts[question.id]
        view: dict[str, Any] = {
            "id": question.id,
            "kind": "latent",
            "question": question.question,
            "truth_status": runtime.truth_status.value,
            "visibility": runtime.visibility.value,
            "evidence_required": runtime.evidence_required,
            "evidence_count": len(runtime.evidence_event_ids),
        }
        if runtime.truth_status == FactTruthStatus.COMMITTED:
            view["value"] = runtime.value
        else:
            view["candidates"] = [
                {"value": item.value, "requirements": item.requirements}
                for item in question.candidates
            ]
        views.append(view)
    condition_context = build_condition_context(state)
    for fact in pack.source.facts.derived:
        views.append(
            {
                "id": fact.id,
                "kind": "derived",
                "value": pack.conditions[f"fact.{fact.id}.derived"].evaluate(condition_context),
            }
        )
    return views


def build_planner_context(pack, state) -> dict[str, Any]:
    source = pack.source
    characters = []
    for character in source.characters:
        runtime = state.characters[character.id]
        characters.append(
            {
                "id": character.id,
                "name": character.name,
                "public_profile": character.public_profile,
                "personality": character.personality.model_dump(mode="json"),
                "voice": character.voice.model_dump(mode="json"),
                "drives": character.drives,
                "boundaries": character.boundaries.model_dump(mode="json"),
                "capabilities": character.capabilities,
                "relationship": state.world.relationships[character.id],
                "emotional_state": runtime.emotional_state,
                "known_fact_ids": sorted(runtime.knowledge),
                "beliefs": {
                    key: value.model_dump(mode="json") for key, value in runtime.beliefs.items()
                },
                "suspicions": {
                    key: value.model_dump(mode="json")
                    for key, value in runtime.suspicions.items()
                },
            }
        )
    return {
        "pack": {
            "id": source.identity.id,
            "language": source.identity.language,
            "viewpoint": source.experience.viewpoint,
            "prose_style": source.experience.prose_style,
            "tone": source.experience.tone,
            "premise": source.world.premise,
            "immutable_rules": source.world.immutable_rules,
            "forbidden_content": source.experience.forbidden_content,
        },
        "state": build_condition_context(state),
        "facts": _planner_fact_views(pack, state),
        "characters": characters,
        "available_action_ids": sorted(
            pack.action_ids & set(source.protagonist.capabilities)
        ),
        "goals": [goal.model_dump(mode="json") for goal in source.goals],
    }
```

- [ ] **Step 4: Implement writer context with per-character knowledge**

Build each present character independently and resolve only facts that character already knows:

```python
def _known_fact_view(pack, state, fact_id: str) -> dict[str, Any] | None:
    runtime = state.facts.get(fact_id)
    if runtime is None or runtime.truth_status != FactTruthStatus.COMMITTED:
        return None
    fixed = next((item for item in pack.source.facts.fixed if item.id == fact_id), None)
    return {
        "id": fact_id,
        "value": fixed.statement if fixed is not None else runtime.value,
        "visibility": runtime.visibility.value,
    }


def build_writer_context(
    pack,
    state,
    present_character_ids,
    approved_plan,
) -> dict[str, Any]:
    sources = {item.id: item for item in pack.source.characters}
    characters = []
    for character_id in present_character_ids:
        source = sources[character_id]
        runtime = state.characters[character_id]
        known_facts = [
            view
            for fact_id in sorted(runtime.knowledge)
            if (view := _known_fact_view(pack, state, fact_id)) is not None
        ]
        characters.append(
            {
                "id": character_id,
                "name": source.name,
                "public_profile": source.public_profile,
                "personality": source.personality.model_dump(mode="json"),
                "voice": source.voice.model_dump(mode="json"),
                "drives": source.drives,
                "boundaries": source.boundaries.model_dump(mode="json"),
                "relationship": state.world.relationships[character_id],
                "emotional_state": runtime.emotional_state,
                "known_facts": known_facts,
                "beliefs": {
                    key: value.model_dump(mode="json") for key, value in runtime.beliefs.items()
                },
                "suspicions": {
                    key: value.model_dump(mode="json")
                    for key, value in runtime.suspicions.items()
                },
            }
        )
    approved_fact_ids = set(approved_plan.related_fact_ids)
    approved_fact_ids.update(item.fact_id for item in approved_plan.fact_commits)
    narration_facts = [
        item
        for item in _planner_fact_views(pack, state)
        if item["id"] in approved_fact_ids
    ]
    return {
        "language": pack.source.identity.language,
        "viewpoint": pack.source.experience.viewpoint,
        "prose_style": pack.source.experience.prose_style,
        "tone": pack.source.experience.tone,
        "forbidden_content": pack.source.experience.forbidden_content,
        "approved_plan": approved_plan.model_dump(mode="json"),
        "approved_narration_facts": narration_facts,
        "characters": characters,
    }


def build_ending_context(pack, state, ending) -> dict[str, Any]:
    revealed_facts = []
    fixed = {item.id: item for item in pack.source.facts.fixed}
    for fact_id, runtime in state.facts.items():
        if runtime.visibility != FactVisibility.REVEALED:
            continue
        source = fixed.get(fact_id)
        revealed_facts.append(
            {
                "id": fact_id,
                "value": source.statement if source is not None else runtime.value,
            }
        )
    return {
        "language": pack.source.identity.language,
        "viewpoint": pack.source.experience.viewpoint,
        "prose_style": pack.source.experience.prose_style,
        "tone": pack.source.experience.tone,
        "ending": ending.model_dump(mode="json"),
        "relationships": {
            key: dict(value) for key, value in state.world.relationships.items()
        },
        "goals": {
            key: value.model_dump(mode="json") for key, value in state.world.goals.items()
        },
        "revealed_facts": revealed_facts,
        "characters": [
            {
                "id": item.id,
                "name": item.name,
                "public_profile": item.public_profile,
                "voice": item.voice.model_dump(mode="json"),
                "boundaries": item.boundaries.model_dump(mode="json"),
            }
            for item in pack.source.characters
        ],
    }
```

Import `Any`, `FactTruthStatus`, and `FactVisibility`. The scene Writer may narrate only `approved_narration_facts`; dialogue uses the matching character entry and cannot read another character's `known_facts`. The ending Writer receives only already revealed facts plus Kernel-fixed ending obligations, never hidden committed values.

- [ ] **Step 5: Run context tests**

Run: `cd backend && uv run pytest tests/test_runtime_context.py -q`

Expected: PASS.

- [ ] **Step 6: Commit context assembly**

```bash
git add backend/src/story/runtime/context.py backend/tests/test_runtime_context.py
git commit -m "feat: assemble truth-safe v2 model context"
```

---

### Task 7: Validate and Simulate Model Proposals

**Files:**
- Create: `backend/src/story/runtime/validator.py`
- Create: `backend/src/story/runtime/simulator.py`
- Create: `backend/src/story/runtime/fallbacks.py`
- Test: `backend/tests/test_runtime_validator.py`
- Test: `backend/tests/test_runtime_simulator.py`

- [ ] **Step 1: Write failing validator tests**

```python
def test_plan_rejects_unknown_character_and_action():
    state, pack = compiled_state()
    plan = valid_decision_plan().model_copy(
        update={
            "present_character_ids": ("invented",),
            "choices": (
                ChoicePlan(option_id="x", action_id="hack", intent="cheat"),
                ChoicePlan(option_id="y", action_id="observe", intent="watch"),
            ),
        }
    )
    with pytest.raises(ProposalRejected) as exc:
        validate_scene_plan(pack, state, plan)
    assert "unknown character" in str(exc.value)
    assert "unavailable action" in str(exc.value)


def test_resolution_rejects_out_of_bounds_relationship_change():
    state, pack = compiled_state()
    resolution = ActionResolution(
        action_id="ask",
        outcome="success",
        relationship_deltas=(RelationshipDelta(character_id="alice", axis="trust", delta=50),),
    )
    with pytest.raises(ProposalRejected, match="relationship delta"):
        validate_action_resolution(pack, state, resolution)
```

Add these simulator assertions:

```python
def test_scene_simulation_applies_complete_batch_without_writing_store():
    state, pack = compiled_state()
    plan = valid_decision_plan()
    draft = valid_scene_draft(plan)
    events = simulate_scene(pack, state, plan, draft)
    assert isinstance(events[-1], SceneCommitted)
    assert [item.id for item in events[-1].choices] == [item.option_id for item in plan.choices]
    assert state.revision == 0


def test_resolution_effect_events_have_deterministic_order():
    state, _ = decision_state()
    choice = state.pending_decision.choices[0]
    resolution = ActionResolution(
        action_id=choice.action_id,
        outcome="success",
        relationship_deltas=(RelationshipDelta(character_id="alice", axis="trust", delta=3),),
        goal_deltas=(GoalDelta(goal_id="alice_find_ally", delta=0.1),),
        reveal_fact_ids=("cafe_is_open",),
        learned_facts={"alice": ("cafe_is_open",)},
    )
    events = simulate_resolution(state, choice, resolution, "request-01")
    assert [event.type for event in events] == [
        "player_action_selected",
        "action_resolved",
        "relationship_changed",
        "goal_advanced",
        "fact_revealed",
        "character_learned_fact",
    ]


def test_resolution_can_add_final_evidence_then_reveal():
    state, pack = committed_fact_decision_state(
        fact_id="who_took_notebook",
        evidence_event_ids=("scene:evidence:1",),
        evidence_required=2,
    )
    choice = state.pending_decision.choices[0]
    resolution = ActionResolution(
        action_id=choice.action_id,
        outcome="success",
        evidence_fact_ids=("who_took_notebook",),
        reveal_fact_ids=("who_took_notebook",),
    )
    validate_action_resolution(pack, state, resolution, expected_action_id=choice.action_id)
    events = simulate_resolution(state, choice, resolution, "request-02")
    assert [event.type for event in events][-2:] == ["fact_evidenced", "fact_revealed"]
```

- [ ] **Step 2: Run tests and verify missing modules fail**

Run: `cd backend && uv run pytest tests/test_runtime_validator.py tests/test_runtime_simulator.py -q`

Expected: FAIL with missing runtime modules.

- [ ] **Step 3: Implement scene-plan validation**

```python
class ProposalRejected(ValueError):
    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


def validate_scene_plan(pack: CompiledScriptPack, state: SessionState, plan: ScenePlan) -> ScenePlan:
    errors: list[str] = []
    location_ids = {item.id for item in pack.source.world.locations}
    if plan.location_id not in location_ids:
        errors.append(f"unknown location: {plan.location_id}")
    errors.extend(
        f"unknown character: {item}"
        for item in plan.present_character_ids
        if item not in pack.character_ids
    )
    errors.extend(f"unknown goal: {item}" for item in plan.focus_goal_ids if item not in pack.goal_ids)
    errors.extend(f"unknown fact: {item}" for item in plan.related_fact_ids if item not in pack.fact_ids)
    allowed_actions = pack.action_ids & set(pack.source.protagonist.capabilities)
    errors.extend(
        f"unavailable action: {choice.action_id}"
        for choice in plan.choices
        if choice.action_id not in allowed_actions
    )
    option_ids = [item.option_id for item in plan.choices]
    if len(option_ids) != len(set(option_ids)):
        errors.append("choice option ids must be unique")
    errors.extend(_validate_fact_commits(pack, state, plan.fact_commits))
    if errors:
        raise ProposalRejected(errors)
    return plan
```

Implement fact-commit validation with the compiled candidate requirement keys:

```python
def _validate_fact_commits(pack, state, commits) -> list[str]:
    errors: list[str] = []
    context = build_condition_context(state)
    seen: set[str] = set()
    questions = {item.id: item for item in pack.source.facts.latent_questions}
    for commit in commits:
        if commit.fact_id in seen:
            errors.append(f"duplicate fact commit: {commit.fact_id}")
            continue
        seen.add(commit.fact_id)
        current = state.facts.get(commit.fact_id)
        question = questions.get(commit.fact_id)
        if current is None or question is None or current.truth_status != FactTruthStatus.POSSIBLE:
            errors.append(f"fact is not an open latent question: {commit.fact_id}")
            continue
        candidate = next((item for item in question.candidates if item.value == commit.value), None)
        if candidate is None:
            errors.append(f"unknown candidate value: {commit.fact_id}.{commit.value}")
            continue
        if commit.reason not in question.commit_when:
            errors.append(f"commit reason is not allowed: {commit.fact_id}.{commit.reason}")
        if commit.reason == "explicit_revelation" and not commit.reveal:
            errors.append(f"explicit revelation must reveal the fact: {commit.fact_id}")
        for index in range(len(candidate.requirements)):
            key = f"fact.{commit.fact_id}.candidate.{commit.value}.requirement.{index}"
            if not pack.conditions[key].evaluate(context):
                errors.append(f"candidate requirement is false: {commit.fact_id}.{commit.value}.{index}")
        unknown_learners = set(commit.learned_by) - pack.character_ids
        errors.extend(f"unknown character: {item}" for item in sorted(unknown_learners))
        if commit.reveal and question.evidence_required > 1:
            errors.append(f"fact cannot be revealed by one scene: {commit.fact_id}")
    return errors
```

- [ ] **Step 4: Implement action bounds**

For standard actions, allow relationship deltas only within `[-10, 10]` and goal deltas within `[-0.15, 0.25]`; do not allow fact commitment. For extension actions, use the extension's exact `EffectBoundsSource`. Reject unknown characters, axes not present in current relationships, unknown goals/facts, duplicate reveal/learn entries, and reveals lacking committed truth/evidence.

```python
def validate_action_resolution(
    pack: CompiledScriptPack,
    state: SessionState,
    resolution: ActionResolution,
    expected_action_id: str | None = None,
) -> ActionResolution:
    if resolution.action_id not in pack.action_ids:
        raise ProposalRejected([f"unavailable action: {resolution.action_id}"])
    errors: list[str] = []
    if expected_action_id is not None and resolution.action_id != expected_action_id:
        errors.append(
            f"resolution action mismatch: expected {expected_action_id}, got {resolution.action_id}"
        )
    extension = next(
        (item for item in pack.source.interaction_rules.extensions if item.id == resolution.action_id),
        None,
    )
    if extension is not None:
        context = build_condition_context(state)
        for index in range(len(extension.preconditions)):
            key = f"action.{extension.id}.precondition.{index}"
            if not pack.conditions[key].evaluate(context):
                errors.append(f"action precondition is false: {extension.id}.{index}")
    for item in resolution.relationship_deltas:
        axes = state.world.relationships.get(item.character_id)
        if axes is None:
            errors.append(f"unknown relationship character: {item.character_id}")
            continue
        if item.axis not in axes:
            errors.append(f"unknown relationship axis: {item.character_id}.{item.axis}")
            continue
        bounds = (-10, 10) if extension is None else extension.effects.relationship_axes.get(item.axis)
        if bounds is None or not bounds[0] <= item.delta <= bounds[1]:
            errors.append(f"relationship delta out of bounds: {item.character_id}.{item.axis}")
    relationship_keys = [(item.character_id, item.axis) for item in resolution.relationship_deltas]
    if len(relationship_keys) != len(set(relationship_keys)):
        errors.append("relationship deltas must target unique character axes")
    goal_bounds = (-0.15, 0.25) if extension is None else extension.effects.goal_progress
    for item in resolution.goal_deltas:
        if item.goal_id not in state.world.goals:
            errors.append(f"unknown goal: {item.goal_id}")
        elif not goal_bounds[0] <= item.delta <= goal_bounds[1]:
            errors.append(f"goal delta out of bounds: {item.goal_id}")
    goal_ids = [item.goal_id for item in resolution.goal_deltas]
    if len(goal_ids) != len(set(goal_ids)):
        errors.append("goal deltas must target unique goals")
    if len(resolution.evidence_fact_ids) != len(set(resolution.evidence_fact_ids)):
        errors.append("evidenced fact ids must be unique")
    if len(resolution.evidence_fact_ids) > 1:
        errors.append("one action can add evidence to at most one fact")
    for fact_id in resolution.evidence_fact_ids:
        fact = state.facts.get(fact_id)
        if fact is None:
            errors.append(f"unknown fact: {fact_id}")
        elif fact.truth_status != FactTruthStatus.COMMITTED:
            errors.append(f"cannot evidence uncommitted fact: {fact_id}")
        elif fact.visibility == FactVisibility.REVEALED:
            errors.append(f"cannot add evidence to revealed fact: {fact_id}")
    for fact_id in resolution.reveal_fact_ids:
        fact = state.facts.get(fact_id)
        if fact is None:
            errors.append(f"unknown fact: {fact_id}")
        elif fact.truth_status != FactTruthStatus.COMMITTED:
            errors.append(f"cannot reveal uncommitted fact: {fact_id}")
        elif (
            len(fact.evidence_event_ids)
            + (1 if fact_id in resolution.evidence_fact_ids else 0)
            < fact.evidence_required
        ):
            errors.append(f"fact lacks evidence: {fact_id}")
    if len(resolution.reveal_fact_ids) != len(set(resolution.reveal_fact_ids)):
        errors.append("revealed fact ids must be unique")
    for character_id, fact_ids in resolution.learned_facts.items():
        if character_id not in state.characters:
            errors.append(f"unknown character: {character_id}")
        for fact_id in fact_ids:
            fact = state.facts.get(fact_id)
            if fact is None or fact.truth_status != FactTruthStatus.COMMITTED:
                errors.append(f"character cannot learn unavailable fact: {character_id}.{fact_id}")
        if len(fact_ids) != len(set(fact_ids)):
            errors.append(f"learned fact ids must be unique: {character_id}")
    if errors:
        raise ProposalRejected(errors)
    return resolution
```

Import `FactTruthStatus` and `FactVisibility` from state models. Evidence may advance only one already committed, unrevealed fact per player action; a reveal can consume the evidence added by that same atomic batch.

- [ ] **Step 5: Implement draft validation and event simulation**

Implement draft validation before event conversion:

```python
def validate_scene_draft(plan: ScenePlan, draft: SceneDraft) -> SceneDraft:
    errors: list[str] = []
    if draft.scene_id != plan.scene_id:
        errors.append(f"scene id mismatch: expected {plan.scene_id}, got {draft.scene_id}")
    if not draft.blocks or any(not block.text.strip() for block in draft.blocks):
        errors.append("scene draft requires non-empty blocks")
    for block in draft.blocks:
        if block.kind == "dialogue" and block.character_id not in plan.present_character_ids:
            errors.append(f"dialogue speaker is not present: {block.character_id}")
    planned_ids = [item.option_id for item in plan.choices]
    written_ids = [item.option_id for item in draft.choices]
    if set(written_ids) != set(planned_ids) or len(written_ids) != len(planned_ids):
        errors.append("written choice ids must exactly match planned choice ids")
    normalized_labels = [item.label.strip().casefold() for item in draft.choices]
    if any(not label for label in normalized_labels):
        errors.append("written choice labels cannot be empty")
    if len(normalized_labels) != len(set(normalized_labels)):
        errors.append("written choice labels must be unique")
    if plan.terminal == "decision" and not 2 <= len(draft.choices) <= 4:
        errors.append("decision draft requires 2-4 choices")
    if plan.terminal == "continue" and draft.choices:
        errors.append("continue draft cannot contain choices")
    if errors:
        raise ProposalRejected(errors)
    return draft
```

`simulate_scene(pack, state, plan, draft)` must build events, apply them to a copied state with synthetic envelopes, and return the event tuple only if the reducer accepts the complete batch:

```python
def scene_events(pack, state, plan, draft) -> tuple[StoryEvent, ...]:
    events: list[StoryEvent] = []
    phase = next_phase(state)
    if phase is not None:
        events.append(PhaseAdvanced(phase=phase))
    for fact in plan.fact_commits:
        evidence = (
            (plan.scene_id,)
            if fact.reason == "first_irreversible_evidence" or fact.reveal
            else ()
        )
        events.append(FactCommitted(fact_id=fact.fact_id, value=fact.value, evidence_event_ids=evidence))
        if fact.reveal:
            events.append(FactRevealed(fact_id=fact.fact_id))
        events.extend(
            CharacterLearnedFact(character_id=character_id, fact_id=fact.fact_id)
            for character_id in fact.learned_by
        )
    written = {item.option_id: item for item in draft.choices}
    choices = tuple(
        PresentedChoice(
            id=item.option_id,
            action_id=item.action_id,
            label=written[item.option_id].label,
            intent=item.intent,
            target_character_id=item.target_character_id,
            preview=written[item.option_id].preview,
        )
        for item in plan.choices
    )
    events.append(
        SceneCommitted(
            scene_id=plan.scene_id,
            terminal=plan.terminal,
            location_id=plan.location_id,
            present_character_ids=plan.present_character_ids,
            blocks=draft.blocks,
            decision_id=plan.decision_id,
            choices=choices,
        )
    )
    return tuple(events)


def simulate_events(state: SessionState, events: tuple[StoryEvent, ...]) -> None:
    envelopes = tuple(
        EventEnvelope(
            event_id=f"simulation-{state.revision + index}",
            session_id=state.session_id,
            sequence=state.revision + index,
            event=event,
        )
        for index, event in enumerate(events, start=1)
    )
    candidate = apply_events(state, envelopes)
    if candidate.world.scene_count > candidate.world.max_scenes:
        raise StateTransitionError("simulation exceeded max scene count")


def simulate_scene(pack, state, plan, draft) -> tuple[StoryEvent, ...]:
    events = scene_events(pack, state, plan, draft)
    simulate_events(state, events)
    return events


def resolution_effect_events(
    state: SessionState,
    resolution: ActionResolution,
) -> tuple[StoryEvent, ...]:
    events: list[StoryEvent] = [
        RelationshipChanged(
            character_id=item.character_id,
            axis=item.axis,
            delta=item.delta,
        )
        for item in resolution.relationship_deltas
    ]
    events.extend(
        GoalAdvanced(goal_id=item.goal_id, delta=item.delta)
        for item in resolution.goal_deltas
    )
    events.extend(
        FactEvidenced(
            fact_id=fact_id,
            evidence_event_id=f"action:{state.session_id}:{state.revision + 1}:{fact_id}",
        )
        for fact_id in resolution.evidence_fact_ids
    )
    events.extend(FactRevealed(fact_id=fact_id) for fact_id in resolution.reveal_fact_ids)
    for character_id in sorted(resolution.learned_facts):
        events.extend(
            CharacterLearnedFact(character_id=character_id, fact_id=fact_id)
            for fact_id in sorted(resolution.learned_facts[character_id])
        )
    return tuple(events)


def simulate_resolution(
    state: SessionState,
    choice: PresentedChoice,
    resolution: ActionResolution,
    idempotency_key: str,
) -> tuple[StoryEvent, ...]:
    if state.pending_decision is None:
        raise StateTransitionError("no decision is pending")
    events: tuple[StoryEvent, ...] = (
        PlayerActionSelected(
            decision_id=state.pending_decision.decision_id,
            option_id=choice.id,
            idempotency_key=idempotency_key,
        ),
        ActionResolved(action_id=resolution.action_id, outcome=resolution.outcome),
        *resolution_effect_events(state, resolution),
    )
    simulate_events(state, events)
    return events
```

Import every event used above plus `EventEnvelope`, `SessionState`, `StateTransitionError`, and `apply_events`. These helpers return events only after the pure reducer accepts the complete synthetic batch; they never write the event store.

- [ ] **Step 6: Add deterministic fallbacks**

Implement deterministic fallbacks without model calls:

```python
def fallback_scene_plan(pack, state) -> ScenePlan:
    actions = sorted(pack.action_ids & set(pack.source.protagonist.capabilities))
    decision = len(actions) >= 2
    return ScenePlan(
        scene_id=f"fallback_scene_{state.world.scene_count + 1}",
        summary="The protagonist pauses and chooses a safe next action.",
        location_id=state.world.location_id,
        present_character_ids=state.world.present_character_ids,
        terminal="decision" if decision else "continue",
        decision_id=f"fallback_decision_{state.revision + 1}" if decision else None,
        choices=tuple(
            ChoicePlan(option_id=f"fallback_{action}", action_id=action, intent=action)
            for action in actions[:2]
        ),
    )


def fallback_resolution(choice: PresentedChoice) -> ActionResolution:
    return ActionResolution(action_id=choice.action_id, outcome="partial")


def fallback_scene_draft(plan: ScenePlan) -> SceneDraft:
    return SceneDraft(
        scene_id=plan.scene_id,
        blocks=(NarrativeBlock(kind="narration", text="片刻沉默后，故事继续向前。"),),
        choices=tuple(
            WrittenChoice(option_id=item.option_id, label=item.intent[:80]) for item in plan.choices
        ),
    )


def fallback_ending_draft(ending: EndingSource) -> EndingDraft:
    text = " ".join((ending.title, *ending.required_outcomes))
    return EndingDraft(
        ending_id=ending.id,
        title=ending.title,
        blocks=(NarrativeBlock(kind="narration", text=text[:4000]),),
    )
```

- [ ] **Step 7: Run validator/simulator tests**

Run: `cd backend && uv run pytest tests/test_runtime_validator.py tests/test_runtime_simulator.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the rule gate**

```bash
git add backend/src/story/runtime backend/tests/test_runtime_validator.py backend/tests/test_runtime_simulator.py
git commit -m "feat: validate and simulate v2 story proposals"
```

---

### Task 8: Implement Scene Advance and Choice Resolution Services

**Files:**
- Create: `backend/src/story/runtime/service.py`
- Modify: `backend/src/story/runtime/contracts.py`
- Test: `backend/tests/test_runtime_service.py`

- [ ] **Step 1: Write failing service tests with fake ports**

```python
class FakePlanner:
    async def plan_scene(self, pack, state):
        return valid_decision_plan()

    async def resolve_action(self, pack, state, choice):
        return ActionResolution(action_id=choice.action_id, outcome="success")


class FakeWriter:
    async def write_scene(self, pack, state, plan):
        return valid_scene_draft(plan)

    async def write_ending(self, pack, state, ending):
        return valid_ending_draft(ending)


@pytest.mark.asyncio
async def test_advance_commits_scene_and_persists_choices(tmp_path):
    service, pack, store = service_fixture(tmp_path, FakePlanner(), FakeWriter())
    result = await service.advance(pack, "session_01", expected_revision=0)
    assert result.scene_id == "scene_01"
    assert len(result.choices) == 2
    assert store.load_session("session_01").pending_decision is not None


@pytest.mark.asyncio
async def test_select_choice_rejects_stale_revision(tmp_path):
    service, pack, _ = decision_service_fixture(tmp_path)
    with pytest.raises(RuntimeRevisionConflict):
        await service.select_choice(
            pack,
            "session_01",
            "ask_alice",
            expected_revision=0,
            idempotency_key="stale-request",
        )
```

Add explicit tests for the remaining state transitions:

```python
@pytest.mark.asyncio
async def test_advance_refuses_while_decision_is_pending(tmp_path):
    service, pack, store = decision_service_fixture(tmp_path)
    state = store.load_session("session_01")
    with pytest.raises(DecisionRequired):
        await service.advance(pack, state.session_id, state.revision)


@pytest.mark.asyncio
async def test_select_choice_commits_selection_before_resolution(tmp_path):
    service, pack, store = decision_service_fixture(tmp_path)
    state = store.load_session("session_01")
    await service.select_choice(
        pack,
        state.session_id,
        state.pending_decision.choices[0].id,
        state.revision,
        "request-01",
    )
    event_types = [item.event.type for item in store.load_events(state.session_id)]
    assert event_types[-2:] == ["player_action_selected", "action_resolved"]


@pytest.mark.asyncio
async def test_eligible_ending_commits_atomic_epilogue(tmp_path):
    service, pack, store = ending_service_fixture(tmp_path)
    state = store.load_session("session_01")
    result = await service.advance(pack, state.session_id, state.revision)
    assert result.ending_id == "safe_exit"
    assert store.load_session(state.session_id).status == SessionStatus.ENDED
    assert [item.event.type for item in store.load_events(state.session_id)][-3:] == [
        "ending_entered",
        "scene_committed",
        "session_ended",
    ]
```

- [ ] **Step 2: Run service tests and verify they fail**

Run: `cd backend && uv run pytest tests/test_runtime_service.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Add runtime result contracts and errors**

```python
class RuntimeScene(RuntimeModel):
    session_id: str
    revision: int
    scene_id: str
    blocks: tuple[NarrativeBlock, ...]
    choices: tuple[PresentedChoice, ...] = ()
    ending_id: str | None = None

    @classmethod
    def from_committed(
        cls,
        state: SessionState,
        event: SceneCommitted,
    ) -> RuntimeScene:
        return cls(
            session_id=state.session_id,
            revision=state.revision,
            scene_id=event.scene_id,
            blocks=event.blocks,
            choices=event.choices,
            ending_id=(
                state.ending.ending_id
                if event.terminal == "ending" and state.ending is not None
                else None
            ),
        )


class ActionResult(RuntimeModel):
    session_id: str
    revision: int
    action_id: str
    outcome: str


class RuntimeRevisionConflict(RuntimeError):
    pass


class DecisionRequired(RuntimeError):
    pass


class InvalidChoice(RuntimeError):
    pass


class RuntimeSessionEnded(RuntimeError):
    pass


class PackMismatch(RuntimeError):
    pass
```

- [ ] **Step 4: Implement `RuntimeService.advance`**

The method must:

1. Load state and compare `expected_revision`.
2. Reject ended sessions and pending decisions.
3. If a non-decision scene is pending, append `SceneAcknowledged`, then reload.
4. Evaluate an ending before asking Planner for another scene.
5. Generate, validate, write, validate, simulate, and append one atomic scene batch.
6. Convert the final `SceneCommitted` event into `RuntimeScene`.
7. If append loses a race, retry generation once only for `advance`; otherwise raise `RuntimeRevisionConflict`.

```python
class RuntimeService:
    def __init__(self, store: StoryEventStore, planner: PlannerPort, writer: WriterPort) -> None:
        self.store = store
        self.planner = planner
        self.writer = writer

    def _load_matching(self, pack, session_id, expected_revision):
        state = self.store.load_session(session_id)
        if state.pack_id != pack.source.identity.id or state.pack_hash != pack.pack_hash:
            raise PackMismatch(session_id)
        if state.revision != expected_revision:
            raise RuntimeRevisionConflict(
                f"session {session_id}: expected {expected_revision}, current {state.revision}"
            )
        return state

    async def advance(self, pack, session_id, expected_revision) -> RuntimeScene:
        initial = self._load_matching(pack, session_id, expected_revision)
        for attempt in range(2):
            state = initial if attempt == 0 else self.store.load_session(session_id)
            try:
                return await self._advance_once(pack, state)
            except RevisionConflict as exc:
                if attempt == 1:
                    raise RuntimeRevisionConflict(str(exc)) from exc
        raise AssertionError("advance retry loop exhausted")

    async def _advance_once(self, pack, state) -> RuntimeScene:
        if state.status == SessionStatus.ENDED:
            raise RuntimeSessionEnded(state.session_id)
        if state.pending_decision is not None:
            raise DecisionRequired(state.pending_decision.decision_id)
        if state.pending_scene is not None:
            state, _ = self.store.append(
                state.session_id,
                state.revision,
                [SceneAcknowledged(scene_id=state.pending_scene.scene_id)],
            )
        ending = select_ending(pack, state)
        if ending is not None:
            return await self._commit_ending(pack, state, ending)
        try:
            proposed = await self.planner.plan_scene(pack, state)
            plan = validate_scene_plan(pack, state, proposed)
        except (ModelContractError, ProposalRejected):
            plan = validate_scene_plan(pack, state, fallback_scene_plan(pack, state))
        try:
            written = await self.writer.write_scene(pack, state, plan)
            draft = validate_scene_draft(plan, written)
        except (ModelContractError, ProposalRejected):
            draft = validate_scene_draft(plan, fallback_scene_draft(plan))
        events = simulate_scene(pack, state, plan, draft)
        updated, _ = self.store.append(state.session_id, state.revision, events)
        return RuntimeScene.from_committed(updated, events[-1])
```

- [ ] **Step 5: Implement `select_choice`**

Load state and require exact expected revision, current pending decision, and offered choice ID. Pass the persisted `PresentedChoice` to Planner, validate the resolution against that exact action, simulate the entire batch, and append it atomically:

```python
async def select_choice(
    self,
    pack,
    session_id: str,
    choice_id: str,
    expected_revision: int,
    idempotency_key: str,
) -> ActionResult:
    state = self._load_matching(pack, session_id, expected_revision)
    if state.status == SessionStatus.ENDED:
        raise RuntimeSessionEnded(session_id)
    if state.pending_decision is None:
        raise InvalidChoice("no decision is pending")
    choice = next(
        (item for item in state.pending_decision.choices if item.id == choice_id),
        None,
    )
    if choice is None:
        raise InvalidChoice(f"choice was not offered: {choice_id}")
    try:
        proposed = await self.planner.resolve_action(pack, state, choice)
        resolution = validate_action_resolution(
            pack,
            state,
            proposed,
            expected_action_id=choice.action_id,
        )
    except (ModelContractError, ProposalRejected):
        resolution = validate_action_resolution(
            pack,
            state,
            fallback_resolution(choice),
            expected_action_id=choice.action_id,
        )
    events = simulate_resolution(state, choice, resolution, idempotency_key)
    try:
        updated, _ = self.store.append(session_id, state.revision, events)
    except RevisionConflict as exc:
        raise RuntimeRevisionConflict(str(exc)) from exc
    return ActionResult(
        session_id=session_id,
        revision=updated.revision,
        action_id=resolution.action_id,
        outcome=resolution.outcome,
    )
```

Do not regenerate or reinterpret an unrecognized choice. On a revision conflict, return conflict because the selected decision may no longer exist.

- [ ] **Step 6: Implement ending commit**

Use Writer only for prose. Kernel fixes ending semantics:

```python
async def _commit_ending(self, pack, state, ending) -> RuntimeScene:
    try:
        draft = await self.writer.write_ending(pack, state, ending)
        if draft.ending_id != ending.id:
            raise ModelContractError("writer changed ending id")
    except ModelContractError:
        draft = fallback_ending_draft(ending)
    ending_runtime = EndingRuntime(
        ending_id=ending.id,
        entered_at_revision=state.revision + 1,
        required_payoffs=ending.required_outcomes,
        final_scene_budget=1,
    )
    committed = SceneCommitted(
        scene_id=f"ending_{ending.id}_{state.revision + 1}",
        terminal="ending",
        location_id=state.world.location_id,
        present_character_ids=state.world.present_character_ids,
        blocks=draft.blocks,
    )
    events = (
        EndingEntered(ending=ending_runtime),
        committed,
        SessionEnded(ending_id=ending.id),
    )
    simulate_events(state, events)
    updated, _ = self.store.append(state.session_id, state.revision, events)
    return RuntimeScene.from_committed(updated, committed)
```

Import the errors and contracts from `contracts.py`, the fallback functions, simulator functions, validator functions, state events/models, and `RevisionConflict`. Provider/network/auth exceptions are intentionally not caught here; only contract/domain rejection chooses deterministic fallback.

- [ ] **Step 7: Run service tests**

Run: `cd backend && uv run pytest tests/test_runtime_service.py -q`

Expected: PASS.

- [ ] **Step 8: Commit runtime orchestration**

```bash
git add backend/src/story/runtime backend/tests/test_runtime_service.py
git commit -m "feat: orchestrate v2 scenes choices and endings"
```

---

### Task 9: Connect OpenAI Agents SDK to OpenCode Go Responses

**Files:**
- Create: `backend/src/story/runtime/config.py`
- Create: `backend/src/story/runtime/model.py`
- Create: `backend/src/story/runtime/planner.py`
- Create: `backend/src/story/runtime/writer.py`
- Modify: `backend/src/story/runtime/__init__.py`
- Test: `backend/tests/test_runtime_config.py`
- Test: `backend/tests/test_runtime_model.py`
- Test: `backend/tests/test_runtime_agents.py`

- [ ] **Step 1: Write failing secret/config tests**

```python
def test_opencode_go_settings_use_responses_defaults(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-secret")
    settings = OpenCodeGoSettings.from_env()
    assert settings.provider == "opencode_go"
    assert settings.base_url == "https://opencode.ai/zen/go/v1"
    assert settings.model == "deepseek-v4-flash"
    assert settings.api == "responses"
    assert "test-secret" not in repr(settings)


def test_conflicting_key_aliases_fail(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "opencode_go")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "one")
    monkeypatch.setenv("OPENAI_API_KEY", "two")
    with pytest.raises(ConfigurationError, match="both set with different values"):
        OpenCodeGoSettings.from_env()


def test_non_opencode_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("GAL_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-secret")
    with pytest.raises(ConfigurationError, match="GAL_LLM_PROVIDER must be opencode_go"):
        OpenCodeGoSettings.from_env()
```

- [ ] **Step 2: Run config tests and verify import failure**

Run: `cd backend && uv run pytest tests/test_runtime_config.py -q`

Expected: FAIL because `config.py` does not exist.

- [ ] **Step 3: Implement secret-safe configuration**

```python
class ConfigurationError(RuntimeError):
    pass


class OpenCodeGoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["opencode_go"] = "opencode_go"
    api_key: SecretStr = Field(repr=False)
    base_url: str = "https://opencode.ai/zen/go/v1"
    model: str = "deepseek-v4-flash"
    api: Literal["responses"] = "responses"
    timeout_seconds: float = Field(default=45, gt=0, le=300)
    max_retries: int = Field(default=1, ge=0, le=2)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if not parsed.hostname or (parsed.scheme != "https" and not localhost):
            raise ValueError("base_url must use HTTPS except for localhost tests")
        return value.rstrip("/")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> OpenCodeGoSettings:
        env = os.environ if environ is None else environ
        provider = env.get("GAL_LLM_PROVIDER")
        if provider != "opencode_go":
            raise ConfigurationError("GAL_LLM_PROVIDER must be opencode_go")
        primary = env.get("OPENCODE_GO_API_KEY")
        alias = env.get("OPENAI_API_KEY")
        if primary and alias and primary != alias:
            raise ConfigurationError(
                "OPENCODE_GO_API_KEY and OPENAI_API_KEY are both set with different values"
            )
        key = primary or alias
        if not key:
            raise ConfigurationError("OPENCODE_GO_API_KEY is required")
        api = env.get("GAL_LLM_API", "responses")
        if api != "responses":
            raise ConfigurationError("GAL_LLM_API must be responses")
        return cls(
            provider=provider,
            api_key=SecretStr(key),
            base_url=env.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1").rstrip("/"),
            model=env.get("GAL_LLM_MODEL", "deepseek-v4-flash"),
            api=api,
            timeout_seconds=float(env.get("GAL_LLM_TIMEOUT_SECONDS", "45")),
            max_retries=int(env.get("GAL_LLM_MAX_RETRIES", "1")),
        )
```

Import `urlparse` and `field_validator`. The URL validator enforces transport safety but intentionally permits a future HTTPS OpenCode Go host override.

- [ ] **Step 4: Add failing model-construction tests**

```python
def test_build_model_uses_responses_model(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeResponsesModel:
        def __init__(self, *, model, openai_client):
            self.model = model
            self.openai_client = openai_client

    monkeypatch.setattr(model_module, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(model_module, "OpenAIResponsesModel", FakeResponsesModel)
    bundle = build_model_bundle(test_settings())
    assert bundle.model.model == "deepseek-v4-flash"
    assert captured["base_url"] == "https://opencode.ai/zen/go/v1"
    assert captured["max_retries"] == 1
```

- [ ] **Step 5: Build one shared Responses model**

```python
@dataclass(frozen=True)
class ModelBundle:
    client: AsyncOpenAI
    model: OpenAIResponsesModel


def build_model_bundle(settings: OpenCodeGoSettings) -> ModelBundle:
    set_tracing_disabled(True)
    client = AsyncOpenAI(
        api_key=settings.api_key.get_secret_value(),
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )
    model = OpenAIResponsesModel(model=settings.model, openai_client=client)
    return ModelBundle(client=client, model=model)
```

Do not call `set_default_openai_api("chat_completions")`, do not construct `OpenAIChatCompletionsModel`, and do not log the client or settings object.

- [ ] **Step 6: Add failing Planner/Writer adapter tests**

Monkeypatch `Runner.run` to return objects whose `final_output` is `PlannerOutput` or `WriterOutput`. Verify:

```python
async def test_planner_uses_one_agent_for_scene_and_resolution(shared_model):
    planner = SdkPlanner(shared_model)
    scene = await planner.plan_scene(pack, state)
    resolution = await planner.resolve_action(pack, decision_state, offered_choice)
    assert scene.scene_id == "scene_01"
    assert resolution.action_id == offered_choice.action_id
    assert planner.agent.model is shared_model


async def test_writer_uses_same_model_instance(shared_model):
    writer = SdkWriter(shared_model)
    assert writer.agent.model is shared_model


async def test_contract_error_retries_once_without_chat_fallback(monkeypatch, shared_model):
    calls = 0

    async def fake_run(agent, input):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelBehaviorError("invalid structured output")
        return SimpleNamespace(final_output=valid_planner_output())

    monkeypatch.setattr(Runner, "run", fake_run)
    planner = SdkPlanner(shared_model)
    await planner.plan_scene(pack, state)
    assert calls == 2
```

- [ ] **Step 7: Implement the Planner Agent**

```python
PLANNER_INSTRUCTIONS = """You are the semantic planner for a constrained visual novel.
Return only the requested structured contract. Propose events and action outcomes; never claim
that state has changed. Use only IDs, locations, characters, goals, facts, candidate values, and
actions supplied in the input. Never choose a latent fact value outside its candidates. Do not
write narration or dialogue. The validator and reducer are the only state authority."""


class SdkPlanner:
    def __init__(self, model: OpenAIResponsesModel) -> None:
        self.agent = Agent(
            name="V2 Narrative Planner",
            instructions=PLANNER_INSTRUCTIONS,
            model=model,
            output_type=PlannerOutput,
        )

    async def plan_scene(self, pack, state) -> ScenePlan:
        prompt = json.dumps(
            {"operation": "plan_scene", "context": build_planner_context(pack, state)},
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, PlannerOutput)
        if output.kind != "scene" or output.scene is None:
            raise ModelContractError("planner returned non-scene output")
        return output.scene

    async def resolve_action(self, pack, state, choice) -> ActionResolution:
        prompt = json.dumps(
            {
                "operation": "resolve_action",
                "choice": choice.model_dump(mode="json"),
                "context": build_planner_context(pack, state),
            },
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, PlannerOutput)
        if output.kind != "resolution" or output.resolution is None:
            raise ModelContractError("planner returned non-resolution output")
        return output.resolution
```

The prompt above is a domain constraint; all returned IDs and effects still pass through deterministic validation.

- [ ] **Step 8: Implement the Writer Agent**

```python
WRITER_INSTRUCTIONS = """You are the prose writer for a constrained visual novel.
Render only the approved semantic plan. Never add, remove, or change a fact, effect, action, choice
ID, ending ID, or ending obligation. Keep each character's dialogue within that character's supplied
knowledge, beliefs, voice, and boundaries. Write in the script pack language and return only the
requested structured contract."""


class SdkWriter:
    def __init__(self, model: OpenAIResponsesModel) -> None:
        self.agent = Agent(
            name="V2 Scene Writer",
            instructions=WRITER_INSTRUCTIONS,
            model=model,
            output_type=WriterOutput,
        )

    async def write_scene(self, pack, state, plan) -> SceneDraft:
        prompt = json.dumps(
            {
                "operation": "write_scene",
                "approved_plan": plan.model_dump(mode="json"),
                "context": build_writer_context(
                    pack, state, plan.present_character_ids, plan
                ),
            },
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, WriterOutput)
        if output.kind != "scene" or output.scene is None:
            raise ModelContractError("writer returned non-scene output")
        return output.scene

    async def write_ending(self, pack, state, ending) -> EndingDraft:
        prompt = json.dumps(
            {
                "operation": "write_ending",
                "context": build_ending_context(pack, state, ending),
            },
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, WriterOutput)
        if output.kind != "ending" or output.ending is None:
            raise ModelContractError("writer returned non-ending output")
        if output.ending.ending_id != ending.id:
            raise ModelContractError("writer changed ending id")
        return output.ending
```

The Writer receives only approved semantics and truth-safe character views; it never receives a state mutation API.

- [ ] **Step 9: Add one contract-repair retry without protocol fallback**

Use one helper around each Runner call:

```python
async def run_with_contract_retry(agent, prompt: str, expected_type):
    try:
        result = await Runner.run(agent, input=prompt)
        return expected_type.model_validate(result.final_output)
    except (ModelBehaviorError, ValidationError) as first_error:
        repair = json.dumps(
            {
                "operation": "repair_contract",
                "validation_error": str(first_error)[:1000],
                "original_input": json.loads(prompt),
            },
            ensure_ascii=False,
        )
        try:
            result = await Runner.run(agent, input=repair)
            return expected_type.model_validate(result.final_output)
        except (ModelBehaviorError, ValidationError) as second_error:
            raise ModelContractError(
                f"structured output failed after repair: {str(second_error)[:1000]}"
            ) from second_error
```

Import `ModelBehaviorError` from `agents.exceptions`, `ValidationError` from Pydantic, and `ModelContractError` from runtime contracts. Network/auth/rate-limit/timeout failures are not in the caught tuple and propagate to the API. Callers catch `ModelContractError` and choose deterministic fallback. Never construct a Chat Completions model.

- [ ] **Step 10: Run all model adapter tests**

Run: `cd backend && uv run pytest tests/test_runtime_config.py tests/test_runtime_model.py tests/test_runtime_agents.py -q`

Expected: PASS without network access.

- [ ] **Step 11: Commit Responses integration**

```bash
git add backend/src/story/runtime backend/tests/test_runtime_config.py backend/tests/test_runtime_model.py backend/tests/test_runtime_agents.py
git commit -m "feat: connect v2 agents to opencode go responses"
```

---

### Task 10: Expose a V2-Only FastAPI Contract

**Files:**
- Create: `backend/src/story/api.py`
- Modify: `backend/src/main.py`
- Test: `backend/tests/test_v2_api.py`

- [ ] **Step 1: Write failing endpoint tests with injected fake Agents**

```python
def test_create_advance_and_choose_v2_session(tmp_path):
    app = create_app(test_dependencies(tmp_path))
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


def test_v1_routes_are_gone(client):
    assert client.post("/api/sessions", json={}).status_code == 404
    assert client.get("/api/sessions/example").status_code == 404
```

Add focused assertions for the error contract:

```python
def test_unknown_pack_and_session_return_404(client):
    assert client.post(
        "/api/v2/sessions", json={"pack_id": "missing", "session_seed": 1}
    ).status_code == 404
    assert client.get("/api/v2/sessions/missing").status_code == 404


def test_unoffered_choice_returns_422(decision_client, decision_session):
    response = decision_client.post(
        f"/api/v2/sessions/{decision_session.id}/choices/invented",
        json={"expected_revision": decision_session.revision, "idempotency_key": "bad-01"},
    )
    assert response.status_code == 422


def test_missing_runtime_configuration_fails_default_app_start(monkeypatch):
    monkeypatch.delenv("GAL_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        create_app()


def test_provider_failure_is_redacted(provider_failure_client):
    response = provider_failure_client.post(
        "/api/v2/sessions/session_01/advance", json={"expected_revision": 0}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "model_provider_unavailable"}}
    assert "secret" not in response.text
```

Add separate 409 tests for pending decisions and stale revisions using the same fake runtime fixtures.

- [ ] **Step 2: Run API tests and verify they fail**

Run: `cd backend && uv run pytest tests/test_v2_api.py -q`

Expected: FAIL because V2 routes do not exist.

- [ ] **Step 3: Implement script-pack registry and dependencies**

```python
class ScriptPackRegistry:
    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, CompiledScriptPack] = {}

    def get(self, pack_id: str) -> CompiledScriptPack:
        if pack_id not in self._cache:
            pack_path = self.root / pack_id
            if not pack_path.is_dir():
                raise PackNotFound(pack_id)
            pack = compile_script_pack(pack_path)
            if pack.source.identity.id != pack_id:
                raise PackCompileError("pack directory id does not match compiled pack id")
            self._cache[pack_id] = pack
        return self._cache[pack_id]


@dataclass(frozen=True)
class AppDependencies:
    store: StoryEventStore
    registry: ScriptPackRegistry
    runtime: RuntimeService


def default_dependencies() -> AppDependencies:
    settings = OpenCodeGoSettings.from_env()
    bundle = build_model_bundle(settings)
    store = StoryEventStore(Path(os.getenv("GAL_DATABASE_PATH", "data/story-v2.db")))
    registry = ScriptPackRegistry(Path(os.getenv("GAL_SCRIPT_PACK_ROOT", "script_packs")))
    runtime = RuntimeService(
        store,
        SdkPlanner(bundle.model),
        SdkWriter(bundle.model),
    )
    return AppDependencies(store=store, registry=registry, runtime=runtime)
```

Define `PackNotFound(LookupError)` next to the registry. `default_dependencies()` loads one settings object and one model bundle, then injects the exact same `bundle.model` into `SdkPlanner` and `SdkWriter`.

- [ ] **Step 4: Define API models**

```python
class CreateSessionRequest(BaseModel):
    pack_id: str
    session_seed: int


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class ChoiceRequest(RevisionRequest):
    idempotency_key: str = Field(min_length=1, max_length=120)


class SessionResponse(BaseModel):
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

    @classmethod
    def from_state(cls, state: SessionState) -> SessionResponse:
        return cls(
            session_id=state.session_id,
            pack_id=state.pack_id,
            revision=state.revision,
            status=state.status.value,
            phase=state.world.phase.value,
            scene_count=state.world.scene_count,
            pending_decision_id=(
                state.pending_decision.decision_id
                if state.pending_decision is not None
                else None
            ),
            scene_id=state.pending_scene.scene_id if state.pending_scene is not None else None,
            blocks=state.pending_scene.blocks if state.pending_scene is not None else (),
            choices=(
                state.pending_decision.choices
                if state.pending_decision is not None
                else ()
            ),
            ending_id=state.ending.ending_id if state.ending is not None else None,
        )
```

Use the runtime `RuntimeScene` and `ActionResult` models directly as response models.

- [ ] **Step 5: Implement V2 routes**

Implement `create_app` with injected dependencies for offline tests and default model dependencies for production:

```python
def create_app(dependencies: AppDependencies | None = None) -> FastAPI:
    deps = dependencies or default_dependencies()
    app = FastAPI(title="Galgame AI V2")

    @app.exception_handler(PackNotFound)
    async def pack_not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": {"code": "pack_not_found"}})

    @app.exception_handler(SessionNotFound)
    async def session_not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": {"code": "session_not_found"}})

    @app.exception_handler(InvalidChoice)
    async def invalid_choice(request, exc):
        return JSONResponse(status_code=422, content={"detail": {"code": "invalid_choice"}})

    @app.exception_handler(DecisionRequired)
    @app.exception_handler(RuntimeRevisionConflict)
    @app.exception_handler(RevisionConflict)
    @app.exception_handler(PackMismatch)
    @app.exception_handler(RuntimeSessionEnded)
    async def command_conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": {"code": "command_conflict"}})

    @app.exception_handler(OpenAIError)
    async def provider_unavailable(request, exc):
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "model_provider_unavailable"}},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "runtime": "v2"}

    @app.post("/api/v2/sessions", response_model=SessionResponse, status_code=201)
    async def create_session(command: CreateSessionRequest) -> SessionResponse:
        try:
            pack = deps.registry.get(command.pack_id)
        except PackCompileError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_script_pack"},
            ) from exc
        state = initial_session_state(pack, str(uuid4()), command.session_seed)
        deps.store.create_session(state)
        return SessionResponse.from_state(state)

    @app.get("/api/v2/sessions/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str) -> SessionResponse:
        return SessionResponse.from_state(deps.store.load_session(session_id))

    @app.post("/api/v2/sessions/{session_id}/advance", response_model=RuntimeScene)
    async def advance(session_id: str, command: RevisionRequest) -> RuntimeScene:
        state = deps.store.load_session(session_id)
        pack = deps.registry.get(state.pack_id)
        return await deps.runtime.advance(pack, session_id, command.expected_revision)

    @app.post(
        "/api/v2/sessions/{session_id}/choices/{choice_id}",
        response_model=ActionResult,
    )
    async def choose(
        session_id: str,
        choice_id: str,
        command: ChoiceRequest,
    ) -> ActionResult:
        state = deps.store.load_session(session_id)
        pack = deps.registry.get(state.pack_id)
        return await deps.runtime.select_choice(
            pack,
            session_id,
            choice_id,
            command.expected_revision,
            command.idempotency_key,
        )

    return app
```

Import `os`, `Path`, `dataclass`, `uuid4`, `FastAPI`, `HTTPException`, `JSONResponse`, `OpenAIError`, the script-pack compiler/errors, state presentation/session models, event-store errors, runtime contracts/errors, and SDK builders used above. Public exception handlers return stable codes only and never include `str(exc)`.

These are the only routes: `GET /health`, `POST/GET /api/v2/sessions`, `POST /advance`, and `POST /choices/{choice_id}`. Do not echo exception text in public responses. Error mapping:

- `PackCompileError`, missing directory -> 404/422 without filesystem leakage
- `SessionNotFound` -> 404
- `DecisionRequired`, `RuntimeRevisionConflict`, `RevisionConflict`, `PackMismatch`, `RuntimeSessionEnded` -> 409
- invalid/unoffered choice -> 422
- provider auth/rate/timeout/model compatibility -> 503 with stable public error code

- [ ] **Step 6: Rewrite `backend/src/main.py`**

```python
from src.story.api import create_app

app = create_app()
```

Keep Uvicorn startup under `if __name__ == "__main__"`. Do not import or expose V1 packages.

- [ ] **Step 7: Run API and structural tests**

Run: `cd backend && uv run pytest tests/test_v2_api.py tests/test_v2_only_layout.py -q`

Expected: PASS.

- [ ] **Step 8: Commit V2 API**

```bash
git add backend/src/main.py backend/src/story/api.py backend/tests/test_v2_api.py
git commit -m "feat: expose v2 story runtime api"
```

---

### Task 11: Add a Real-Model Autoplay CLI

**Files:**
- Modify: `backend/src/story/cli.py`
- Test: `backend/tests/test_story_cli_live.py`

- [ ] **Step 1: Write failing CLI parser and loop tests**

```python
def test_play_live_parser_accepts_required_arguments():
    args = _parser().parse_args(
        [
            "play-live",
            "script_packs/cafe_mystery",
            "--database", "data/live.db",
            "--session-id", "live-01",
            "--seed", "17",
            "--choice-strategy", "first",
        ]
    )
    assert args.command == "play-live"


@pytest.mark.asyncio
async def test_autoplay_reaches_ended_state_with_fake_agents(tmp_path):
    result = await autoplay(
        pack=test_pack(),
        store=StoryEventStore(tmp_path / "story.db"),
        runtime=fake_ending_runtime(tmp_path),
        session_id="auto-01",
        seed=17,
        choice_strategy="first",
        max_commands=50,
    )
    assert result.status == SessionStatus.ENDED
```

- [ ] **Step 2: Run CLI tests and verify they fail**

Run: `cd backend && uv run pytest tests/test_story_cli_live.py -q`

Expected: FAIL because `play-live` and `autoplay` do not exist.

- [ ] **Step 3: Add the command parser**

```python
play = commands.add_parser("play-live")
play.add_argument("pack_path", type=Path)
play.add_argument("--database", type=Path, required=True)
play.add_argument("--session-id", required=True)
play.add_argument("--seed", type=int, required=True)
play.add_argument("--choice-strategy", choices=("first", "last"), default="first")
play.add_argument("--max-commands", type=int, default=200)
```

- [ ] **Step 4: Implement deterministic autoplay control**

The loop creates the session if absent, then alternates:

```python
async def autoplay(
    pack,
    store,
    runtime,
    session_id: str,
    seed: int,
    choice_strategy: str,
    max_commands: int,
) -> SessionState:
    try:
        state = store.load_session(session_id)
    except SessionNotFound:
        state = initial_session_state(pack, session_id, seed)
        store.create_session(state)
    if state.pack_id != pack.source.identity.id or state.pack_hash != pack.pack_hash:
        raise PackMismatch(session_id)
    commands = 0
    while state.status != SessionStatus.ENDED:
        if commands >= max_commands:
            raise RuntimeError("autoplay command budget exhausted")
        if state.pending_decision:
            choice = state.pending_decision.choices[0 if choice_strategy == "first" else -1]
            result = await runtime.select_choice(
                pack,
                session_id,
                choice.id,
                expected_revision=state.revision,
                idempotency_key=f"autoplay-{commands}",
            )
            _print(result.model_dump(mode="json"))
        else:
            scene = await runtime.advance(pack, session_id, expected_revision=state.revision)
            _print(scene.model_dump(mode="json"))
        state = store.load_session(session_id)
        commands += 1
    return state
```

Construct the SDK settings and agents only inside the `play-live` branch, so `validate`, `init-session`, and `inspect-session` remain usable without a key.

- [ ] **Step 5: Run CLI tests**

Run: `cd backend && uv run pytest tests/test_story_cli.py tests/test_story_cli_live.py -q`

Expected: PASS without network.

- [ ] **Step 6: Commit live autoplay**

```bash
git add backend/src/story/cli.py backend/tests/test_story_cli_live.py
git commit -m "feat: add v2 live autoplay cli"
```

---

### Task 12: Add the Opt-In OpenCode Go Live Capability Test

**Files:**
- Create: `backend/tests/live/__init__.py`
- Create: `backend/tests/live/test_opencode_go_v2_runtime.py`
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Register and default-skip live tests**

Add to pytest config:

```toml
markers = [
    "live: calls the configured external model provider and consumes quota",
]
```

The live module must skip unless `RUN_LIVE_ZEN_TEST=1`; once enabled, `OpenCodeGoSettings.from_env()` requires `GAL_LLM_PROVIDER=opencode_go` and a key alias. Keep the already agreed opt-in variable name for compatibility even though the provider is OpenCode Go.

- [ ] **Step 2: Implement the real Responses capability test**

```python
pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_deepseek_responses_runs_one_v2_choice_roundtrip(tmp_path):
    if os.getenv("RUN_LIVE_ZEN_TEST") != "1":
        pytest.skip("set RUN_LIVE_ZEN_TEST=1 to run provider tests")
    settings = OpenCodeGoSettings.from_env()
    assert settings.api == "responses"
    bundle = build_model_bundle(settings)
    assert isinstance(bundle.model, OpenAIResponsesModel)
    sdk_planner = SdkPlanner(bundle.model)
    writer = SdkWriter(bundle.model)
    pack = compile_script_pack(Path("script_packs/cafe_mystery"))
    store = StoryEventStore(tmp_path / "live.db")
    state = initial_session_state(pack, "live-capability", 17)
    store.create_session(state)

    planner_probe = await sdk_planner.plan_scene(pack, state)
    validate_scene_plan(pack, state, planner_probe)

    actions = sorted(pack.action_ids & set(pack.source.protagonist.capabilities))[:2]
    deterministic_plan = ScenePlan(
        scene_id="live_decision_01",
        summary="The protagonist considers two safe ways to continue the cafe investigation.",
        location_id=state.world.location_id,
        present_character_ids=state.world.present_character_ids,
        terminal="decision",
        decision_id="live_decision_01",
        choices=tuple(
            ChoicePlan(
                option_id=f"live_{action_id}",
                action_id=action_id,
                intent=f"Use {action_id} to continue the investigation",
            )
            for action_id in actions
        ),
    )

    class FixedScenePlanner:
        async def plan_scene(self, pack, state):
            return deterministic_plan

        async def resolve_action(self, pack, state, choice):
            return await sdk_planner.resolve_action(pack, state, choice)

    runtime = RuntimeService(store, FixedScenePlanner(), writer)
    scene = await runtime.advance(pack, state.session_id, state.revision)
    assert scene.blocks
    assert len(scene.choices) == 2

    selected = scene.choices[0]
    result = await runtime.select_choice(
        pack,
        state.session_id,
        selected.id,
        expected_revision=scene.revision,
        idempotency_key="live-capability-choice",
    )
    replayed = store.load_session(state.session_id)
    assert result.revision == replayed.revision
    assert replayed.pending_decision is None
```

The independent Planner probe verifies real Planner `output_type` parsing and domain validation. The committed round trip uses a deterministic decision plan so the live test cannot fail merely because a valid model scene chose `terminal="continue"`; Writer and action Resolver remain real SDK calls through the same `OpenAIResponsesModel`. The test must not call a raw Chat Completions endpoint.

- [ ] **Step 3: Verify default test runs do not access the network**

Run: `cd backend && uv run pytest tests/live/test_opencode_go_v2_runtime.py -q`

Expected: `1 skipped`.

- [ ] **Step 4: Run the live test only with a rotated key**

Run after the user has exported a new secret locally:

```bash
cd backend
GAL_LLM_PROVIDER=opencode_go RUN_LIVE_ZEN_TEST=1 \
  uv run pytest -m live tests/live/test_opencode_go_v2_runtime.py -v
```

Expected: PASS and one completed player-choice round trip. If `/responses`, `output_type`, or model compatibility fails, stop and report the exact public error without switching protocols.

- [ ] **Step 5: Check outputs for secret leakage**

Run: `rg -l 'sk-[A-Za-z0-9]' backend docs frontend`

Expected: no output. Use `-l` so a failure reports only filenames, never secret contents.

- [ ] **Step 6: Commit the live test**

```bash
git add backend/pyproject.toml backend/tests/live
git commit -m "test: verify opencode go responses runtime"
```

---

### Task 13: Update Documentation and Perform Full Verification

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`
- Create: `backend/.env.example`

- [ ] **Step 1: Replace V1 setup and architecture documentation**

Document only the V2 flow:

```dotenv
GAL_LLM_PROVIDER=opencode_go
OPENCODE_GO_API_KEY=
OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1
GAL_LLM_MODEL=deepseek-v4-flash
GAL_LLM_API=responses
GAL_LLM_TIMEOUT_SECONDS=45
GAL_LLM_MAX_RETRIES=1
```

State that the key exposed in chat must be revoked and never placed in `.env.example`. Remove `GAL_USE_STUBS`, `chapter_01`, V1 `/api/sessions`, V1 WebSocket, Director/Character/Choice/Memory architecture, and `plot.md` instructions.

- [ ] **Step 2: Document commands**

Include:

```bash
cd backend
uv sync --extra dev
uv run python -m src.story.cli validate script_packs/cafe_mystery
uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
uv run python -m src.story.cli play-live script_packs/cafe_mystery \
  --database data/live.db --session-id demo --seed 17
```

Explain that validate/init/inspect are offline, while API startup and `play-live` require model configuration.

- [ ] **Step 3: Run the complete backend suite**

Run: `cd backend && uv run pytest tests/ -q`

Expected: all offline tests pass and the live test is skipped unless explicitly enabled.

- [ ] **Step 4: Run scoped static checks**

Run: `cd backend && uv run ruff check src/story src/main.py tests`

Expected: PASS.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 5: Validate the production script pack**

Run: `cd backend && uv run python -m src.story.cli validate script_packs/cafe_mystery`

Expected: `pack_id` is `cafe_mystery`, with at least 3 normal endings and 1 fallback ending.

- [ ] **Step 6: Build and serve the frontend shell**

Run: `cd frontend && npm run build`

Expected: PASS.

Run: `cd frontend && npm run dev -- --host 127.0.0.1`

Expected: Vite prints a local URL; inspect desktop and 320px mobile layouts for overlap, then keep the server running for user review.

- [ ] **Step 7: Run the real normal-play checks**

With a rotated OpenCode Go key exported:

```bash
cd backend
RUN_LIVE_ZEN_TEST=1 uv run pytest -m live tests/live/test_opencode_go_v2_runtime.py -v
uv run python -m src.story.cli play-live script_packs/cafe_mystery \
  --database data/cafe-live.db --session-id cafe-live --seed 17 --choice-strategy first
```

Expected: live capability test passes; autoplay ends with a normal or fallback ending within the script-pack command budget.

- [ ] **Step 8: Commit docs and final verification changes**

```bash
git add README.md backend/README.md backend/.env.example
git commit -m "docs: document v2 deepseek responses runtime"
```

- [ ] **Step 9: Record final evidence**

Before claiming completion, record:

- offline pytest pass count
- Ruff result
- script-pack hash
- frontend build result and review URL
- live test result, or an explicit statement that it could not run because no rotated key was available
- autoplay ending ID and final revision
- `git status --short` result

---

## Plan Self-Review Checklist

- [x] Every requirement in the design spec maps to at least one task above.
- [x] V1 removal and V2 additions are separated into reviewable commits.
- [x] All model calls use `OpenAIResponsesModel`; no Chat Completions fallback exists.
- [x] Planner/Writer share one model configuration but keep separate responsibilities.
- [x] Player commands contain only persisted choice IDs and expected revisions.
- [x] Model output cannot mutate state without Validator, Simulator, reducer, and EventStore.
- [x] Offline validation commands do not require a key.
- [x] Live tests require explicit opt-in and a rotated secret.
- [x] Frontend shell builds without calling V1 endpoints.
- [x] No task contains TBD/TODO or an unspecified implementation step.
