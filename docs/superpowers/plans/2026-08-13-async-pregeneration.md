# Async Pre-generation Implementation Plan

> **Status:** ✅ All tasks complete — 381 tests pass, lint clean.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Eliminate in-game LLM wait time by implementing a dual-layer cache: a frozen Pack Cache (opening + first-decision pregen, generated at `init-pack` time and shared across all sessions) and a dynamic Session Cache (runtime background pre-generation after each decision segment). When a cache hit occurs the orchestrator skips all LLM calls and streams pre-computed blocks instantly.

**Architecture:** `PackCache` persists frozen opening + pre-generated segments to `data/pack_cache/<pack_hash>/`. `PreGenerationManager` holds an in-memory cache of background-pre-generated segments keyed by `(session_id, choice_id)`. `TurnOrchestrator` checks caches before calling any agent; on hit it uses cached plan/draft/events and skips pacing, generation, validation, guard, and simulation. After committing a decision segment the orchestrator fires a background `pregenerate_choices` task. A new `init-pack` CLI command drives the pack-level initialization pipeline.

**Tech Stack:** Python 3.12+, Pydantic v2 (frozen models), FastAPI, asyncio, SQLite event store, pytest.

**Spec:** `docs/superpowers/specs/2026-08-12-async-pregeneration-design.md`

## Global Constraints

- All new runtime models extend `RuntimeModel` (`extra="forbid"`, `frozen=True`).
- Cache data models must be fully JSON-serializable via `model_dump_json()` / `model_validate_json()` for `PackCache` persistence.
- Cache hits still go through `claim_command` → `commit_command` — idempotency and atomic commit are never bypassed.
- Cache hits skip pacing, generation, validation, guard, and simulation (all were done during pre-generation).
- Pre-generation failures are silent — a failed pre-gen falls through to normal generation at runtime.
- `PreGenerationManager` is session-scoped in-memory; `PackCache` is pack-scoped on-disk.
- The opening segment uses a dedicated prompt variant (`OPENING_INSTRUCTIONS`) with longer scene targets.
- Existing offline tests must continue to pass with no model calls.
- `data/` is already gitignored — `data/pack_cache/` will not be committed.
- `PlayerActionSelected` in resolution events already clears `pending_scene` and `pending_decision` (reducer line 153-155), so pre-generation does not need a separate auto-ack step.

---

## File Structure

### New Files

| File | Responsibility |
|------|----------------|
| `backend/src/story/runtime/pack_cache.py` | `CachedOpening`, `CachedPregen` data models; `PackCache` class for on-disk persistence |
| `backend/src/story/runtime/pregeneration.py` | `PreGenerationManager` class for in-memory session-level background pre-generation |
| `backend/tests/test_pack_cache.py` | PackCache model serialization + file I/O tests |
| `backend/tests/test_pregeneration.py` | PreGenerationManager cache lifecycle + concurrency tests |

### Modified Files

| File | Changes |
|------|---------|
| `backend/src/story/runtime/segment_contracts.py` | Add `target_block_range` to `PacingEnvelope` |
| `backend/src/story/runtime/pacing.py` | Populate `target_block_range` in `compute_pacing_envelope` |
| `backend/src/story/runtime/unified_segment.py` | Add `OPENING_INSTRUCTIONS`, accept optional `instructions` param in `SdkUnifiedSegmentAgent.__init__`, add segment-length guidance to `UNIFIED_INSTRUCTIONS` |
| `backend/src/story/runtime/turn_orchestrator.py` | Add `pack_cache` and `pregen_manager` deps; insert cache lookup paths; add post-commit pre-generation trigger |
| `backend/src/story/api.py` | Add `pack_cache` and `pregen_manager` to `AppDependencies` and `default_dependencies()` |
| `backend/src/story/cli.py` | Add `init-pack` subcommand |
| `backend/src/story/runtime/__init__.py` | Export new types |

---

## Task 1: Cache Data Models

**Files:**
- Create: `backend/src/story/runtime/pack_cache.py`
- Test: `backend/tests/test_pack_cache.py`

**Interfaces:**
- Consumes: `SegmentPlan`, `SegmentDraft`, `PacingEnvelope`, `StoryEvent`
- Produces: `CachedOpening`, `CachedPregen`

- [ ] **Step 1: Write failing model serialization tests.**

Create `backend/tests/test_pack_cache.py`:

```python
from __future__ import annotations

import pytest

from src.story.runtime.pack_cache import CachedOpening, CachedPregen
from src.story.runtime.segment_contracts import SegmentPlan, SegmentDraft, PacingEnvelope
from src.story.runtime.contracts import ScenePlan, SceneDraft
from src.story.state import NarrativeBlock, StoryPhase
from src.story.state.events import SceneCommitted, PhaseAdvanced

# ... tests for:
# - CachedOpening JSON round-trip (model_dump_json → model_validate_json)
# - CachedPregen JSON round-trip
# - CachedOpening/CachedPregen reject extra fields (frozen=True, extra="forbid")
# - CachedPregen requires choice_id
# - StoryEvent list serialization preserves discriminator type
```

- [ ] **Step 2: Implement cache data models.**

Create `backend/src/story/runtime/pack_cache.py`:

```python
class CachedOpening(RuntimeModel):
    """Frozen opening segment persisted in PackCache."""
    segment_plan: SegmentPlan
    segment_draft: SegmentDraft
    seg_events: tuple[StoryEvent, ...]
    pacing: PacingEnvelope

class CachedPregen(RuntimeModel):
    """Pre-generated segment for a specific choice, persisted or in-memory."""
    choice_id: str
    pre_events: tuple[StoryEvent, ...]
    seg_events: tuple[StoryEvent, ...]
    segment_plan: SegmentPlan
    segment_draft: SegmentDraft
    pacing: PacingEnvelope
```

- [ ] **Step 3: Run tests, confirm they pass.**

---

## Task 2: PackCache Class

**Files:**
- Modify: `backend/src/story/runtime/pack_cache.py`
- Test: `backend/tests/test_pack_cache.py`

**Interfaces:**
- Consumes: `CachedOpening`, `CachedPregen`, `pathlib.Path`
- Produces: `PackCache`

- [ ] **Step 1: Write failing PackCache I/O tests.**

Add to `backend/tests/test_pack_cache.py`:

```python
# Tests for:
# - save_opening / load_opening round-trip via filesystem (tmp_path)
# - load_opening returns None when file missing
# - save_pregen / load_pregen round-trip
# - load_pregen returns None when file missing
# - has_opening returns True/False correctly
# - Different pack_hash values map to different directories
# - save_opening creates nested directories as needed
```

- [ ] **Step 2: Implement `PackCache` class.**

```python
class PackCache:
    """Pack-level frozen cache for opening + first-decision pre-generation."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _pack_dir(self, pack_hash: str) -> Path:
        return self.root / pack_hash

    def has_opening(self, pack_hash: str) -> bool: ...
    def load_opening(self, pack_hash: str) -> CachedOpening | None: ...
    def save_opening(self, pack_hash: str, opening: CachedOpening) -> None: ...
    def load_pregen(self, pack_hash: str, choice_id: str) -> CachedPregen | None: ...
    def save_pregen(self, pack_hash: str, choice_id: str, pregen: CachedPregen) -> None: ...
    def is_complete(self, pack_hash: str, choice_ids: list[str]) -> bool: ...
    # is_complete: opening.json exists AND all choice pregen files exist
```

- [ ] **Step 3: Run tests, confirm they pass.**

---

## Task 3: PacingEnvelope target_block_range

**Files:**
- Modify: `backend/src/story/runtime/segment_contracts.py`
- Modify: `backend/src/story/runtime/pacing.py`
- Test: `backend/tests/test_pacing.py`

**Interfaces:**
- Consumes: existing `PacingEnvelope`, `compute_pacing_envelope`
- Produces: extended `PacingEnvelope` with `target_block_range`

- [ ] **Step 1: Write failing test for `target_block_range` field.**

Add to `backend/tests/test_pacing.py`:

```python
def test_pacing_envelope_has_target_block_range():
    """PacingEnvelope must carry a target_block_range tuple."""
    # Build a minimal state + pack, compute pacing, assert field exists
    # For normal state: target_block_range == (8, 25)
    # For opening (scene_count == 0): target_block_range == (30, 60)
```

- [ ] **Step 2: Add `target_block_range` to `PacingEnvelope`.**

In `segment_contracts.py`, add field:

```python
class PacingEnvelope(RuntimeModel):
    # ... existing fields ...
    target_block_range: tuple[int, int]
```

- [ ] **Step 3: Populate `target_block_range` in `compute_pacing_envelope`.**

In `pacing.py`:

```python
# Opening (scene_count == 0): (30, 60)
# Normal: (8, 25)
target_block_range = (30, 60) if scene_count == 0 else (8, 25)
```

- [ ] **Step 4: Run tests, confirm all pass including existing pacing tests.**

---

## Task 4: Opening Prompt Variant

**Files:**
- Modify: `backend/src/story/runtime/unified_segment.py`
- Test: `backend/tests/test_segment_context.py` or `backend/tests/test_runtime_contracts.py`

**Interfaces:**
- Consumes: existing `SdkUnifiedSegmentAgent`, `UNIFIED_INSTRUCTIONS`
- Produces: `OPENING_INSTRUCTIONS`, parameterized `SdkUnifiedSegmentAgent`

- [ ] **Step 1: Write failing test for opening instructions.**

```python
def test_opening_instructions_exist_and_differ():
    from src.story.runtime.unified_segment import OPENING_INSTRUCTIONS, UNIFIED_INSTRUCTIONS
    assert OPENING_INSTRUCTIONS != UNIFIED_INSTRUCTIONS
    assert "30" in OPENING_INSTRUCTIONS or "opening" in OPENING_INSTRUCTIONS.lower()

def test_unified_agent_accepts_custom_instructions():
    """SdkUnifiedSegmentAgent should accept optional instructions param."""
    from src.story.runtime.unified_segment import SdkUnifiedSegmentAgent, OPENING_INSTRUCTIONS
    # Construct with opening instructions — verify agent.instructions == OPENING_INSTRUCTIONS
```

- [ ] **Step 2: Add `OPENING_INSTRUCTIONS` constant.**

```python
OPENING_INSTRUCTIONS = """You are the Unified Segment Agent for a constrained visual novel.
This is the game OPENING — a long, atmospheric prologue.

  STEP 1 — PLAN: Decide the scene structure for a long opening sequence.
  STEP 2 — WRITE: Render each planned scene as narration and dialogue blocks.

═══ OPENING-SPECIFIC RULES ═══

1. Generate 8-15 scenes (target 30-50 narrative blocks total).
2. The opening must world-build: establish setting, character relationships, and the initial conflict.
3. Do NOT rush to a decision — let the player immerse in the opening atmosphere.
4. The last scene MUST be terminal="decision" with 2-4 choices.
5. All middle scenes must be terminal="continue".
6. Provide ample narration, environmental description, and character interaction between scenes.

═══ PLANNING RULES ═══
(same as UNIFIED_INSTRUCTIONS planning rules)

═══ WRITING RULES ═══
(same as UNIFIED_INSTRUCTIONS writing rules)
"""
```

- [ ] **Step 3: Add segment-length guidance to `UNIFIED_INSTRUCTIONS`.**

Append to the writing rules:

```
8. Generate sufficiently long continuous Galgame performance. Aim for at least
   8 blocks of narration and dialogue between choices. Do not rush toward the
   decision point — let the player linger in each scene.
```

- [ ] **Step 4: Add optional `instructions` parameter to `SdkUnifiedSegmentAgent`.**

```python
class SdkUnifiedSegmentAgent:
    def __init__(
        self,
        model: OpenAIResponsesModel,
        instructions: str = UNIFIED_INSTRUCTIONS,
    ) -> None:
        self.agent = Agent(
            name="Unified Segment Agent",
            instructions=instructions,
            ...
        )
```

- [ ] **Step 5: Run tests, confirm they pass.**

---

## Task 5: init-pack CLI Command

**Files:**
- Modify: `backend/src/story/cli.py`
- Test: `backend/tests/test_story_cli.py`

**Interfaces:**
- Consumes: `compile_script_pack`, `initial_session_state`, `SdkUnifiedSegmentAgent`, `SdkPlanner`, `PackCache`, `compute_pacing_envelope`, `validate_segment_plan`, `validate_segment_draft`, `simulate_segment`, `simulate_resolution`
- Produces: `init-pack` CLI subcommand + `_init_pack()` async function

- [ ] **Step 1: Write failing init-pack CLI tests.**

Add to `backend/tests/test_story_cli.py`:

```python
# Tests for:
# - init-pack requires pack_path argument
# - init-pack creates data/pack_cache/<hash>/opening.json
# - init-pack is idempotent (second run detects existing cache, skips)
# - init-pack --force regenerates even when cache exists
# - init-pack with non-existent pack path fails gracefully
# All tests mock LLM agents — no real API calls.
```

- [ ] **Step 2: Add `init-pack` subparser.**

In `_parser()`:

```python
init_pack = commands.add_parser("init-pack")
init_pack.add_argument("pack_path", type=Path)
init_pack.add_argument("--force", action="store_true")
init_pack.add_argument("--cache-root", type=Path, default=Path("data/pack_cache"))
```

- [ ] **Step 3: Implement `_init_pack()` async function.**

```python
async def _init_pack(pack, cache: PackCache, unified_agent, opening_agent, planner, force: bool) -> dict:
    """Generate opening + pregen, persist to PackCache.

    Returns summary dict for CLI output.
    Flow:
    1. Check if cache is complete and !force → skip
    2. Build initial session state
    3. Compute pacing (opening: target_block_range=(30,60))
    4. Opening agent generates opening segment
    5. Validate plan + draft
    6. Guard check
    7. Simulate segment events
    8. Save opening to PackCache
    9. For each choice in the opening's decision:
       a. Resolve choice (planner)
       b. Validate resolution
       c. Simulate resolution events → hypothetical state
       d. Compute pacing for hypothetical state
       e. Unified agent generates next segment
       f. Validate, guard, simulate
       g. Save pregen to PackCache
    """
```

- [ ] **Step 4: Wire `init-pack` command in `main()`.**

```python
if args.command == "init-pack":
    from dotenv import load_dotenv
    load_dotenv()
    pack = compile_script_pack(args.pack_path)
    cache = PackCache(args.cache_root)
    if cache.is_complete(pack.pack_hash, ...) and not args.force:
        _print({"status": "already_initialized", "pack_hash": pack.pack_hash})
        return 0
    settings = OpenCodeGoSettings.from_env()
    bundle = build_model_bundle(settings)
    opening_agent = SdkUnifiedSegmentAgent(bundle.model, instructions=OPENING_INSTRUCTIONS)
    unified_agent = SdkUnifiedSegmentAgent(bundle.model)
    planner = SdkPlanner(bundle.model)
    result = asyncio.run(_init_pack(pack, cache, unified_agent, opening_agent, planner, args.force))
    _print(result)
    return 0
```

- [ ] **Step 5: Run tests, confirm they pass.**

---

## Task 6: TurnOrchestrator — Opening Cache Path

**Files:**
- Modify: `backend/src/story/runtime/turn_orchestrator.py`
- Test: `backend/tests/test_turn_orchestrator.py`

**Interfaces:**
- Consumes: `PackCache`, existing orchestrator pipeline
- Produces: cache-aware `execute_turn` with opening shortcut

- [ ] **Step 1: Write failing test for cached opening.**

```python
def test_cached_opening_skips_generation(tmp_path: Path):
    """When PackCache has an opening, the orchestrator uses it directly
    and never calls director/writer/unified_agent."""
    # Build orchestrator with a mock PackCache containing a CachedOpening
    # Use a FailingDirector that raises if called — verify it's never invoked
    # Verify: segment_started, blocks, segment_ready are emitted correctly
    # Verify: committed events match cached seg_events
```

- [ ] **Step 2: Add `pack_cache` to `TurnOrchestrator.__init__`.**

```python
def __init__(
    self,
    store: StoryEventStore,
    director: DirectorPort,
    writer: SegmentWriterPort,
    guard: GuardPort,
    completion_judge: CompletionJudge,
    planner: PlannerPort | None = None,
    unified_agent: UnifiedSegmentPort | None = None,
    pack_cache: PackCache | None = None,
) -> None:
    ...
    self.pack_cache = pack_cache
```

- [ ] **Step 3: Insert opening cache check in `execute_turn`.**

After the choice resolution block and before pacing/generation, add:

```python
# Check for cached opening (choice_id is None and no pending decision)
cached_opening = None
if choice_id is None and self.pack_cache is not None:
    cached_opening = self.pack_cache.load_opening(pack.pack_hash)

if cached_opening is not None:
    plan = cached_opening.segment_plan
    draft = cached_opening.segment_draft
    seg_events = list(cached_opening.seg_events)
    # Skip pacing, generation, validation, guard, simulation
    # Go directly to streaming + commit
```

This requires restructuring the try block so that the streaming/commit section is reachable from both the cache-hit path and the normal generation path. Use a `cached: bool` flag or early variable assignment to control the flow.

- [ ] **Step 4: Run tests, confirm opening cache path works and existing tests pass.**

---

## Task 7: TurnOrchestrator — Choice Cache Path (Pack + Session)

**Files:**
- Modify: `backend/src/story/runtime/turn_orchestrator.py`
- Test: `backend/tests/test_turn_orchestrator.py`

**Interfaces:**
- Consumes: `PackCache`, `PreGenerationManager` (from Task 8 — stub for now)
- Produces: cache-aware choice resolution path

> **Note:** This task depends on Task 8 (PreGenerationManager). Task 8 and Task 7 should be done together — implement Task 8 first, then wire it here.

- [ ] **Step 1: Write failing test for cached choice (pack cache hit).**

```python
def test_pack_cache_hit_skips_planner_and_agent(tmp_path: Path):
    """When PackCache has a pregen for a choice_id, the orchestrator uses it
    directly — planner.resolve_action and unified_agent.generate are never called."""
    # Build orchestrator with a mock PackCache containing CachedPregen
    # Use FailingPlanner and FailingDirector to verify they're not called
    # Verify events are committed correctly
```

- [ ] **Step 2: Insert choice cache lookup in `execute_turn`.**

In the `choice_id is not None` branch, before calling `planner.resolve_action`:

```python
# Check caches before resolving
pregen = None
if self.pregen_manager is not None:
    pregen = self.pregen_manager.try_get(session_id, choice_id)
if pregen is None and self.pack_cache is not None:
    pregen = self.pack_cache.load_pregen(pack.pack_hash, choice_id)
if pregen is None and self.pregen_manager is not None:
    pregen = await self.pregen_manager.await_in_progress(session_id, choice_id)

if pregen is not None:
    # Cache hit: use cached plan, draft, pre_events, seg_events
    plan = pregen.segment_plan
    draft = pregen.segment_draft
    pre_events = list(pregen.pre_events)
    seg_events = list(pregen.seg_events)
    # Skip resolution, pacing, generation, validation, guard, simulation
    cached = True
else:
    # Normal resolution + generation flow (existing code)
    cached = False
```

- [ ] **Step 3: Run tests, confirm they pass.**

---

## Task 8: PreGenerationManager

**Files:**
- Create: `backend/src/story/runtime/pregeneration.py`
- Test: `backend/tests/test_pregeneration.py`

**Interfaces:**
- Consumes: `CachedPregen`, `PlannerPort`, `UnifiedSegmentPort`, `GuardPort`, `CompiledScriptPack`, `SessionState`, `PresentedChoice`, `compute_pacing_envelope`, `validate_*`, `simulate_*`
- Produces: `PreGenerationManager`

- [ ] **Step 1: Write failing PreGenerationManager tests.**

Create `backend/tests/test_pregeneration.py`:

```python
# Tests for:
# - try_get returns None when cache empty
# - try_get returns CachedPregen after pregenerate_choices completes
# - try_get pops (removes) the entry after returning
# - pregenerate_choices skips already-cached choices
# - pregenerate_choices skips already-running tasks
# - await_in_progress waits for running task and returns result
# - await_in_progress returns None on timeout
# - await_in_progress returns None when no task running
# - cleanup_session removes all cache entries and cancels all tasks
# - cleanup_session cancels in-progress tasks
# - pre-generation failure (mock agent raises) → cache entry not created, no exception propagated
# - Multiple choices pre-generated concurrently
```

All tests use fake/mock agents — no real LLM calls.

- [ ] **Step 2: Implement `PreGenerationManager`.**

```python
class PreGenerationManager:
    def __init__(
        self,
        planner: PlannerPort,
        unified_agent: UnifiedSegmentPort,
        guard: GuardPort,
    ) -> None:
        self._planner = planner
        self._unified_agent = unified_agent
        self._guard = guard
        self._cache: dict[tuple[str, str], CachedPregen] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    async def pregenerate_choices(
        self,
        session_id: str,
        state: SessionState,
        choices: list[PresentedChoice],
        pack: CompiledScriptPack,
    ) -> None: ...

    async def _pregenerate_one(
        self,
        session_id: str,
        choice: PresentedChoice,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> None:
        """Full pre-generation pipeline for one choice:
        1. resolve_action (LLM)
        2. validate_action_resolution
        3. simulate_resolution → pre_events
        4. apply_events → hypothetical_state
        5. compute_pacing_envelope
        6. unified_agent.generate (LLM)
        7. validate_segment_plan + validate_segment_draft
        8. guard.check_segment
        9. simulate_segment → seg_events
        10. store CachedPregen in _cache
        All exceptions are swallowed (pre-gen failure → fallback at runtime).
        """

    def try_get(self, session_id: str, choice_id: str) -> CachedPregen | None: ...
    async def await_in_progress(self, session_id: str, choice_id: str, timeout: float = 15.0) -> CachedPregen | None: ...
    def cleanup_session(self, session_id: str) -> None: ...
```

Key implementation detail: `simulate_resolution` internally calls `simulate_events` which raises on invalid state transitions. The `_pregenerate_one` must catch this and treat it as a pre-gen failure.

- [ ] **Step 3: Run tests, confirm they pass.**

---

## Task 9: TurnOrchestrator — Post-commit Pregeneration Trigger

**Files:**
- Modify: `backend/src/story/runtime/turn_orchestrator.py`
- Test: `backend/tests/test_turn_orchestrator.py`

**Interfaces:**
- Consumes: `PreGenerationManager`
- Produces: background pre-generation trigger after decision commit

- [ ] **Step 1: Write failing test for pre-generation trigger.**

```python
def test_pregeneration_triggered_after_decision_commit(tmp_path: Path):
    """After committing a decision segment, pregeneration_manager.pregenerate_choices
    is called with the new choices and updated state."""
    # Use a mock PreGenerationManager that records calls
    # Run a turn, verify pregenerate_choices was called with correct args
```

- [ ] **Step 2: Add `pregen_manager` to `TurnOrchestrator.__init__`.**

```python
def __init__(
    self,
    ...
    pregen_manager: PreGenerationManager | None = None,
) -> None:
    ...
    self.pregen_manager = pregen_manager
```

- [ ] **Step 3: Add pre-generation trigger after commit.**

After `commit_command` succeeds and before streaming `segment_ready`:

```python
# Trigger background pre-generation for next choices.
if (
    plan.terminal == "decision"
    and self.pregen_manager is not None
    and ready_data.get("choices")
):
    choices_data = ready_data["choices"]
    updated_state = self.store.load_session(session_id)
    presented_choices = [
        PresentedChoice(
            id=c["id"],
            action_id=c["action_id"],
            label=c["label"],
            intent=c.get("intent", c["label"]),
            target_character_id=c.get("target_character_id"),
            preview=c.get("preview"),
        )
        for c in choices_data
    ]
    asyncio.create_task(
        self.pregen_manager.pregenerate_choices(
            session_id, updated_state, presented_choices, pack
        )
    )
```

- [ ] **Step 4: Run tests, confirm they pass.**

---

## Task 10: API Wiring

**Files:**
- Modify: `backend/src/story/api.py`
- Test: `backend/tests/test_v2_api.py` or `backend/tests/test_turns_api.py`

**Interfaces:**
- Consumes: `PackCache`, `PreGenerationManager`
- Produces: fully wired `AppDependencies`

- [ ] **Step 1: Write failing integration test.**

```python
def test_default_dependencies_include_pack_cache_and_pregen():
    """default_dependencies() should create pack_cache and pregen_manager."""
    # This test requires env vars — mark as live or mock.
    # Alternatively, test AppDependencies construction directly.
```

- [ ] **Step 2: Add `pack_cache` and `pregen_manager` to `AppDependencies`.**

```python
@dataclass(frozen=True)
class AppDependencies:
    store: StoryEventStore
    registry: ScriptPackRegistry
    runtime: RuntimeService | None = None
    orchestrator: TurnOrchestrator | None = None
    director: DirectorPort | None = None
    segment_writer: SegmentWriterPort | None = None
    guard: GuardPort | None = None
    pack_cache: PackCache | None = None
    pregen_manager: PreGenerationManager | None = None
```

- [ ] **Step 3: Wire in `default_dependencies()`.**

```python
from src.story.runtime.pack_cache import PackCache
from src.story.runtime.pregeneration import PreGenerationManager

pack_cache = PackCache(Path(os.getenv("GAL_PACK_CACHE_ROOT", "data/pack_cache")))
pregen_manager = PreGenerationManager(
    planner=SdkPlanner(bundle.model),
    unified_agent=unified_agent,
    guard=Guard(),
)
orchestrator = TurnOrchestrator(
    store,
    director,
    segment_writer,
    guard,
    CompletionJudge(),
    planner=SdkPlanner(bundle.model),
    unified_agent=unified_agent,
    pack_cache=pack_cache,
    pregen_manager=pregen_manager,
)
```

- [ ] **Step 4: Run tests, confirm they pass.**

---

## Task 11: Full Integration Test

**Files:**
- Test: `backend/tests/test_turn_orchestrator.py`

- [ ] **Step 1: Write end-to-end cache flow test.**

```python
def test_full_cache_flow_opening_to_choice(tmp_path: Path):
    """Full flow: cached opening → choice → pre-generated choice hit.
    1. Save opening to PackCache
    2. Run opening turn → cache hit, no LLM
    3. PreGenerationManager pre-generates choices
    4. Run choice turn → session cache hit, no LLM
    5. Verify revision advances and events are correct
    """
```

- [ ] **Step 2: Write session cleanup test.**

```python
def test_session_cleanup_clears_cache():
    """PreGenerationManager.cleanup_session removes all entries."""
```

- [ ] **Step 3: Write cache-miss fallback test.**

```python
def test_cache_miss_falls_through_to_normal_generation(tmp_path: Path):
    """When no cache entry exists, orchestrator generates normally."""
```

- [ ] **Step 4: Run all tests, confirm full suite passes.**

---

## Task 12: Run Full Test Suite + Lint

- [ ] **Step 1: Run full backend test suite.**

```bash
cd backend && uv run pytest -x -q
```

- [ ] **Step 2: Run ruff lint + format check.**

```bash
cd backend && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

- [ ] **Step 3: Fix any failures.**

---

## Summary of Invariants

| Invariant | How preserved |
|-----------|--------------|
| Idempotency (claim → replay) | Cache hits still go through `claim_command` / `commit_command` |
| Atomic commit | Pre-computed events committed via same `commit_command` path |
| Revision check | `expected_revision` checked before cache lookup |
| Guard validation | Run during pre-generation; skipped at runtime for cache hits |
| Simulation | Run during pre-generation; `seg_events` stored in cache |
| Fallback safety | Cache miss → normal generation → deterministic fallback |
| Session isolation | Session cache keyed by `(session_id, choice_id)`; cleanup on session end |
| Pack hash invalidation | Pack YAML change → hash change → old cache ignored |
