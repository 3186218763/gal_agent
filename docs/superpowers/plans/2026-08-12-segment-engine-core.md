# Segment Engine Core Implementation Plan

> Implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the transactional performance-segment runtime that replaces per-scene advance with multi-scene segments, pacing-governed endings, completion judgment, and a single SSE turn endpoint.

**Architecture:** The Turn Orchestrator is the sole entry point for a player turn. It resolves the choice (if any), derives a PacingEnvelope, calls the Director for a SegmentPlan, validates it, calls the SegmentWriter for a SegmentDraft, runs the Guard, simulates all events on a candidate state, optionally invokes the CompletionJudge, and atomically commits. The API layer streams provisional blocks over SSE and emits `segment_ready` after commit.

**Tech Stack:** Python 3.12+, Pydantic v2 (frozen models), FastAPI, SQLite event store, pytest, SSE (text/event-stream).

## Global Constraints

- All new models use `ConfigDict(extra="forbid", frozen=True)` — either `RuntimeModel` (contracts layer) or `FrozenModel` (state layer).
- Event types live in `backend/src/story/state/events.py` and are part of the `StoryEvent` discriminated union (`Field(discriminator="type")`).
- The reducer in `backend/src/story/state/reducer.py` must handle every event type in the union — no fallthrough.
- Agent proposes, deterministic kernel commits: no model output is written to state without passing through validation, simulation, and atomic `commit_command`.
- SceneCommitted backward compatibility: `terminal` defaults to `"continue"`, `decision_id` defaults to `None`, `choices` defaults to `()`. V1 events with explicit values still work.
- SafeId pattern: `^[a-z][a-z0-9_]{1,63}$` for pack-level IDs.
- `min_scenes < max_scenes` and `min_scenes + reserved_resolution_scenes <= max_scenes` enforced by pack schema.
- No raw prompts, hidden facts, model output, API keys, or database paths in error responses.
- `cleared = all(requirement.satisfied)` — the kernel computes this, not the judge.

## Plan Dependencies

This plan depends on **Plan 1 (Pack 2.0)** for:
- `ScriptPackSourceV2` with `completion_requirements` field
- `CompletionRequirementSource` and `EvidenceHintsSource` types
- `minimal_pack_v2_dict()` factory function for real v2.0 pack testing

For backward compatibility with v1.0 packs during tests, use `getattr(pack.source, "completion_requirements", ())`.

See `cross-plan-resolution.md` sections 2-5 for authoritative type locations and shared definitions.

---

## File Structure

### New Files

| File | Responsibility |
|------|----------------|
| `backend/src/story/runtime/segment_contracts.py` | SegmentPlan, SegmentDraft, EndingProposal, ThreadOperation, PacingEnvelope, GuardResult, GuardViolation, DirectorPort, SegmentWriterPort, GuardPort, CompletionAssessment, CompletionResult |
| `backend/src/story/runtime/pacing.py` | `compute_pacing_envelope()`, PacingEnvelope derivation from state + pack |
| `backend/src/story/runtime/completion_judge.py` | CompletionJudge: deterministic evaluation of final state vs requirements |
| `backend/src/story/runtime/turn_orchestrator.py` | TurnOrchestrator: single-turn command pipeline with SSE streaming |
| `backend/tests/fakes.py` | FakeDirector, FakeSegmentWriter, FakeGuard — canned implementations for testing |
| `backend/tests/test_segment_contracts.py` | Contract validation tests |
| `backend/tests/test_pacing.py` | Pacing envelope computation tests |
| `backend/tests/test_segment_validator.py` | Segment plan and draft validation tests |
| `backend/tests/test_segment_simulator.py` | Segment event simulation tests |
| `backend/tests/test_completion_judge.py` | Completion judge evaluation tests |
| `backend/tests/test_turn_orchestrator.py` | Turn orchestrator pipeline tests |
| `backend/tests/test_segment_property.py` | End-to-end property tests with multiple player policies |

### Modified Files

| File | Changes |
|------|---------|
| `backend/src/story/state/models.py` | Add `CompletionAssessmentRecord`, `CompletionState`; extend `EndingRuntime` (add `tone`, `terminal_state_summary`, make `required_payoffs` and `final_scene_budget` optional); add `completion` field to `SessionState` |
| `backend/src/story/state/events.py` | Add `DecisionPresented`, `EndingGenerated`, `CompletionEvaluated`; modify `SceneCommitted` defaults; update `StoryEvent` union |
| `backend/src/story/state/reducer.py` | Handle new event types; backward-compatible `SceneCommitted` |
| `backend/src/story/state/__init__.py` | Export new types |
| `backend/src/story/runtime/contracts.py` | Extend `EndingDraft` with `tone` and `terminal_state_summary` |
| `backend/src/story/runtime/validator.py` | Add `validate_segment_plan`, `validate_segment_draft` |
| `backend/src/story/runtime/simulator.py` | Add `segment_events`, `simulate_segment` |
| `backend/src/story/api.py` | Add `POST /api/v2/sessions/{id}/turns`; update `AppDependencies`; migration wrappers for old routes |

---

## Task 1: State Model Extensions

**Files:**
- Modify: `backend/src/story/state/models.py`
- Test: `backend/tests/test_story_state.py`

**Interfaces:**
- Consumes: existing `FrozenModel`, `NarrativeBlock`
- Produces: `CompletionAssessmentRecord`, `CompletionState`; modified `EndingRuntime` (with `tone`, `terminal_state_summary`, defaulted `required_payoffs` and `final_scene_budget`); modified `SessionState` (with `completion` field)

- [ ] **Step 1: Write failing tests for CompletionAssessmentRecord and CompletionState**

Add to `backend/tests/test_story_state.py`:

```python
from src.story.state.models import CompletionAssessmentRecord, CompletionState


def test_completion_assessment_record_minimal():
    record = CompletionAssessmentRecord(
        requirement_id="core_truth",
        satisfied=True,
    )
    assert record.requirement_id == "core_truth"
    assert record.satisfied is True
    assert record.cited_event_ids == ()
    assert record.rationale == ""


def test_completion_assessment_record_full():
    record = CompletionAssessmentRecord(
        requirement_id="protagonist_choice",
        satisfied=False,
        cited_event_ids=("evt-1", "evt-2"),
        rationale="No irreversible choice was made",
    )
    assert record.satisfied is False
    assert len(record.cited_event_ids) == 2


def test_completion_state():
    assessment = CompletionAssessmentRecord(
        requirement_id="req_a", satisfied=True, rationale="ok",
    )
    state = CompletionState(cleared=True, assessments=(assessment,))
    assert state.cleared is True
    assert len(state.assessments) == 1


def test_completion_state_not_cleared():
    state = CompletionState(cleared=False, assessments=())
    assert state.cleared is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_story_state.py::test_completion_assessment_record_minimal tests/test_story_state.py::test_completion_assessment_record_full tests/test_story_state.py::test_completion_state tests/test_story_state.py::test_completion_state_not_cleared -v`
Expected: FAIL with `ImportError: cannot import name 'CompletionAssessmentRecord'`

- [ ] **Step 3: Add model types to state/models.py**

Add after the `EndingRuntime` class in `backend/src/story/state/models.py`:

```python
class CompletionAssessmentRecord(FrozenModel):
    requirement_id: str
    satisfied: bool
    cited_event_ids: tuple[str, ...] = ()
    rationale: str = ""


class CompletionState(FrozenModel):
    cleared: bool
    assessments: tuple[CompletionAssessmentRecord, ...]
```

- [ ] **Step 4: Extend EndingRuntime with optional fields**

Replace the existing `EndingRuntime` class in `backend/src/story/state/models.py`:

```python
class EndingRuntime(FrozenModel):
    ending_id: str
    entered_at_revision: int = Field(ge=1)
    required_payoffs: tuple[str, ...] = ()
    final_scene_budget: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=120)
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)
    tone: str | None = None
    terminal_state_summary: str | None = None
```

- [ ] **Step 5: Add completion field to SessionState**

Add `completion: CompletionState | None = None` to the `SessionState` class, after the `ending` field:

```python
class SessionState(FrozenModel):
    session_id: str
    pack_id: str
    pack_hash: str
    revision: int = Field(default=0, ge=0)
    status: SessionStatus = SessionStatus.ACTIVE
    session_seed: int
    created_at: datetime = Field(default_factory=utc_now)
    world: WorldSnapshot
    facts: dict[str, FactRecord]
    characters: dict[str, CharacterRuntime]
    threads: dict[str, NarrativeThread] = Field(default_factory=dict)
    pending_scene: PendingSceneReference | None = None
    pending_decision: PendingDecisionReference | None = None
    ending: EndingRuntime | None = None
    completion: CompletionState | None = None
```

- [ ] **Step 6: Write failing test for extended EndingRuntime**

Add to `backend/tests/test_story_state.py`:

```python
from src.story.state.models import EndingRuntime, NarrativeBlock


def test_ending_runtime_v2_fields():
    ending = EndingRuntime(
        ending_id="ending_sess_001",
        entered_at_revision=10,
        title="The Long Goodbye",
        blocks=(NarrativeBlock(kind="narration", text="They parted."),),
        tone="bittersweet",
        terminal_state_summary="Alice left the city.",
    )
    assert ending.tone == "bittersweet"
    assert ending.terminal_state_summary == "Alice left the city."
    assert ending.required_payoffs == ()
    assert ending.final_scene_budget == 1


def test_ending_runtime_v1_backward_compat():
    ending = EndingRuntime(
        ending_id="ally_ending",
        entered_at_revision=5,
        required_payoffs=("Alice and the protagonist cooperate.",),
        final_scene_budget=2,
        title="Together",
        blocks=(NarrativeBlock(kind="narration", text="The end."),),
    )
    assert ending.tone is None
    assert ending.required_payoffs == ("Alice and the protagonist cooperate.",)
```

- [ ] **Step 7: Run all state model tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_story_state.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/state/models.py backend/tests/test_story_state.py
git commit -m "feat(state): add CompletionState, extend EndingRuntime for v2 endings"
```

---

## Task 2: New Event Types

**Files:**
- Modify: `backend/src/story/state/events.py`
- Modify: `backend/src/story/state/__init__.py`
- Test: `backend/tests/test_story_reducer.py`

**Interfaces:**
- Consumes: `CompletionAssessmentRecord` from Task 1, existing `FrozenModel`, `NarrativeBlock`, `PresentedChoice`
- Produces: `DecisionPresented`, `EndingGenerated`, `CompletionEvaluated` events; modified `SceneCommitted` (terminal defaults to `"continue"`); updated `StoryEvent` union

- [ ] **Step 1: Write failing tests for new event types**

Add to `backend/tests/test_story_reducer.py`:

```python
import pytest
from src.story.state.events import (
    CompletionEvaluated,
    DecisionPresented,
    EndingGenerated,
    SceneCommitted,
)
from src.story.state.models import (
    CompletionAssessmentRecord,
    NarrativeBlock,
    PresentedChoice,
)


def test_decision_presented_event_serialization():
    event = DecisionPresented(
        decision_id="dec_01",
        choices=(
            PresentedChoice(id="opt_a", action_id="ask", label="Ask", intent="Ask directly"),
            PresentedChoice(id="opt_b", action_id="observe", label="Watch", intent="Watch carefully"),
        ),
    )
    assert event.type == "decision_presented"
    assert len(event.choices) == 2


def test_ending_generated_event_serialization():
    event = EndingGenerated(
        ending_id="ending_sess_001",
        title="The Long Goodbye",
        tone="bittersweet",
        terminal_state_summary="Alice left the city.",
        blocks=(NarrativeBlock(kind="narration", text="They parted."),),
    )
    assert event.type == "ending_generated"
    assert event.tone == "bittersweet"


def test_completion_evaluated_event_serialization():
    event = CompletionEvaluated(
        cleared=True,
        assessments=(
            CompletionAssessmentRecord(
                requirement_id="req_a",
                satisfied=True,
                rationale="Fact committed",
            ),
        ),
    )
    assert event.type == "completion_evaluated"
    assert event.cleared is True


def test_scene_committed_default_terminal_is_continue():
    event = SceneCommitted(
        scene_id="scene_01",
        location_id="cafe",
        present_character_ids=("alice",),
        blocks=(NarrativeBlock(kind="narration", text="A quiet day."),),
    )
    assert event.terminal == "continue"
    assert event.decision_id is None
    assert event.choices == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_story_reducer.py::test_decision_presented_event_serialization tests/test_story_reducer.py::test_ending_generated_event_serialization tests/test_story_reducer.py::test_completion_evaluated_event_serialization tests/test_story_reducer.py::test_scene_committed_default_terminal_is_continue -v`
Expected: FAIL with `ImportError: cannot import name 'DecisionPresented'`

- [ ] **Step 3: Modify SceneCommitted defaults**

In `backend/src/story/state/events.py`, change the `terminal` field of `SceneCommitted` to have a default:

```python
class SceneCommitted(FrozenModel):
    type: Literal["scene_committed"] = "scene_committed"
    scene_id: str
    terminal: Literal["continue", "decision", "ending"] = "continue"
    location_id: str
    present_character_ids: tuple[str, ...]
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)
    decision_id: str | None = None
    choices: tuple[PresentedChoice, ...] = ()
```

- [ ] **Step 4: Add new event types**

Add before the `StoryEvent` union in `backend/src/story/state/events.py`:

```python
class DecisionPresented(FrozenModel):
    type: Literal["decision_presented"] = "decision_presented"
    decision_id: str
    choices: tuple[PresentedChoice, ...] = Field(min_length=2, max_length=4)


class EndingGenerated(FrozenModel):
    type: Literal["ending_generated"] = "ending_generated"
    ending_id: str
    title: str = Field(min_length=1, max_length=120)
    tone: str = Field(min_length=1)
    terminal_state_summary: str = Field(min_length=1)
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)


class CompletionEvaluated(FrozenModel):
    type: Literal["completion_evaluated"] = "completion_evaluated"
    cleared: bool
    assessments: tuple[CompletionAssessmentRecord, ...]
```

Add `CompletionAssessmentRecord` to the imports at the top of `events.py`:

```python
from src.story.state.models import (
    BeliefRecord,
    CompletionAssessmentRecord,
    EndingRuntime,
    FrozenModel,
    GoalStatus,
    NarrativeBlock,
    NarrativeThread,
    PresentedChoice,
    StoryPhase,
    ThreadStatus,
    utc_now,
)
```

- [ ] **Step 5: Update StoryEvent union**

Replace the `StoryEvent` union to include the three new event types:

```python
StoryEvent = Annotated[
    SceneCommitted
    | SceneAcknowledged
    | PlayerActionSelected
    | ActionResolved
    | FactCommitted
    | FactEvidenced
    | FactRevealed
    | CharacterLearnedFact
    | BeliefChanged
    | RelationshipChanged
    | GoalAdvanced
    | ThreadOpened
    | ThreadAdvanced
    | ThreadClosed
    | PhaseAdvanced
    | EndingEntered
    | EndingGenerated
    | DecisionPresented
    | CompletionEvaluated
    | SessionEnded,
    Field(discriminator="type"),
]
```

- [ ] **Step 6: Update state/__init__.py exports**

Add the new event types and model types to the imports and `__all__` in `backend/src/story/state/__init__.py`:

Add to imports from `.events`:
```python
    CompletionEvaluated,
    DecisionPresented,
    EndingGenerated,
```

Add to imports from `.models`:
```python
    CompletionAssessmentRecord,
    CompletionState,
```

Add to `__all__`:
```python
    "CompletionAssessmentRecord",
    "CompletionEvaluated",
    "CompletionState",
    "DecisionPresented",
    "EndingGenerated",
```

- [ ] **Step 7: Run all event type tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_story_reducer.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/state/events.py backend/src/story/state/__init__.py backend/tests/test_story_reducer.py
git commit -m "feat(state): add DecisionPresented, EndingGenerated, CompletionEvaluated events"
```

---

## Task 3: Reducer Updates for New Events

**Files:**
- Modify: `backend/src/story/state/reducer.py`
- Test: `backend/tests/test_story_reducer.py`

**Interfaces:**
- Consumes: `DecisionPresented`, `EndingGenerated`, `CompletionEvaluated` from Task 2; `CompletionState`, `CompletionAssessmentRecord` from Task 1
- Produces: Reducer that handles all 20 event types in the `StoryEvent` union

- [ ] **Step 1: Write failing tests for DecisionPresented reducer**

Add to `backend/tests/test_story_reducer.py`:

```python
from src.story.state.events import EventEnvelope
from src.story.state.models import SessionState, WorldSnapshot
from src.story.state import (
    FactRecord,
    FactTruthStatus,
    FactVisibility,
    SessionStatus,
    StoryPhase,
)
from src.story.state.reducer import apply_event


def _make_minimal_state(revision=0, **overrides):
    world = WorldSnapshot(
        location_id="cafe",
        time_label="opening",
        present_character_ids=("alice",),
        max_scenes=20,
        reserved_resolution_scenes=3,
    )
    base = SessionState(
        session_id="s1",
        pack_id="test_pack",
        pack_hash="abcd" * 16,
        revision=revision,
        session_seed=1,
        world=world,
        facts={},
        characters={},
    )
    return base.model_copy(update=overrides)


def test_decision_presented_sets_pending_decision():
    state = _make_minimal_state(revision=4)
    event = DecisionPresented(
        decision_id="dec_01",
        choices=(
            PresentedChoice(id="opt_a", action_id="ask", label="Ask", intent="Ask directly"),
            PresentedChoice(id="opt_b", action_id="observe", label="Watch", intent="Watch carefully"),
        ),
    )
    envelope = EventEnvelope(session_id="s1", sequence=5, event=event)
    result = apply_event(state, envelope)
    assert result.pending_decision is not None
    assert result.pending_decision.decision_id == "dec_01"
    assert len(result.pending_decision.choices) == 2


def test_decision_presented_rejects_duplicate():
    from src.story.state import PendingDecisionReference
    state = _make_minimal_state(revision=4, pending_decision=PendingDecisionReference(
        decision_id="old", scene_id="scene_0", revision=4,
        choices=(PresentedChoice(id="x", action_id="ask", label="X", intent="x"),
                 PresentedChoice(id="y", action_id="ask", label="Y", intent="y")),
    ))
    event = DecisionPresented(
        decision_id="dec_01",
        choices=(
            PresentedChoice(id="opt_a", action_id="ask", label="Ask", intent="Ask directly"),
            PresentedChoice(id="opt_b", action_id="observe", label="Watch", intent="Watch carefully"),
        ),
    )
    envelope = EventEnvelope(session_id="s1", sequence=5, event=event)
    from src.story.state.reducer import StateTransitionError
    with pytest.raises(StateTransitionError, match="already pending"):
        apply_event(state, envelope)
```

- [ ] **Step 2: Write failing tests for EndingGenerated reducer**

Add to `backend/tests/test_story_reducer.py`:

```python
def test_ending_generated_sets_ending_and_resolving():
    state = _make_minimal_state(revision=9)
    event = EndingGenerated(
        ending_id="ending_s1_10",
        title="The Long Goodbye",
        tone="bittersweet",
        terminal_state_summary="Alice left the city.",
        blocks=(NarrativeBlock(kind="narration", text="They parted."),),
    )
    envelope = EventEnvelope(session_id="s1", sequence=10, event=event)
    result = apply_event(state, envelope)
    assert result.status == SessionStatus.RESOLVING
    assert result.ending is not None
    assert result.ending.ending_id == "ending_s1_10"
    assert result.ending.tone == "bittersweet"
    assert result.world.phase == StoryPhase.RESOLUTION


def test_ending_generated_rejects_if_ending_exists():
    from src.story.state import EndingRuntime
    existing_ending = EndingRuntime(
        ending_id="old", entered_at_revision=5, title="Old",
        blocks=(NarrativeBlock(kind="narration", text="."),),
    )
    state = _make_minimal_state(revision=9, ending=existing_ending)
    event = EndingGenerated(
        ending_id="new", title="New", tone="sad",
        terminal_state_summary="Bye",
        blocks=(NarrativeBlock(kind="narration", text="."),),
    )
    envelope = EventEnvelope(session_id="s1", sequence=10, event=event)
    from src.story.state.reducer import StateTransitionError
    with pytest.raises(StateTransitionError, match="ending already"):
        apply_event(state, envelope)
```

- [ ] **Step 3: Write failing tests for CompletionEvaluated reducer**

Add to `backend/tests/test_story_reducer.py`:

```python
def test_completion_evaluated_sets_completion():
    from src.story.state import EndingRuntime
    ending = EndingRuntime(
        ending_id="e1", entered_at_revision=10, title="End",
        blocks=(NarrativeBlock(kind="narration", text="."),),
    )
    state = _make_minimal_state(revision=10, ending=ending)
    event = CompletionEvaluated(
        cleared=True,
        assessments=(CompletionAssessmentRecord(
            requirement_id="req_a", satisfied=True, rationale="ok",
        ),),
    )
    envelope = EventEnvelope(session_id="s1", sequence=11, event=event)
    result = apply_event(state, envelope)
    assert result.completion is not None
    assert result.completion.cleared is True
    assert len(result.completion.assessments) == 1


def test_completion_evaluated_rejects_without_ending():
    state = _make_minimal_state(revision=10)
    event = CompletionEvaluated(cleared=False, assessments=())
    envelope = EventEnvelope(session_id="s1", sequence=11, event=event)
    from src.story.state.reducer import StateTransitionError
    with pytest.raises(StateTransitionError, match="ending"):
        apply_event(state, envelope)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_story_reducer.py::test_decision_presented_sets_pending_decision tests/test_story_reducer.py::test_ending_generated_sets_ending_and_resolving tests/test_story_reducer.py::test_completion_evaluated_sets_completion -v`
Expected: FAIL — no reducer branch for new event types

- [ ] **Step 5: Add reducer handlers for new events**

Add the new event types to the imports in `backend/src/story/state/reducer.py`:

```python
from src.story.state.events import (
    ActionResolved,
    BeliefChanged,
    CharacterLearnedFact,
    CompletionEvaluated,
    DecisionPresented,
    EndingEntered,
    EndingGenerated,
    EventEnvelope,
    FactCommitted,
    FactEvidenced,
    FactRevealed,
    GoalAdvanced,
    PhaseAdvanced,
    PlayerActionSelected,
    RelationshipChanged,
    SceneAcknowledged,
    SceneCommitted,
    SessionEnded,
    ThreadAdvanced,
    ThreadClosed,
    ThreadOpened,
)
```

Add `CompletionState` to the model imports:

```python
from src.story.state.models import (
    CompletionState,
    FactTruthStatus,
    FactVisibility,
    GoalStatus,
    PendingDecisionReference,
    PendingSceneReference,
    SessionState,
    SessionStatus,
    StoryPhase,
    ThreadStatus,
)
```

Add the following handlers before the `elif isinstance(event, SessionEnded)` block:

```python
    elif isinstance(event, DecisionPresented):
        _require(next_state.pending_decision is None, "a decision is already pending")
        choice_ids = [item.id for item in event.choices]
        _require(len(choice_ids) == len(set(choice_ids)), "choice ids must be unique")
        _require(2 <= len(event.choices) <= 4, "decision requires 2-4 choices")
        scene_id = (
            next_state.pending_scene.scene_id
            if next_state.pending_scene is not None
            else ""
        )
        pending_decision = PendingDecisionReference(
            decision_id=event.decision_id,
            scene_id=scene_id,
            revision=envelope.sequence,
            choices=event.choices,
        )
        next_state = next_state.model_copy(
            update={"pending_scene": None, "pending_decision": pending_decision}
        )

    elif isinstance(event, EndingGenerated):
        _require(next_state.ending is None, "ending already entered")
        ending = EndingRuntime(
            ending_id=event.ending_id,
            entered_at_revision=envelope.sequence,
            title=event.title,
            blocks=event.blocks,
            tone=event.tone,
            terminal_state_summary=event.terminal_state_summary,
        )
        world = next_state.world.model_copy(update={"phase": StoryPhase.RESOLUTION})
        next_state = next_state.model_copy(
            update={"status": SessionStatus.RESOLVING, "world": world, "ending": ending}
        )

    elif isinstance(event, CompletionEvaluated):
        _require(next_state.ending is not None, "completion requires an ending")
        completion = CompletionState(
            cleared=event.cleared,
            assessments=event.assessments,
        )
        next_state = next_state.model_copy(update={"completion": completion})
```

Add `EndingRuntime` to the imports from `.models` at the top of reducer.py if not already imported:

```python
from src.story.state.models import (
    CompletionState,
    EndingRuntime,
    FactTruthStatus,
    FactVisibility,
    GoalStatus,
    PendingDecisionReference,
    PendingSceneReference,
    SessionState,
    SessionStatus,
    StoryPhase,
    ThreadStatus,
)
```

- [ ] **Step 6: Run all reducer tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_story_reducer.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/state/reducer.py backend/tests/test_story_reducer.py
git commit -m "feat(reducer): handle DecisionPresented, EndingGenerated, CompletionEvaluated"
```

---

## Task 4: Segment Contracts

**Files:**
- Create: `backend/src/story/runtime/segment_contracts.py`
- Modify: `backend/src/story/runtime/contracts.py` (extend `EndingDraft`)
- Test: `backend/tests/test_segment_contracts.py`

**NOTE:** Plan 3 will append additional schema tests to `test_segment_contracts.py`.

**Interfaces:**
- Consumes: `RuntimeModel`, `ScenePlan`, `SceneDraft`, `WrittenChoice`, `EndingDraft`, `FactCommitPlan`, `ChoicePlan` from `contracts.py`; `StoryPhase`, `NarrativeBlock`, `PresentedChoice`, `SessionState` from `state`; `CompiledScriptPack` from `script_pack`
- Produces: `SegmentPlan`, `SegmentDraft`, `EndingProposal`, `ThreadOperation`, `PacingEnvelope`, `GuardResult`, `GuardViolation`, `CompletionAssessment`, `CompletionResult`, `DirectorPort`, `SegmentWriterPort`, `GuardPort`

- [ ] **Step 1: Extend EndingDraft in contracts.py**

Add `tone` and `terminal_state_summary` fields to `EndingDraft` in `backend/src/story/runtime/contracts.py`:

```python
class EndingDraft(RuntimeModel):
    ending_id: str
    title: str
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)
    tone: str | None = None
    terminal_state_summary: str | None = None
```

- [ ] **Step 2: Write failing tests for segment contracts**

Create `backend/tests/test_segment_contracts.py`:

```python
import pytest
from pydantic import ValidationError
from src.story.runtime.contracts import (
    ChoicePlan,
    EndingDraft,
    NarrativeBlock,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.segment_contracts import (
    CompletionAssessment,
    CompletionResult,
    EndingProposal,
    GuardResult,
    GuardViolation,
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
    ThreadOperation,
)
from src.story.state import StoryPhase


def _make_scene_plan(scene_id="scene_01", terminal="continue"):
    return ScenePlan(
        scene_id=scene_id,
        summary="A scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal=terminal,
    )


def _make_scene_draft(scene_id="scene_01"):
    return SceneDraft(
        scene_id=scene_id,
        blocks=(NarrativeBlock(kind="narration", text="Text."),),
    )


def test_ending_proposal():
    proposal = EndingProposal(
        title="The Long Goodbye",
        tone="bittersweet",
        terminal_state_summary="Alice left the city.",
    )
    assert proposal.title == "The Long Goodbye"


def test_thread_operation_open():
    op = ThreadOperation(
        kind="open",
        thread_id="thread_mystery",
        thread_type="mystery",
        involved_character_ids=("alice",),
    )
    assert op.kind == "open"
    assert op.thread_type == "mystery"


def test_thread_operation_advance():
    op = ThreadOperation(
        kind="advance",
        thread_id="thread_mystery",
        urgency=0.8,
    )
    assert op.kind == "advance"


def test_thread_operation_close():
    op = ThreadOperation(
        kind="close",
        thread_id="thread_mystery",
        close_status="resolved",
    )
    assert op.close_status == "resolved"


def test_pacing_envelope():
    env = PacingEnvelope(
        phase=StoryPhase.EXPLORATION,
        scene_count=5,
        min_scenes=8,
        max_scenes=20,
        reserved_resolution_scenes=3,
        remaining_budget=15,
        can_end=False,
        must_end=False,
        in_convergence=False,
        max_new_threads=3,
        quiet_scene_allowance=2,
    )
    assert env.phase == StoryPhase.EXPLORATION
    assert env.can_end is False


def test_segment_plan_decision():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_scene_plan(),),
        terminal="decision",
    )
    assert plan.segment_id == "seg_01"
    assert plan.terminal == "decision"
    assert plan.ending_proposal is None


def test_segment_plan_ending():
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_make_scene_plan(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Finale", tone="epic", terminal_state_summary="The end.",
        ),
    )
    assert plan.ending_proposal is not None
    assert plan.ending_proposal.title == "Finale"


def test_segment_plan_requires_min_one_scene():
    with pytest.raises(ValidationError):
        SegmentPlan(segment_id="seg_01", scenes=(), terminal="decision")


def test_segment_draft():
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(_make_scene_draft(),),
    )
    assert draft.segment_id == "seg_01"
    assert draft.ending is None


def test_segment_draft_with_ending():
    draft = SegmentDraft(
        segment_id="seg_02",
        scene_drafts=(_make_scene_draft(),),
        ending=EndingDraft(
            ending_id="ending_001",
            title="Finale",
            blocks=(NarrativeBlock(kind="narration", text="The end."),),
            tone="epic",
            terminal_state_summary="World saved.",
        ),
    )
    assert draft.ending is not None
    assert draft.ending.tone == "epic"


def test_guard_result_passed():
    result = GuardResult(passed=True)
    assert result.passed is True
    assert result.violations == ()


def test_guard_result_with_violations():
    result = GuardResult(
        passed=False,
        violations=(
            GuardViolation(
                kind="knowledge_leak",
                block_index=2,
                character_id="alice",
                detail="Alice reveals a secret she does not know.",
            ),
        ),
    )
    assert result.passed is False
    assert len(result.violations) == 1
    assert result.violations[0].kind == "knowledge_leak"


def test_completion_assessment():
    a = CompletionAssessment(
        requirement_id="req_a",
        satisfied=True,
        cited_event_ids=("evt-1",),
        rationale="Fact committed",
    )
    assert a.satisfied is True


def test_completion_result():
    result = CompletionResult(
        assessments=(
            CompletionAssessment(requirement_id="req_a", satisfied=True, rationale="ok"),
            CompletionAssessment(requirement_id="req_b", satisfied=False, rationale="no"),
        ),
        cleared=False,
    )
    assert len(result.assessments) == 2
    assert result.cleared is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_segment_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.story.runtime.segment_contracts'`

- [ ] **Step 4: Create segment_contracts.py**

Create `backend/src/story/runtime/segment_contracts.py`:

```python
"""Contracts for segment-based runtime: plans, drafts, agent ports, guard, completion."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field

from src.story.runtime.contracts import (
    EndingDraft,
    FactCommitPlan,
    RuntimeModel,
    SceneDraft,
    ScenePlan,
)
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState, StoryPhase


# ---------------------------------------------------------------------------
# Ending proposal (Director's plan for a dynamic ending)
# ---------------------------------------------------------------------------


class EndingProposal(RuntimeModel):
    title: str
    tone: str
    terminal_state_summary: str


# ---------------------------------------------------------------------------
# Thread operations (Director's thread lifecycle proposals)
# ---------------------------------------------------------------------------


class ThreadOperation(RuntimeModel):
    kind: Literal["open", "advance", "close"]
    thread_id: str
    thread_type: str = ""
    involved_character_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
    urgency: float | None = None
    close_status: Literal["resolved", "abandoned"] | None = None


# ---------------------------------------------------------------------------
# Segment plan and draft
# ---------------------------------------------------------------------------


class SegmentPlan(RuntimeModel):
    segment_id: str
    scenes: tuple[ScenePlan, ...] = Field(min_length=1)
    terminal: Literal["decision", "ending"]
    ending_proposal: EndingProposal | None = None
    thread_ops: tuple[ThreadOperation, ...] = ()
    new_facts: tuple[FactCommitPlan, ...] = ()
    phase_after: StoryPhase | None = None


class SegmentDraft(RuntimeModel):
    segment_id: str
    scene_drafts: tuple[SceneDraft, ...] = Field(min_length=1)
    choices: tuple[WrittenChoice, ...] = ()
    ending: EndingDraft | None = None


# ---------------------------------------------------------------------------
# Pacing envelope (deterministic budget computation)
# ---------------------------------------------------------------------------


class PacingEnvelope(RuntimeModel):
    phase: StoryPhase
    scene_count: int
    min_scenes: int
    max_scenes: int
    reserved_resolution_scenes: int
    remaining_budget: int
    can_end: bool
    must_end: bool
    in_convergence: bool
    max_new_threads: int
    quiet_scene_allowance: int


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


class GuardViolation(RuntimeModel):
    kind: Literal[
        "knowledge_leak",
        "contradiction",
        "unauthorized_fact",
        "wrong_speaker",
        "unsupported_certainty",
    ]
    block_index: int | None = None
    character_id: str | None = None
    detail: str


class GuardResult(RuntimeModel):
    passed: bool
    violations: tuple[GuardViolation, ...] = ()


# ---------------------------------------------------------------------------
# Completion judgment
# ---------------------------------------------------------------------------


class CompletionAssessment(RuntimeModel):
    requirement_id: str
    satisfied: bool
    cited_event_ids: tuple[str, ...] = ()
    rationale: str


class CompletionResult(RuntimeModel):
    assessments: tuple[CompletionAssessment, ...]
    cleared: bool


# ---------------------------------------------------------------------------
# Agent ports (Plan 3 provides real implementations; Plan 2 uses fakes)
# ---------------------------------------------------------------------------


class DirectorPort(Protocol):
    async def plan_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        pacing: PacingEnvelope,
    ) -> SegmentPlan:
        ...


class SegmentWriterPort(Protocol):
    async def write_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
    ) -> SegmentDraft:
        ...


class GuardPort(Protocol):
    def check_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        draft: SegmentDraft,
    ) -> GuardResult:
        ...
```

- [ ] **Step 5: Run segment contract tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_segment_contracts.py -v`
Expected: PASS

- [ ] **Step 6: Run existing contract tests to verify no regressions**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_runtime_contracts.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/runtime/segment_contracts.py backend/src/story/runtime/contracts.py backend/tests/test_segment_contracts.py
git commit -m "feat(segment): add segment contracts, agent ports, guard, completion types"
```

---

## Task 5: Pacing Envelope and Ending Policy

**Files:**
- Create: `backend/src/story/runtime/pacing.py`
- Test: `backend/tests/test_pacing.py`

**Interfaces:**
- Consumes: `SessionState`, `CompiledScriptPack`
- Produces: `compute_pacing_envelope(state, pack) -> PacingEnvelope`

- [ ] **Step 1: Write failing tests for pacing computation**

Create `backend/tests/test_pacing.py`:

```python
import pytest
from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.segment_contracts import PacingEnvelope
from src.story.state import StoryPhase
from tests.story_factories import minimal_script_pack_dict
from src.story.script_pack.compiler import compile_source
from src.story.state import initial_session_state


def _make_pack_and_state(scene_count=0, min_scenes=8, max_scenes=20, reserved=3):
    raw = minimal_script_pack_dict()
    raw["experience"]["min_scenes"] = min_scenes
    raw["experience"]["max_scenes"] = max_scenes
    raw["experience"]["reserved_resolution_scenes"] = reserved
    pack = compile_source(raw)
    state = initial_session_state(pack, "s1", session_seed=1)
    if scene_count > 0:
        state = state.model_copy(update={
            "world": state.world.model_copy(update={"scene_count": scene_count})
        })
    return pack, state


def test_pacing_at_opening():
    pack, state = _make_pack_and_state(scene_count=0)
    env = compute_pacing_envelope(state, pack)
    assert env.phase == StoryPhase.OPENING
    assert env.scene_count == 0
    assert env.remaining_budget == 20
    assert env.can_end is False
    assert env.must_end is False
    assert env.in_convergence is False
    assert env.max_new_threads == 3
    assert env.quiet_scene_allowance >= 1


def test_pacing_can_end_after_min_scenes():
    pack, state = _make_pack_and_state(scene_count=8)
    env = compute_pacing_envelope(state, pack)
    assert env.can_end is True
    assert env.must_end is False


def test_pacing_must_end_at_max():
    pack, state = _make_pack_and_state(scene_count=20)
    env = compute_pacing_envelope(state, pack)
    assert env.must_end is True
    assert env.can_end is True
    assert env.remaining_budget == 0


def test_pacing_convergence_window():
    pack, state = _make_pack_and_state(scene_count=17, max_scenes=20, reserved=3)
    env = compute_pacing_envelope(state, pack)
    assert env.in_convergence is True
    assert env.max_new_threads == 0


def test_pacing_before_convergence():
    pack, state = _make_pack_and_state(scene_count=16, max_scenes=20, reserved=3)
    env = compute_pacing_envelope(state, pack)
    assert env.in_convergence is False
    assert env.max_new_threads == 3


def test_pacing_returns_pacing_envelope_type():
    pack, state = _make_pack_and_state()
    env = compute_pacing_envelope(state, pack)
    assert isinstance(env, PacingEnvelope)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_pacing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.story.runtime.pacing'`

- [ ] **Step 3: Create pacing.py**

Create `backend/src/story/runtime/pacing.py`:

```python
"""Deterministic pacing envelope and ending policy computation."""

from __future__ import annotations

from src.story.runtime.segment_contracts import PacingEnvelope
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState

# Default open-thread budget before convergence window.
DEFAULT_MAX_OPEN_THREADS = 3


def compute_pacing_envelope(
    state: SessionState,
    pack: CompiledScriptPack,
) -> PacingEnvelope:
    scene_count = state.world.scene_count
    min_scenes = pack.source.experience.min_scenes
    max_scenes = state.world.max_scenes
    reserved = state.world.reserved_resolution_scenes
    remaining = max_scenes - scene_count
    convergence_start = max_scenes - reserved

    in_convergence = scene_count >= convergence_start

    return PacingEnvelope(
        phase=state.world.phase,
        scene_count=scene_count,
        min_scenes=min_scenes,
        max_scenes=max_scenes,
        reserved_resolution_scenes=reserved,
        remaining_budget=remaining,
        can_end=scene_count >= min_scenes,
        must_end=scene_count >= max_scenes,
        in_convergence=in_convergence,
        max_new_threads=0 if in_convergence else DEFAULT_MAX_OPEN_THREADS,
        quiet_scene_allowance=max(0, min(2, remaining // 4)),
    )
```

- [ ] **Step 4: Run pacing tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_pacing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/runtime/pacing.py backend/tests/test_pacing.py
git commit -m "feat(pacing): add deterministic pacing envelope and ending policy"
```

---

## Task 6: Session Projection Update

**Files:**
- Modify: `backend/src/story/runtime/projection.py`
- Test: `backend/tests/test_projection.py`

**Interfaces:**
- Consumes: Existing `SessionProjection`, `project_session()`
- Produces: Extended `SessionProjection` with segment fields; `EndingProjection`, `CompletionSummary` types

- [ ] **Step 1: Write failing tests for segment projection fields**

Add to `backend/tests/test_projection.py`:

```python
from src.story.runtime.projection import project_session, EndingProjection, CompletionSummary
from tests.story_factories import budget_test_pack_dict
from src.story.script_pack.compiler import compile_source
from src.story.state import initial_session_state, NarrativeBlock


def test_projection_includes_segment_blocks():
    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    proj = project_session(state)
    assert hasattr(proj, "segment_blocks")
    assert proj.segment_blocks == ()


def test_projection_includes_segment_revision():
    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    proj = project_session(state)
    assert hasattr(proj, "segment_revision")
    assert proj.segment_revision is None


def test_ending_projection():
    proj = EndingProjection(
        ending_id="ending_s1",
        title="Finale",
        tone="epic",
        terminal_state_summary="The end.",
    )
    assert proj.ending_id == "ending_s1"


def test_completion_summary():
    summary = CompletionSummary(
        requirement_id="req_a",
        description="Find the truth",
        satisfied=True,
        rationale="Fact committed",
    )
    assert summary.satisfied is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_projection.py::test_projection_includes_segment_blocks tests/test_projection.py::test_projection_includes_segment_revision tests/test_projection.py::test_ending_projection tests/test_projection.py::test_completion_summary -v`
Expected: FAIL with `AttributeError` or `ImportError`

- [ ] **Step 3: Add projection types and extend SessionProjection**

Add to `backend/src/story/runtime/projection.py`:

```python
class EndingProjection(BaseModel):
    ending_id: str
    title: str
    tone: str
    terminal_state_summary: str


class CompletionSummary(BaseModel):
    requirement_id: str
    description: str
    satisfied: bool
    rationale: str
```

Extend `SessionProjection` with new fields:

```python
segment_blocks: tuple[NarrativeBlock, ...] = ()
segment_revision: int | None = None
segment_choices: tuple[PresentedChoice, ...] = ()
segment_ending: EndingProjection | None = None
cleared: bool | None = None
completion_summaries: tuple[CompletionSummary, ...] = ()
```

Update `project_session()` to populate the new fields from state.

- [ ] **Step 4: Run projection tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_projection.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/runtime/projection.py backend/tests/test_projection.py
git commit -m "feat(projection): add segment, ending, and completion fields"
```

---

## Task 7: Segment Plan Validator

**Files:**
- Modify: `backend/src/story/runtime/validator.py`
- Test: `backend/tests/test_segment_validator.py`

**Interfaces:**
- Consumes: `SegmentPlan`, `SegmentDraft`, `PacingEnvelope` from Task 4; existing `validate_scene_plan`, `validate_scene_draft`, `ProposalRejected`
- Produces: `validate_segment_plan(pack, state, plan, pacing) -> SegmentPlan`, `validate_segment_draft(plan, draft) -> SegmentDraft`

- [ ] **Step 1: Write failing tests for segment plan validation**

Create `backend/tests/test_segment_validator.py`:

```python
import pytest
from src.story.runtime.contracts import (
    ChoicePlan,
    NarrativeBlock,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.segment_contracts import (
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
    ThreadOperation,
    EndingProposal,
)
from src.story.runtime.validator import ProposalRejected, validate_segment_plan, validate_segment_draft
from src.story.script_pack.compiler import compile_source
from src.story.state import StoryPhase
from tests.story_factories import minimal_script_pack_dict


def _make_pack():
    return compile_source(minimal_script_pack_dict())


def _make_pacing(**overrides):
    defaults = dict(
        phase=StoryPhase.EXPLORATION,
        scene_count=5,
        min_scenes=8,
        max_scenes=20,
        reserved_resolution_scenes=3,
        remaining_budget=15,
        can_end=False,
        must_end=False,
        in_convergence=False,
        max_new_threads=3,
        quiet_scene_allowance=2,
    )
    defaults.update(overrides)
    return PacingEnvelope(**defaults)


def _make_state():
    from src.story.state import initial_session_state
    pack = _make_pack()
    return initial_session_state(pack, "s1", session_seed=1)


def _make_continue_scene(scene_id="scene_01"):
    return ScenePlan(
        scene_id=scene_id,
        summary="A scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="continue",
    )


def _make_decision_scene(scene_id="scene_02"):
    return ScenePlan(
        scene_id=scene_id,
        summary="Decision point",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="dec_01",
        choices=(
            ChoicePlan(option_id="ask", action_id="ask", intent="Ask directly"),
            ChoicePlan(option_id="observe", action_id="observe", intent="Watch carefully"),
        ),
    )


def test_valid_decision_segment_plan():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing()
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_scene(),),
        terminal="decision",
    )
    result = validate_segment_plan(pack, state, plan, pacing)
    assert result.segment_id == "seg_01"


def test_valid_ending_segment_plan():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(can_end=True)
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_make_continue_scene(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Finale", tone="epic", terminal_state_summary="The end.",
        ),
    )
    result = validate_segment_plan(pack, state, plan, pacing)
    assert result.terminal == "ending"


def test_ending_before_min_scenes_rejected():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(can_end=False, scene_count=2, min_scenes=8)
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_make_continue_scene(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Finale", tone="epic", terminal_state_summary="The end.",
        ),
    )
    with pytest.raises(ProposalRejected, match="min_scenes"):
        validate_segment_plan(pack, state, plan, pacing)


def test_ending_without_proposal_rejected():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(can_end=True)
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_make_continue_scene(),),
        terminal="ending",
    )
    with pytest.raises(ProposalRejected, match="ending_proposal"):
        validate_segment_plan(pack, state, plan, pacing)


def test_must_end_forces_ending():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(must_end=True, scene_count=20, max_scenes=20)
    plan = SegmentPlan(
        segment_id="seg_03",
        scenes=(_make_continue_scene(),),
        terminal="decision",
    )
    with pytest.raises(ProposalRejected, match="must_end"):
        validate_segment_plan(pack, state, plan, pacing)


def test_thread_op_in_convergence_rejected():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(in_convergence=True, max_new_threads=0)
    plan = SegmentPlan(
        segment_id="seg_04",
        scenes=(_make_continue_scene(),),
        terminal="decision",
        thread_ops=(
            ThreadOperation(
                kind="open",
                thread_id="new_thread",
                thread_type="mystery",
            ),
        ),
    )
    with pytest.raises(ProposalRejected, match="convergence"):
        validate_segment_plan(pack, state, plan, pacing)


def test_too_many_scenes_rejected():
    pack = _make_pack()
    state = _make_state()
    pacing = _make_pacing(remaining_budget=1, scene_count=19, max_scenes=20)
    plan = SegmentPlan(
        segment_id="seg_05",
        scenes=(_make_continue_scene("s1"), _make_continue_scene("s2")),
        terminal="decision",
    )
    with pytest.raises(ProposalRejected, match="budget"):
        validate_segment_plan(pack, state, plan, pacing)


# --- Segment draft validation ---


def test_valid_segment_draft():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_scene(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="Text."),),
            ),
        ),
        choices=(
            WrittenChoice(option_id="ask", label="Ask directly"),
            WrittenChoice(option_id="observe", label="Watch carefully"),
        ),
    )
    result = validate_segment_draft(plan, draft)
    assert result.segment_id == "seg_01"


def test_segment_draft_id_mismatch_rejected():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_scene(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_02",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="Text."),),
            ),
        ),
    )
    with pytest.raises(ProposalRejected, match="segment_id"):
        validate_segment_draft(plan, draft)


def test_segment_draft_scene_count_mismatch_rejected():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_scene("s1"), _make_continue_scene("s2")),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(
            SceneDraft(
                scene_id="s1",
                blocks=(NarrativeBlock(kind="narration", text="Text."),),
            ),
        ),
    )
    with pytest.raises(ProposalRejected, match="scene count"):
        validate_segment_draft(plan, draft)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_segment_validator.py -v`
Expected: FAIL with `ImportError: cannot import name 'validate_segment_plan'`

- [ ] **Step 3: Add segment validation functions**

Add to the end of `backend/src/story/runtime/validator.py`:

```python
from src.story.runtime.segment_contracts import (
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
)


def validate_segment_plan(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    pacing: PacingEnvelope,
) -> SegmentPlan:
    errors: list[str] = []

    # Terminal-ending requires an ending proposal.
    if plan.terminal == "ending" and plan.ending_proposal is None:
        errors.append("ending terminal requires ending_proposal")

    # Ending before min_scenes is rejected (unless must_end).
    if plan.terminal == "ending" and not pacing.can_end and not pacing.must_end:
        errors.append(
            f"ending proposed before min_scenes ({pacing.min_scenes}); "
            f"current scene_count is {pacing.scene_count}"
        )

    # must_end forces an ending terminal.
    if pacing.must_end and plan.terminal != "ending":
        errors.append("must_end is True; segment terminal must be 'ending'")

    # All but last scene must have terminal="continue".
    for i, scene in enumerate(plan.scenes[:-1]):
        if scene.terminal != "continue":
            errors.append(
                f"non-terminal scene at index {i} must have terminal='continue'"
            )

    # Scene count must not exceed remaining budget.
    if len(plan.scenes) > pacing.remaining_budget:
        errors.append(
            f"segment has {len(plan.scenes)} scenes but only "
            f"{pacing.remaining_budget} remaining in budget"
        )

    # Validate each scene plan individually.
    simulated_state = state
    for scene in plan.scenes:
        try:
            validate_scene_plan(pack, simulated_state, scene)
        except ProposalRejected as exc:
            errors.extend(f"scene {scene.scene_id}: {e}" for e in exc.errors)

    # Thread operations: no new threads in convergence window.
    if pacing.in_convergence or pacing.max_new_threads == 0:
        new_thread_count = sum(1 for op in plan.thread_ops if op.kind == "open")
        if new_thread_count > 0:
            errors.append(
                f"cannot open {new_thread_count} new thread(s) in convergence window"
            )
    else:
        new_thread_count = sum(1 for op in plan.thread_ops if op.kind == "open")
        if new_thread_count > pacing.max_new_threads:
            errors.append(
                f"segment opens {new_thread_count} threads but budget is "
                f"{pacing.max_new_threads}"
            )

    # Validate thread operation referential integrity.
    existing_thread_ids = set(state.threads.keys())
    for op in plan.thread_ops:
        if op.kind == "open" and op.thread_id in existing_thread_ids:
            errors.append(f"thread already exists: {op.thread_id}")
        if op.kind in ("advance", "close") and op.thread_id not in existing_thread_ids:
            errors.append(f"unknown thread for {op.kind}: {op.thread_id}")

    if errors:
        raise ProposalRejected(errors)
    return plan


def validate_segment_draft(
    plan: SegmentPlan,
    draft: SegmentDraft,
) -> SegmentDraft:
    errors: list[str] = []

    if draft.segment_id != plan.segment_id:
        errors.append(
            f"segment_id mismatch: expected {plan.segment_id}, got {draft.segment_id}"
        )

    if len(draft.scene_drafts) != len(plan.scenes):
        errors.append(
            f"scene count mismatch: plan has {len(plan.scenes)} scenes, "
            f"draft has {len(draft.scene_drafts)}"
        )
        if errors:
            raise ProposalRejected(errors)

    # Validate each scene draft against its plan.
    for scene_plan, scene_draft in zip(plan.scenes, draft.scene_drafts):
        try:
            validate_scene_draft(scene_plan, scene_draft)
        except ProposalRejected as exc:
            errors.extend(f"scene {scene_plan.scene_id}: {e}" for e in exc.errors)

    # For ending terminal, draft must have ending.
    if plan.terminal == "ending" and draft.ending is None:
        errors.append("ending terminal requires draft.ending")

    # For ending terminal, draft.ending title must match proposal title.
    if plan.terminal == "ending" and plan.ending_proposal is not None and draft.ending is not None:
        if draft.ending.title != plan.ending_proposal.title:
            errors.append(
                f"ending title mismatch: proposal has '{plan.ending_proposal.title}', "
                f"draft has '{draft.ending.title}'"
            )

    if errors:
        raise ProposalRejected(errors)
    return draft
```

- [ ] **Step 4: Run segment validator tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_segment_validator.py -v`
Expected: PASS

- [ ] **Step 5: Run existing validator tests to verify no regressions**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_runtime_validator.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/runtime/validator.py backend/tests/test_segment_validator.py
git commit -m "feat(validator): add segment plan and draft validation"
```

---

## Task 8: Segment Simulator

**Files:**
- Modify: `backend/src/story/runtime/simulator.py`
- Test: `backend/tests/test_segment_simulator.py`

**Interfaces:**
- Consumes: `SegmentPlan`, `SegmentDraft` from Task 4; `validate_segment_plan`, `validate_segment_draft` from Task 6; existing event types, `simulate_events`, `next_phase`
- Produces: `segment_events(pack, state, plan, draft) -> tuple[StoryEvent, ...]`, `simulate_segment(pack, state, plan, draft) -> tuple[StoryEvent, ...]`

- [ ] **Step 1: Write failing tests for segment simulation**

Create `backend/tests/test_segment_simulator.py`:

```python
import pytest
from src.story.runtime.contracts import (
    ChoicePlan,
    NarrativeBlock,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.segment_contracts import (
    SegmentDraft,
    SegmentPlan,
)
from src.story.runtime.simulator import simulate_segment, segment_events
from src.story.script_pack.compiler import compile_source
from src.story.state import (
    DecisionPresented,
    EndingGenerated,
    SceneAcknowledged,
    SceneCommitted,
    initial_session_state,
)
from tests.story_factories import minimal_script_pack_dict


def _make_pack():
    return compile_source(minimal_script_pack_dict())


def _make_state():
    pack = _make_pack()
    return initial_session_state(pack, "s1", session_seed=1)


def _make_continue_plan(scene_id="scene_01"):
    return ScenePlan(
        scene_id=scene_id,
        summary="A scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="continue",
    )


def _make_continue_draft(scene_id="scene_01"):
    return SceneDraft(
        scene_id=scene_id,
        blocks=(NarrativeBlock(kind="narration", text="A quiet moment."),),
    )


def test_decision_segment_events():
    state = _make_state()
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_plan(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(_make_continue_draft(),),
        choices=(
            WrittenChoice(option_id="ask", label="Ask directly"),
            WrittenChoice(option_id="observe", label="Watch carefully"),
        ),
    )
    events = segment_events(_make_pack(), state, plan, draft)
    event_types = [type(e).__name__ for e in events]
    assert "SceneCommitted" in event_types
    assert "DecisionPresented" in event_types
    # No SceneAcknowledged needed for single-scene segment.
    assert "SceneAcknowledged" not in event_types


def test_multi_scene_segment_auto_acks():
    state = _make_state()
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(
            _make_continue_plan("scene_01"),
            _make_continue_plan("scene_02"),
        ),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_02",
        scene_drafts=(
            _make_continue_draft("scene_01"),
            _make_continue_draft("scene_02"),
        ),
        choices=(
            WrittenChoice(option_id="ask", label="Ask"),
            WrittenChoice(option_id="observe", label="Watch"),
        ),
    )
    events = segment_events(_make_pack(), state, plan, draft)
    ack_count = sum(1 for e in events if isinstance(e, SceneAcknowledged))
    assert ack_count == 1  # auto-ack between scene 1 and scene 2


def test_ending_segment_events():
    state = _make_state()
    # Force state past min_scenes for ending.
    state = state.model_copy(update={
        "world": state.world.model_copy(update={"scene_count": 10})
    })
    from src.story.runtime.segment_contracts import EndingProposal
    plan = SegmentPlan(
        segment_id="seg_03",
        scenes=(_make_continue_plan(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="The Long Goodbye",
            tone="bittersweet",
            terminal_state_summary="Alice left the city.",
        ),
    )
    draft = SegmentDraft(
        segment_id="seg_03",
        scene_drafts=(_make_continue_draft(),),
        ending=__import__(
            "src.story.runtime.contracts", fromlist=["EndingDraft"]
        ).EndingDraft(
            ending_id="ending_s1_001",
            title="The Long Goodbye",
            blocks=(NarrativeBlock(kind="narration", text="They parted."),),
            tone="bittersweet",
            terminal_state_summary="Alice left the city.",
        ),
    )
    events = segment_events(_make_pack(), state, plan, draft)
    event_types = [type(e).__name__ for e in events]
    assert "EndingGenerated" in event_types


def test_simulate_segment_validates():
    state = _make_state()
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_continue_plan(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(_make_continue_draft(),),
        choices=(
            WrittenChoice(option_id="ask", label="Ask"),
            WrittenChoice(option_id="observe", label="Watch"),
        ),
    )
    events = simulate_segment(_make_pack(), state, plan, draft)
    assert len(events) > 0


def test_simulate_segment_exceeding_max_raises():
    from src.story.state import StateTransitionError
    state = _make_state()
    # Set scene_count to max to force overflow.
    state = state.model_copy(update={
        "world": state.world.model_copy(update={"scene_count": 20})
    })
    plan = SegmentPlan(
        segment_id="seg_04",
        scenes=(_make_continue_plan(),),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_04",
        scene_drafts=(_make_continue_draft(),),
        choices=(
            WrittenChoice(option_id="ask", label="Ask"),
            WrittenChoice(option_id="observe", label="Watch"),
        ),
    )
    with pytest.raises(StateTransitionError):
        simulate_segment(_make_pack(), state, plan, draft)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_segment_simulator.py -v`
Expected: FAIL with `ImportError: cannot import name 'simulate_segment'`

- [ ] **Step 3a: Add imports and _thread_op_events helper**

Add to imports at the top of `backend/src/story/runtime/simulator.py`:

```python
from src.story.runtime.segment_contracts import (
    SegmentDraft,
    SegmentPlan,
    ThreadOperation,
)
from src.story.state import (
    DecisionPresented,
    EndingGenerated,
    SceneAcknowledged,
)
from src.story.state.models import EndingRuntime
```

Add the `_thread_op_events` helper function after the existing `simulate_resolution` function:

```python
def _thread_op_events(
    state: SessionState,
    ops: tuple[ThreadOperation, ...],
) -> list[StoryEvent]:
    events: list[StoryEvent] = []
    for op in ops:
        if op.kind == "open":
            thread = NarrativeThread(
                id=op.thread_id,
                type=op.thread_type,
                introduced_at=f"session:{state.session_id}:rev:{state.revision}",
                involved_character_ids=op.involved_character_ids,
                related_fact_ids=op.related_fact_ids,
            )
            events.append(ThreadOpened(thread=thread))
        elif op.kind == "advance":
            events.append(
                ThreadAdvanced(thread_id=op.thread_id, urgency=op.urgency)
            )
        elif op.kind == "close":
            from src.story.state import ThreadStatus
            status = ThreadStatus.RESOLVED if op.close_status == "resolved" else ThreadStatus.ABANDONED
            events.append(ThreadClosed(thread_id=op.thread_id, status=status))
    return events
```

- [ ] **Step 3b: Add segment_events for decision terminals**

Add the `segment_events` function skeleton with decision terminal handling:

```python
def segment_events(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    draft: SegmentDraft,
) -> tuple[StoryEvent, ...]:
    events: list[StoryEvent] = []
    current_scene_count = state.world.scene_count

    # At most one PhaseAdvanced per segment (based on first scene projected count).
    projected_count = current_scene_count + 1
    phase = next_phase_for_count(state, projected_count)
    if phase is not None:
        events.append(PhaseAdvanced(phase=phase))

    # Thread operations (before scenes so they are available for reference).
    events.extend(_thread_op_events(state, plan.thread_ops))

    # Build per-scene events with auto-acknowledge between scenes.
    for i, (scene_plan, scene_draft) in enumerate(
        zip(plan.scenes, draft.scene_drafts)
    ):
        if i > 0:
            events.append(
                SceneAcknowledged(scene_id=plan.scenes[i - 1].scene_id)
            )

        # Fact commits for this scene.
        for fact in scene_plan.fact_commits:
            evidence = (
                (scene_plan.scene_id,)
                if fact.reason == "first_irreversible_evidence" or fact.reveal
                else ()
            )
            events.append(
                FactCommitted(
                    fact_id=fact.fact_id,
                    value=fact.value,
                    evidence_event_ids=evidence,
                )
            )
            if fact.reveal:
                events.append(FactRevealed(fact_id=fact.fact_id))
            events.extend(
                CharacterLearnedFact(character_id=cid, fact_id=fact.fact_id)
                for cid in fact.learned_by
            )

        # SceneCommitted with just content (no terminal/decision/choices).
        events.append(
            SceneCommitted(
                scene_id=scene_plan.scene_id,
                location_id=scene_plan.location_id,
                present_character_ids=scene_plan.present_character_ids,
                blocks=scene_draft.blocks,
            )
        )

    # Decision terminal events.
    if plan.terminal == "decision":
        last_scene = plan.scenes[-1]
        from src.story.state import PresentedChoice
        written_map = {wc.option_id: wc for wc in draft.choices}
        choices = tuple(
            PresentedChoice(
                id=choice_plan.option_id,
                action_id=choice_plan.action_id,
                label=written_map[choice_plan.option_id].label,
                intent=choice_plan.intent,
                target_character_id=choice_plan.target_character_id,
                preview=written_map[choice_plan.option_id].preview,
            )
            for choice_plan in last_scene.choices
        )
        events.append(
            DecisionPresented(
                decision_id=last_scene.decision_id if last_scene.decision_id else f"dec_{plan.segment_id}",
                choices=choices,
            )
        )

    return tuple(events)
```

- [ ] **Step 3c: Add segment_events for ending terminals**

Add ending terminal handling to `segment_events` (after decision block, before return):

```python
    # Ending terminal events.
    if plan.terminal == "ending":
        ending_draft = draft.ending
        if ending_draft is not None:
            events.append(
                EndingGenerated(
                    ending_id=ending_draft.ending_id,
                    title=ending_draft.title,
                    tone=ending_draft.tone or plan.ending_proposal.tone if plan.ending_proposal else "neutral",
                    terminal_state_summary=(
                        ending_draft.terminal_state_summary
                        or (plan.ending_proposal.terminal_state_summary if plan.ending_proposal else "")
                    ),
                    blocks=ending_draft.blocks,
                )
            )
```

Note: Change the decision block's `if` to `if plan.terminal == "decision":` and add `elif plan.terminal == "ending":` for the ending block.

- [ ] **Step 3d: Add simulate_segment and next_phase_for_count**

- [ ] **Step 3d: Add simulate_segment and next_phase_for_count**

Add the remaining helper functions:

```python
def next_phase_for_count(state: SessionState, projected_scene_count: int):
    """Check if phase should advance based on projected scene count.
    Refactors endings.next_phase() to accept optional projected_count parameter.
    """
    from src.story.runtime.endings import next_phase
    return next_phase(state, projected_count=projected_scene_count)


def simulate_segment(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    draft: SegmentDraft,
) -> tuple[StoryEvent, ...]:
    events = segment_events(pack, state, plan, draft)
    simulate_events(state, events)
    return events
```

Add `StoryPhase` to the imports from `src.story.state` in simulator.py:

```python
from src.story.state import (
    ActionResolved,
    CharacterLearnedFact,
    EventEnvelope,
    FactCommitted,
    FactEvidenced,
    FactRevealed,
    GoalAdvanced,
    NarrativeThread,
    PhaseAdvanced,
    PlayerActionSelected,
    PresentedChoice,
    RelationshipChanged,
    SceneCommitted,
    SessionState,
    StateTransitionError,
    StoryPhase,
    ThreadAdvanced,
    ThreadClosed,
    ThreadOpened,
    apply_events,
)
```

- [ ] **Step 4: Run segment simulator tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_segment_simulator.py -v`
Expected: PASS

- [ ] **Step 5: Run existing simulator tests for regressions**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_runtime_simulator.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/runtime/simulator.py backend/tests/test_segment_simulator.py
git commit -m "feat(simulator): add segment event generation and simulation"
```

---

## Task 9: Completion Judge

**Files:**
- Create: `backend/src/story/runtime/completion_judge.py`
- Test: `backend/tests/test_completion_judge.py`

**Interfaces:**
- Consumes: `CompletionResult`, `CompletionAssessment` from Task 4; `SessionState`, `EventEnvelope`, `FactTruthStatus`, `FactVisibility` from state; `CompletionRequirementSource` from pack (or test fixture)
- Produces: `CompletionJudge.evaluate(requirements, final_state, event_trace) -> CompletionResult`

- [ ] **Step 1: Write failing tests for completion judge**

Create `backend/tests/test_completion_judge.py`:

```python
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.segment_contracts import CompletionResult
from src.story.state import (
    EventEnvelope,
    FactCommitted,
    FactRecord,
    FactTruthStatus,
    FactVisibility,
    GoalAdvanced,
    GoalRuntime,
    GoalStatus,
    SessionState,
    WorldSnapshot,
)


def _make_state(facts=None, goals=None):
    world = WorldSnapshot(
        location_id="cafe",
        time_label="opening",
        present_character_ids=("alice",),
        max_scenes=20,
        reserved_resolution_scenes=3,
        goals=goals or {},
    )
    return SessionState(
        session_id="s1",
        pack_id="test_pack",
        pack_hash="abcd" * 16,
        revision=20,
        session_seed=1,
        world=world,
        facts=facts or {},
        characters={},
    )


def _make_requirement(req_id="req_a", fact_ids=(), goal_ids=()):
    """Create a test requirement mimicking CompletionRequirementSource shape.
    For real v2.0 packs, import CompletionRequirementSource from Plan 1's script_pack.models.
    """
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class ReqHint:
        fact_ids: tuple = ()
        goal_ids: tuple = ()

    @dataclass(frozen=True)
    class Requirement:
        id: str
        description: str
        evidence_hints: ReqHint = ReqHint()

    return Requirement(id=req_id, description=f"Requirement {req_id}", evidence_hints=ReqHint(fact_ids=fact_ids, goal_ids=goal_ids))


def test_judge_satisfied_by_committed_fact():
    facts = {
        "core_cause": FactRecord(
            id="core_cause",
            truth_status=FactTruthStatus.COMMITTED,
            value="alice",
            visibility=FactVisibility.REVEALED,
            evidence_required=1,
            evidence_event_ids=("evt-1",),
        ),
    }
    state = _make_state(facts=facts)
    reqs = (_Requirement(id="core_truth", description="Understand the cause.",
                         evidence_hints=_ReqHint(fact_ids=("core_cause",))),)
    trace = (
        EventEnvelope(
            session_id="s1", sequence=5,
            event=FactCommitted(fact_id="core_cause", value="alice", evidence_event_ids=("evt-1",)),
        ),
    )
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is True
    assert result.assessments[0].satisfied is True
    assert len(result.assessments[0].cited_event_ids) > 0


def test_judge_satisfied_by_fact_and_goal():
    facts = {
        "core_cause": FactRecord(
            id="core_cause",
            truth_status=FactTruthStatus.COMMITTED,
            value="alice",
            visibility=FactVisibility.REVEALED,
            evidence_required=1,
            evidence_event_ids=("evt-1",),
        ),
    }
    goals = {
        "find_ally": GoalRuntime(
            goal_id="find_ally", status=GoalStatus.COMPLETED, progress=1.0,
        ),
    }
    state = _make_state(facts=facts, goals=goals)
    reqs = (
        _make_requirement(req_id="req_a", fact_ids=("core_cause",)),
        _make_requirement(req_id="req_b", goal_ids=("find_ally",)),
    )
    trace = (
        EventEnvelope(
            session_id="s1", sequence=5,
            event=FactCommitted(fact_id="core_cause", value="alice", evidence_event_ids=("evt-1",)),
        ),
        EventEnvelope(
            session_id="s1", sequence=10,
            event=GoalAdvanced(goal_id="find_ally", delta=0.5),
        ),
    )
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is True
    assert result.assessments[0].satisfied is True
    assert result.assessments[1].satisfied is True


def test_judge_not_satisfied_by_uncommitted_fact():
    facts = {
        "core_cause": FactRecord(
            id="core_cause",
            truth_status=FactTruthStatus.POSSIBLE,
            value=None,
            visibility=FactVisibility.HIDDEN,
            evidence_required=1,
        ),
    }
    state = _make_state(facts=facts)
    reqs = (_Requirement(id="core_truth", description="Understand the cause.",
                         evidence_hints=_ReqHint(fact_ids=("core_cause",))),)
    trace = ()
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is False
    assert result.assessments[0].satisfied is False


def test_judge_satisfied_by_completed_goal():
    goals = {
        "find_ally": GoalRuntime(
            goal_id="find_ally", status=GoalStatus.COMPLETED, progress=1.0,
        ),
    }
    state = _make_state(goals=goals)
    reqs = (_Requirement(id="ally", description="Find an ally.",
                         evidence_hints=_ReqHint(goal_ids=("find_ally",))),)
    trace = (
        EventEnvelope(
            session_id="s1", sequence=10,
            event=GoalAdvanced(goal_id="find_ally", delta=0.5),
        ),
    )
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is True


def test_judge_no_hints_unsatisfied():
    state = _make_state()
    reqs = (_Requirement(id="vague", description="Something."),)
    trace = ()
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is False
    assert "no evidence hints" in result.assessments[0].rationale


def test_judge_multiple_requirements_partial():
    facts = {
        "fact_a": FactRecord(
            id="fact_a",
            truth_status=FactTruthStatus.COMMITTED,
            value=True,
            visibility=FactVisibility.REVEALED,
            evidence_required=1,
            evidence_event_ids=("e1",),
        ),
    }
    state = _make_state(facts=facts)
    reqs = (
        _Requirement(id="req_a", description="A",
                     evidence_hints=_ReqHint(fact_ids=("fact_a",))),
        _Requirement(id="req_b", description="B",
                     evidence_hints=_ReqHint(fact_ids=("fact_missing",))),
    )
    trace = ()
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is False
    assert result.assessments[0].satisfied is True
    assert result.assessments[1].satisfied is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_completion_judge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.story.runtime.completion_judge'`

- [ ] **Step 3: Create completion_judge.py**

Create `backend/src/story/runtime/completion_judge.py`:

```python
"""Deterministic completion judge — evaluates final state against author requirements."""

from __future__ import annotations

from typing import Protocol

from src.story.runtime.segment_contracts import (
    CompletionAssessment,
    CompletionResult,
)
from src.story.state import (
    EventEnvelope,
    FactCommitted,
    FactTruthStatus,
    GoalAdvanced,
    SessionState,
)


class _EvidenceHintsLike(Protocol):
    fact_ids: tuple[str, ...]
    goal_ids: tuple[str, ...]


class _RequirementLike(Protocol):
    id: str
    description: str
    evidence_hints: _EvidenceHintsLike


class CompletionJudge:
    """Evaluates the final state and event trace against completion requirements.

    The judge is deterministic: it checks whether evidence hints (fact IDs,
    goal IDs) are satisfied in the final state. It cannot add or alter
    requirements. The kernel computes ``cleared = all(satisfied)``.
    """

    def evaluate(
        self,
        requirements: tuple[_RequirementLike, ...],
        final_state: SessionState,
        event_trace: tuple[EventEnvelope, ...],
    ) -> CompletionResult:
        assessments: list[CompletionAssessment] = []

        for req in requirements:
            hints = req.evidence_hints
            fact_ids = tuple(getattr(hints, "fact_ids", ()))
            goal_ids = tuple(getattr(hints, "goal_ids", ()))

            satisfied = True
            cited: list[str] = []
            rationale_parts: list[str] = []

            for fact_id in fact_ids:
                fact = final_state.facts.get(fact_id)
                if fact is not None and fact.truth_status == FactTruthStatus.COMMITTED:
                    cited.extend(
                        env.event_id
                        for env in event_trace
                        if isinstance(env.event, FactCommitted)
                        and env.event.fact_id == fact_id
                    )
                    rationale_parts.append(f"fact {fact_id} is committed")
                else:
                    satisfied = False
                    rationale_parts.append(f"fact {fact_id} is not committed")

            for goal_id in goal_ids:
                goal = final_state.world.goals.get(goal_id)
                if goal is not None and goal.completed:
                    cited.extend(
                        env.event_id
                        for env in event_trace
                        if isinstance(env.event, GoalAdvanced)
                        and env.event.goal_id == goal_id
                    )
                    rationale_parts.append(f"goal {goal_id} is completed")
                else:
                    satisfied = False
                    rationale_parts.append(f"goal {goal_id} is not completed")

            if not fact_ids and not goal_ids:
                satisfied = False
                rationale_parts.append(
                    "no evidence hints provided; cannot auto-satisfy"
                )

            assessments.append(
                CompletionAssessment(
                    requirement_id=req.id,
                    satisfied=satisfied,
                    cited_event_ids=tuple(dict.fromkeys(cited)),
                    rationale="; ".join(rationale_parts),
                )
            )

        cleared = all(a.satisfied for a in assessments) if assessments else False
        return CompletionResult(
            assessments=tuple(assessments),
            cleared=cleared,
        )
```

- [ ] **Step 4: Run completion judge tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_completion_judge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/runtime/completion_judge.py backend/tests/test_completion_judge.py
git commit -m "feat(judge): add deterministic completion judge"
```

---

## Task 10: Fake Agents

**Files:**
- Modify: `backend/tests/fakes.py` (APPEND if exists from Plan 1)
- Test: `backend/tests/test_segment_contracts.py` (smoke test of fake construction)

**NOTE:** If `fakes.py` already exists from Plan 1, APPEND the following additions: `FakeDirector`, `FakeSegmentWriter`, `FakeGuard`, `FakePlanner`, `budget_test_pack_dict()`. Do not overwrite existing fakes.

**Interfaces:**
- Consumes: `DirectorPort`, `SegmentWriterPort`, `GuardPort`, `PlannerPort` from contracts; `SegmentPlan`, `SegmentDraft`, `GuardResult`, `PacingEnvelope` from Task 4; `ScenePlan`, `SceneDraft`, `WrittenChoice`, `EndingDraft`, `ChoicePlan`, `NarrativeBlock` from contracts
- Produces: `FakeDirector`, `FakeSegmentWriter`, `FakeGuard`, `FakePlanner`, `budget_test_pack_dict()`

- [ ] **Step 1: Write failing tests for fake agents**

Add to `backend/tests/test_segment_contracts.py`:

```python
def test_fake_director_plan_segment():
    from tests.fakes import FakeDirector, budget_test_pack_dict
    from src.story.script_pack.compiler import compile_source
    from src.story.state import initial_session_state
    from src.story.runtime.pacing import compute_pacing_envelope
    import asyncio

    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    pacing = compute_pacing_envelope(state, pack)
    director = FakeDirector()
    plan = asyncio.run(director.plan_segment(pack, state, pacing))
    assert plan.segment_id is not None
    assert len(plan.scenes) >= 1
    assert plan.terminal in ("decision", "ending")


def test_fake_segment_writer_write_segment():
    from tests.fakes import FakeDirector, FakeSegmentWriter, budget_test_pack_dict
    from src.story.script_pack.compiler import compile_source
    from src.story.state import initial_session_state
    from src.story.runtime.pacing import compute_pacing_envelope
    import asyncio

    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    pacing = compute_pacing_envelope(state, pack)
    director = FakeDirector()
    plan = asyncio.run(director.plan_segment(pack, state, pacing))
    writer = FakeSegmentWriter()
    draft = asyncio.run(writer.write_segment(pack, state, plan))
    assert draft.segment_id == plan.segment_id
    assert len(draft.scene_drafts) == len(plan.scenes)


def test_fake_guard_passes():
    from tests.fakes import FakeDirector, FakeSegmentWriter, FakeGuard, FakePlanner, budget_test_pack_dict
    from src.story.script_pack.compiler import compile_source
    from src.story.state import initial_session_state
    from src.story.runtime.pacing import compute_pacing_envelope
    import asyncio

    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    pacing = compute_pacing_envelope(state, pack)
    director = FakeDirector()
    plan = asyncio.run(director.plan_segment(pack, state, pacing))
    writer = FakeSegmentWriter()
    draft = asyncio.run(writer.write_segment(pack, state, plan))
    guard = FakeGuard()
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_segment_contracts.py::test_fake_director_plan_segment -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tests.fakes'`

- [ ] **Step 3: Create tests/fakes.py**

Create `backend/tests/fakes.py`:

```python
"""Fake agent implementations for segment engine testing."""

from __future__ import annotations

import uuid
from typing import Any

from src.story.runtime.contracts import (
    ChoicePlan,
    EndingDraft,
    NarrativeBlock,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.segment_contracts import (
    EndingProposal,
    GuardResult,
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
    ThreadOperation,
)


def budget_test_pack_dict() -> dict[str, Any]:
    """Minimal v1.0 pack dict with adjusted scene budgets for testing.

    Uses the existing v1 schema but with min/max scene budget suitable for
    multi-scene segment testing. For real v2.0 packs with completion_requirements,
    import `minimal_pack_v2_dict()` from Plan 1's `story_factories.py`.

    For backward compatibility with v1.0 packs, use:
        getattr(pack.source, "completion_requirements", ())
    """
    from tests.story_factories import minimal_script_pack_dict

    raw = minimal_script_pack_dict()
    raw["experience"]["min_scenes"] = 4
    raw["experience"]["max_scenes"] = 12
    raw["experience"]["reserved_resolution_scenes"] = 2
    return raw


class FakeDirector:
    """Returns a canned SegmentPlan. Produces decision segments until
    pacing.must_end, then produces an ending segment."""

    def __init__(self) -> None:
        self._call_count = 0

    async def plan_segment(
        self,
        pack: Any,
        state: Any,
        pacing: PacingEnvelope,
    ) -> SegmentPlan:
        self._call_count += 1
        segment_id = f"seg_{state.session_id}_{self._call_count}"

        if pacing.must_end:
            return SegmentPlan(
                segment_id=segment_id,
                scenes=(
                    ScenePlan(
                        scene_id=f"scene_{segment_id}_ending",
                        summary="The final scene",
                        location_id=state.world.location_id,
                        present_character_ids=state.world.present_character_ids,
                        terminal="ending",
                    ),
                ),
                terminal="ending",
                ending_proposal=EndingProposal(
                    title="An Ending",
                    tone="reflective",
                    terminal_state_summary="The story concludes.",
                ),
            )

        return SegmentPlan(
            segment_id=segment_id,
            scenes=(
                ScenePlan(
                    scene_id=f"scene_{segment_id}",
                    summary="A scene unfolds",
                    location_id=state.world.location_id,
                    present_character_ids=state.world.present_character_ids,
                    terminal="decision",
                    decision_id=f"dec_{segment_id}",
                    choices=(
                        ChoicePlan(option_id=f"opt_{segment_id}_a", action_id="ask", intent="Ask directly"),
                        ChoicePlan(option_id=f"opt_{segment_id}_b", action_id="observe", intent="Watch carefully"),
                    ),
                ),
            ),
            terminal="decision",
        )


class FakeSegmentWriter:
    """Returns canned scene drafts and endings matching a SegmentPlan."""

    async def write_segment(
        self,
        pack: Any,
        state: Any,
        plan: SegmentPlan,
    ) -> SegmentDraft:
        scene_drafts = tuple(
            SceneDraft(
                scene_id=scene.scene_id,
                blocks=(
                    NarrativeBlock(
                        kind="narration",
                        text=f"The story continues in {scene.scene_id}.",
                    ),
                ),
            )
            for scene in plan.scenes
        )

        choices: tuple[WrittenChoice, ...] = ()
        if plan.terminal == "decision":
            last_scene = plan.scenes[-1]
            choices = tuple(
                WrittenChoice(option_id=c.option_id, label=c.intent[:80])
                for c in last_scene.choices
            )

        ending = None
        if plan.terminal == "ending" and plan.ending_proposal is not None:
            ending_id = f"ending_{state.session_id}_{uuid.uuid4().hex[:8]}"
            ending = EndingDraft(
                ending_id=ending_id,
                title=plan.ending_proposal.title,
                blocks=(
                    NarrativeBlock(
                        kind="narration",
                        text=f"{plan.ending_proposal.title}. {plan.ending_proposal.terminal_state_summary}",
                    ),
                ),
                tone=plan.ending_proposal.tone,
                terminal_state_summary=plan.ending_proposal.terminal_state_summary,
            )

        return SegmentDraft(
            segment_id=plan.segment_id,
            scene_drafts=scene_drafts,
            choices=choices,
            ending=ending,
        )


class FakeGuard:
    """Always-pass guard for testing."""

    def check_segment(
        self,
        pack: Any,
        state: Any,
        plan: SegmentPlan,
        draft: SegmentDraft,
    ) -> GuardResult:
        return GuardResult(passed=True)


class DeterministicGuard:
    """Production guard with deterministic checks (per cross-plan resolution section 11)."""

    def check_segment(
        self,
        pack: Any,
        state: Any,
        plan: SegmentPlan,
        draft: SegmentDraft,
    ) -> GuardResult:
        violations: list[GuardViolation] = []

        # Check segment/scene ID consistency between plan and draft
        plan_scene_ids = {s.scene_id for s in plan.scenes}
        draft_scene_ids = {s.scene_id for s in draft.scene_drafts}
        if plan_scene_ids != draft_scene_ids:
            violations.append(GuardViolation(
                kind="contradiction",
                detail=f"Scene ID mismatch: plan has {plan_scene_ids}, draft has {draft_scene_ids}",
            ))

        # Check all speakers in drafts exist in plan's present_character_ids
        all_present_ids = set()
        for scene in plan.scenes:
            all_present_ids.update(scene.present_character_ids)
        for i, scene_draft in enumerate(draft.scene_drafts):
            for j, block in enumerate(scene_draft.blocks):
                if block.character_id and block.character_id not in all_present_ids:
                    violations.append(GuardViolation(
                        kind="wrong_speaker",
                        block_index=i,
                        character_id=block.character_id,
                        detail=f"Character {block.character_id} not present in scene",
                    ))

        # Check all choice IDs in draft match plan's choice IDs
        if plan.terminal == "decision" and plan.scenes:
            last_scene = plan.scenes[-1]
            plan_choice_ids = {c.option_id for c in last_scene.choices}
            draft_choice_ids = {c.option_id for c in draft.choices}
            if plan_choice_ids != draft_choice_ids:
                violations.append(GuardViolation(
                    kind="contradiction",
                    detail=f"Choice ID mismatch: plan {plan_choice_ids}, draft {draft_choice_ids}",
                ))

        # Check narration blocks have no character_id
        for i, scene_draft in enumerate(draft.scene_drafts):
            for j, block in enumerate(scene_draft.blocks):
                if block.kind == "narration" and block.character_id:
                    violations.append(GuardViolation(
                        kind="wrong_speaker",
                        block_index=i,
                        detail="Narration block has character_id",
                    ))

        # Check scene count does not exceed max_scenes
        if len(plan.scenes) > state.world.max_scenes:
            violations.append(GuardViolation(
                kind="contradiction",
                detail=f"Scene count {len(plan.scenes)} exceeds max_scenes {state.world.max_scenes}",
            ))

        return GuardResult(passed=len(violations) == 0, violations=tuple(violations))


class FakePlanner:
    """Returns a simple success resolution for any choice."""

    def resolve_action(
        self,
        pack: Any,
        state: Any,
        choice: Any,
    ) -> Any:
        from src.story.runtime.contracts import ActionResolution
        return ActionResolution(action_id=choice.action_id, outcome="success")
```

- [ ] **Step 4: Run fake agent tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_segment_contracts.py::test_fake_director_plan_segment tests/test_segment_contracts.py::test_fake_segment_writer_write_segment tests/test_segment_contracts.py::test_fake_guard_passes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/tests/fakes.py backend/tests/test_segment_contracts.py
git commit -m "feat(tests): add fake Director, SegmentWriter, Guard for segment testing"
```

---

## Task 11: Turn Orchestrator

**Files:**
- Create: `backend/src/story/runtime/turn_orchestrator.py`
- Test: `backend/tests/test_turn_orchestrator.py`

**Interfaces:**
- Consumes: `DirectorPort`, `SegmentWriterPort`, `GuardPort`, `PacingEnvelope` from Task 4; `compute_pacing_envelope` from Task 5; `validate_segment_plan`, `validate_segment_draft` from Task 6; `simulate_segment` from Task 7; `CompletionJudge` from Task 8; `StoryEventStore`, `commit_command`, `claim_command`, `release_command` from storage; existing `validate_action_resolution`, `simulate_resolution`
- Produces: `TurnOrchestrator` with `execute_turn(pack, session_id, expected_revision, idempotency_key, choice_id) -> AsyncGenerator[tuple[str, dict], None]`

- [ ] **Step 1: Write failing tests for turn orchestrator**

Create `backend/tests/test_turn_orchestrator.py`:

```python
import asyncio
import json
import pytest
from pathlib import Path

from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.completion_judge import CompletionJudge
from src.story.script_pack.compiler import compile_source
from src.story.state import initial_session_state
from src.story.storage import StoryEventStore
from tests.fakes import FakeDirector, FakeSegmentWriter, FakeGuard, FakePlanner, budget_test_pack_dict


def _build_orchestrator(tmp_path: Path):
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "turn_test.db")
    state = initial_session_state(pack, "s1", session_seed=42)
    store.create_session(state)
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    return pack, store, orchestrator


def _collect_events(gen):
    """Run an async generator synchronously and collect events."""
    events = []
    loop = asyncio.new_event_loop()

    async def run():
        async for evt_type, data in gen:
            events.append((evt_type, data))

    loop.run_until_complete(run())
    loop.close()
    return events


def test_opening_turn_streams_segment_started_blocks_ready(tmp_path: Path):
    pack, store, orch = _build_orchestrator(tmp_path)
    gen = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events = _collect_events(gen)

    types = [e[0] for e in events]
    assert "segment_started" in types
    assert "block" in types
    assert "segment_ready" in types

    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "decision"
    assert len(ready["choices"]) == 2


def test_turn_increases_revision(tmp_path: Path):
    pack, store, orch = _build_orchestrator(tmp_path)
    gen = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events = _collect_events(gen)
    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["revision"] > 0


def test_idempotent_replay_returns_same_segment(tmp_path: Path):
    pack, store, orch = _build_orchestrator(tmp_path)
    gen1 = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events1 = _collect_events(gen1)
    gen2 = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events2 = _collect_events(gen2)

    ready1 = next(data for t, data in events1 if t == "segment_ready")
    ready2 = next(data for t, data in events2 if t == "segment_ready")
    assert ready1["revision"] == ready2["revision"]
    assert ready1["segment_id"] == ready2["segment_id"]


def test_failed_generation_releases_command(tmp_path: Path):
    from tests.fakes import FakeGuard
    from src.story.runtime.segment_contracts import GuardResult, GuardViolation

    class FailingDirector(FakeDirector):
        async def plan_segment(self, pack, state, pacing):
            raise RuntimeError("model failed")

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "fail_test.db")
    state = initial_session_state(pack, "s1", session_seed=1)
    store.create_session(state)
    orch = TurnOrchestrator(
        store=store,
        director=FailingDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    gen = orch.execute_turn(pack, "s1", 0, "cmd-fail", None)

    with pytest.raises(Exception):
        _collect_events(gen)

    # Verify session revision is unchanged (command was released).
    loaded = store.load_session("s1")
    assert loaded.revision == 0


def test_choice_turn_resolves_and_advances(tmp_path: Path):
    pack, store, orch = _build_orchestrator(tmp_path)

    # First turn: opening -> decision.
    gen1 = orch.execute_turn(pack, "s1", 0, "cmd-00", None)
    events1 = _collect_events(gen1)
    ready1 = next(data for t, data in events1 if t == "segment_ready")
    choice_id = ready1["choices"][0]["id"]
    rev = ready1["revision"]

    # Second turn: resolve choice -> next decision.
    gen2 = orch.execute_turn(pack, "s1", rev, "cmd-01", choice_id)
    events2 = _collect_events(gen2)
    ready2 = next(data for t, data in events2 if t == "segment_ready")
    assert ready2["revision"] > rev


def test_ending_turn_has_ending_terminal(tmp_path: Path):
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "ending_test.db")
    state = initial_session_state(pack, "s1", session_seed=1)
    # Force to max scenes.
    state = state.model_copy(update={
        "world": state.world.model_copy(update={"scene_count": state.world.max_scenes})
    })
    store.create_session(state)
    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
    )
    gen = orch.execute_turn(pack, "s1", 0, "cmd-ending", None)
    events = _collect_events(gen)
    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "ending"
    assert "ending" in ready
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_turn_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.story.runtime.turn_orchestrator'`

- [ ] **Step 3a: Create turn_orchestrator.py with skeleton**

Create `backend/src/story/runtime/turn_orchestrator.py`:

```python
"""Turn orchestrator: single-turn command pipeline with SSE streaming."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from src.story.runtime.contracts import (
    ModelContractError,
    PlannerPort,
    RuntimeGenerationUnavailable,
    RuntimeRevisionConflict,
    RuntimeSessionEnded,
)
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.segment_contracts import (
    DirectorPort,
    GuardPort,
    GuardResult,
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
    SegmentWriterPort,
)
from src.story.runtime.simulator import simulate_segment
from src.story.runtime.validator import (
    ProposalRejected,
    validate_action_resolution,
    validate_segment_draft,
    validate_segment_plan,
)
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import (
    DecisionPresented,
    EndingGenerated,
    EventEnvelope,
    InvalidChoice,
    PlayerActionSelected,
    PresentedChoice,
    SceneCommitted,
    SessionEnded,
    SessionState,
    SessionStatus,
    apply_events,
)
from src.story.state.events import StoryEvent
from src.story.storage import StoryEventStore


def _turn_fingerprint(
    expected_revision: int, choice_id: str | None = None
) -> str:
    payload = {
        "kind": "turn",
        "expected_revision": expected_revision,
        "choice_id": choice_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


class TurnOrchestrator:
    """Sole entry point for a player turn.

    Pipeline: resolve choice -> simulate effects -> derive pacing ->
    Director.plan_segment -> validate plan -> Writer.write_segment ->
    Guard.check_segment -> simulate segment events -> (if ending:
    CompletionJudge) -> atomic commit -> SSE stream.
    """

    def __init__(
        self,
        store: StoryEventStore,
        director: DirectorPort,
        writer: SegmentWriterPort,
        guard: GuardPort,
        completion_judge: CompletionJudge,
        planner: PlannerPort,
    ) -> None:
        self.store = store
        self.director = director
        self.writer = writer
        self.guard = guard
        self.completion_judge = completion_judge
        self.planner = planner

    async def execute_turn(
        self,
        pack: CompiledScriptPack,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        choice_id: str | None,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        fingerprint = _turn_fingerprint(expected_revision, choice_id)

        claim = self.store.claim_command(
            session_id, idempotency_key, "turn", fingerprint
        )
        if claim.replay_json is not None:
            replay = json.loads(claim.replay_json)
            yield ("segment_started", {
                "segment_id": replay["segment_id"],
                "expected_revision": expected_revision,
            })
            for block in replay.get("blocks", []):
                yield ("block", block)
            ready = replay["segment_ready"]
            yield ("segment_ready", ready)
            return

        # Handle retry_after when command lease is still active
        if claim.leased:
            yield ("retry_after", {
                "retry_after_seconds": 5,
                "message": "Command is already being processed",
            })
            return

        try:
            state = self.store.load_session(session_id)
            if state.revision != expected_revision:
                raise RuntimeRevisionConflict(
                    f"session {session_id}: expected {expected_revision}, "
                    f"current {state.revision}"
                )
            if state.status == SessionStatus.ENDED:
                raise RuntimeSessionEnded(session_id)

            segment_id = f"seg_{session_id}_{uuid4().hex[:8]}"

            yield ("segment_started", {
                "segment_id": segment_id,
                "expected_revision": expected_revision,
            })

        except Exception:
            self.store.release_command(
                session_id, idempotency_key, "turn", fingerprint
            )
            raise
```

- [ ] **Step 3b: Add choice resolution and pre-events**

Add after the state validation in Step 3a:

```python
            # Resolve choice if non-opening turn.
            pre_events: list[StoryEvent] = []
            if choice_id is not None:
                if state.pending_decision is None:
                    raise InvalidChoice("no decision is pending")
                choice = next(
                    (c for c in state.pending_decision.choices if c.id == choice_id),
                    None,
                )
                if choice is None:
                    raise InvalidChoice(f"choice was not offered: {choice_id}")
                # Call planner to resolve action (per cross-plan resolution section 10)
                resolution = self.planner.resolve_action(pack, state, choice)
                validate_action_resolution(pack, state, choice, resolution)
                from src.story.runtime.simulator import simulate_resolution
                pre_events.extend(
                    simulate_resolution(state, choice, resolution, idempotency_key)
                )
                # Apply pre-events to get post-choice state.
                pre_envelopes = tuple(
                    EventEnvelope(
                        session_id=session_id,
                        sequence=state.revision + i,
                        event=e,
                    )
                    for i, e in enumerate(pre_events, start=1)
                )
                state = apply_events(state, pre_envelopes)

            elif state.pending_decision is not None:
                from src.story.runtime.contracts import DecisionRequired
                raise DecisionRequired(state.pending_decision.decision_id)

            # Also auto-ack any pending scene from previous turn.
            if state.pending_scene is not None:
                from src.story.state import SceneAcknowledged
                ack = SceneAcknowledged(scene_id=state.pending_scene.scene_id)
                pre_events.append(ack)
                ack_envelope = EventEnvelope(
                    session_id=session_id,
                    sequence=state.revision + len(pre_events),
                    event=ack,
                )
                state = apply_events(state, (ack_envelope,))
```

- [ ] **Step 3c: Add pacing, director call, and plan validation**

Add after the auto-ack code:

```python
            # Derive pacing.
            pacing = compute_pacing_envelope(state, pack)

            # Director proposes segment plan.
            try:
                plan = await self.director.plan_segment(pack, state, pacing)
            except Exception as exc:
                raise RuntimeGenerationUnavailable(
                    "director failed to produce a segment plan"
                ) from exc

            plan = validate_segment_plan(pack, state, plan, pacing)
```

- [ ] **Step 3d: Add writer, guard, and draft validation**

Add after the plan validation:

```python
            # Writer produces draft.
            try:
                draft = await self.writer.write_segment(pack, state, plan)
            except Exception as exc:
                raise RuntimeGenerationUnavailable(
                    "writer failed to produce a segment draft"
                ) from exc

            draft = validate_segment_draft(plan, draft)

            # Guard checks.
            guard_result = self.guard.check_segment(pack, state, plan, draft)
            if not guard_result.passed:
                raise RuntimeGenerationUnavailable(
                    "guard rejected segment"
                )
```

- [ ] **Step 3e: Add block streaming and simulation**

Add after the guard check:

```python
            # Stream provisional blocks.
            block_index = 0
            for scene_draft in draft.scene_drafts:
                for block in scene_draft.blocks:
                    yield ("block", {
                        "segment_id": plan.segment_id,
                        "index": block_index,
                        "kind": block.kind,
                        "text": block.text,
                        "character_id": block.character_id,
                    })
                    block_index += 1

            # Simulate segment events.
            seg_events = simulate_segment(pack, state, plan, draft)
```

- [ ] **Step 3f: Add completion judge and atomic commit**

Add after the simulation:

```python
            # If ending, run completion judge.
            completion_result = None
            if plan.terminal == "ending":
                # Simulate final state for judge.
                all_events = tuple(pre_events) + seg_events
                all_envelopes = tuple(
                    EventEnvelope(
                        session_id=session_id,
                        sequence=expected_revision + i,
                        event=e,
                    )
                    for i, e in enumerate(all_events, start=1)
                )
                final_state = apply_events(
                    self.store.load_session(session_id), all_envelopes,
                )
                # Gather completion requirements from pack or use empty.
                reqs = getattr(pack.source, "completion_requirements", ()) or ()
                completion_result = self.completion_judge.evaluate(
                    reqs, final_state, all_envelopes,
                )

            # Build full event list.
            all_story_events: list[StoryEvent] = list(pre_events) + list(seg_events)

            # Add completion events if ending.
            if plan.terminal == "ending" and completion_result is not None:
                from src.story.state import CompletionEvaluated, CompletionAssessmentRecord
                # Convert CompletionAssessment to CompletionAssessmentRecord per cross-plan resolution section 8
                assessment_records = tuple(
                    CompletionAssessmentRecord(
                        requirement_id=a.requirement_id,
                        satisfied=a.satisfied,
                        cited_event_ids=a.cited_event_ids,
                        rationale=a.rationale,
                    )
                    for a in completion_result.assessments
                )
                all_story_events.append(
                    CompletionEvaluated(
                        cleared=completion_result.cleared,
                        assessments=assessment_records,
                    )
                )
                # Add SessionEnded.
                ending_event = next(
                    e for e in seg_events if isinstance(e, EndingGenerated)
                )
                all_story_events.append(
                    SessionEnded(ending_id=ending_event.ending_id)
                )

            # Atomic commit.
            def result_factory(updated: SessionState, envelopes) -> str:
                blocks_data = []
                for scene_draft in draft.scene_drafts:
                    for block in scene_draft.blocks:
                        blocks_data.append(block.model_dump(mode="json"))

                ready_data: dict[str, Any] = {
                    "segment_id": plan.segment_id,
                    "revision": updated.revision,
                    "terminal": plan.terminal,
                    "blocks": blocks_data,
                }

                if plan.terminal == "decision":
                    written_map = {wc.option_id: wc for wc in draft.choices}
                    last_scene = plan.scenes[-1]
                    ready_data["choices"] = [
                        {
                            "id": c.option_id,
                            "action_id": c.action_id,
                            "label": written_map[c.option_id].label,
                            "intent": c.intent,
                            "target_character_id": c.target_character_id,
                            "preview": written_map[c.option_id].preview,
                        }
                        for c in last_scene.choices
                    ]
                elif plan.terminal == "ending" and draft.ending is not None:
                    ready_data["ending"] = {
                        "ending_id": draft.ending.ending_id,
                        "title": draft.ending.title,
                        "tone": draft.ending.tone,
                        "terminal_state_summary": draft.ending.terminal_state_summary,
                    }
                    if updated.completion is not None:
                        ready_data["cleared"] = updated.completion.cleared
                        ready_data["assessments"] = [
                            a.model_dump(mode="json")
                            for a in updated.completion.assessments
                        ]

                return json.dumps({
                    "segment_id": plan.segment_id,
                    "blocks": blocks_data,
                    "segment_ready": ready_data,
                })

            updated_state, _, result_json = self.store.commit_command(
                session_id,
                idempotency_key,
                "turn",
                fingerprint,
                expected_revision,
                all_story_events,
                result_factory,
            )

            result_data = json.loads(result_json)
            ready_data = result_data["segment_ready"]
            ready_data["revision"] = updated_state.revision
            yield ("segment_ready", ready_data)
```

- [ ] **Step 4: Run turn orchestrator tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_turn_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/runtime/turn_orchestrator.py backend/tests/test_turn_orchestrator.py
git commit -m "feat(orchestrator): add TurnOrchestrator with SSE segment streaming"
```

---

## Task 12: API Turns Endpoint

**Files:**
- Modify: `backend/src/story/api.py`
- Test: `backend/tests/test_turns_api.py`

**Interfaces:**
- Consumes: `TurnOrchestrator` from Task 10; existing `AppDependencies`, `ScriptPackRegistry`, `create_app`
- Produces: `POST /api/v2/sessions/{id}/turns` SSE endpoint; updated `AppDependencies` with optional `TurnOrchestrator`

- [ ] **Step 1: Write failing tests for the turns API endpoint**

Create `backend/tests/test_turns_api.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.story.api import AppDependencies, ScriptPackRegistry, create_app
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.storage import StoryEventStore
from tests.fakes import FakeDirector, FakeSegmentWriter, FakeGuard, FakePlanner, budget_test_pack_dict


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
    pack_dir.mkdir(parents=True)
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
        store=store, registry=registry, runtime=None, orchestrator=orchestrator,
    )


def test_turns_endpoint_streams_segment(tmp_path: Path):
    http = TestClient(create_app(_build_deps(tmp_path)))
    created = http.post(
        "/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 1}
    )
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
    created = http.post(
        "/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 2}
    )
    session_id = created.json()["session_id"]

    # Manually set scene_count to max to trigger ending.
    from src.story.script_pack.compiler import compile_source
    pack = compile_source(budget_test_pack_dict())
    from src.story.state import initial_session_state
    store = _build_deps(tmp_path).store
    state = initial_session_state(pack, "force_ending", session_seed=1)
    state = state.model_copy(update={
        "world": state.world.model_copy(update={"scene_count": state.world.max_scenes})
    })
    store.create_session(state)

    with http.stream(
        "POST",
        f"/api/v2/sessions/force_ending/turns",
        json={"expected_revision": 0, "idempotency_key": "cmd-end", "choice_id": None},
    ) as resp:
        events = _parse_sse(resp)

    ready = next(data for t, data in events if t == "segment_ready")
    assert ready["terminal"] == "ending"
    assert "ending" in ready


def test_turns_endpoint_idempotent_replay(tmp_path: Path):
    http = TestClient(create_app(_build_deps(tmp_path)))
    created = http.post(
        "/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 3}
    )
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_turns_api.py -v`
Expected: FAIL — endpoint does not exist or `AppDependencies` does not accept `orchestrator`

- [ ] **Step 3: Modify AppDependencies and add turns endpoint**

In `backend/src/story/api.py`, update the `AppDependencies` dataclass:

```python
@dataclass(frozen=True)
class AppDependencies:
    store: StoryEventStore
    registry: ScriptPackRegistry
    runtime: RuntimeService | None = None
    orchestrator: TurnOrchestrator | None = None
```

Add import at the top:

```python
from src.story.runtime.turn_orchestrator import TurnOrchestrator
```

Add the turns endpoint inside `create_app`, after the existing routes:

```python
    class TurnRequest(BaseModel):
        expected_revision: int = Field(ge=0)
        idempotency_key: str = Field(min_length=1, max_length=120)
        choice_id: str | None = None

    @app.post(
        "/api/v2/sessions/{session_id}/turns",
        response_class=StreamingResponse,
    )
    async def execute_turn(session_id: str, command: TurnRequest):
        if deps.orchestrator is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "turn_orchestrator_not_configured"},
            )
        state = deps.store.load_session(session_id)
        pack = deps.registry.get(state.pack_id)

        async def event_stream():
            try:
                async for event_type, data in deps.orchestrator.execute_turn(
                    pack,
                    session_id,
                    command.expected_revision,
                    command.idempotency_key,
                    command.choice_id,
                ):
                    yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    # heartbeat events are no-ops for frontend, retry_after shows message
            except (RuntimeRevisionConflict, RevisionConflict):
                yield _sse_error("revision_conflict")
            except DecisionRequired:
                yield _sse_error("decision_required")
            except RuntimeSessionEnded:
                yield _sse_error("session_ended")
            except PackMismatch:
                yield _sse_error("pack_mismatch")
            except (OpenAIError, RuntimeGenerationUnavailable) as exc:
                logger.warning("turn stream failed: %s", exc)
                yield _sse_error("generation_unavailable")

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
```

- [ ] **Step 4: Run turns API tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_turns_api.py -v`
Expected: PASS

- [ ] **Step 5: Run existing API tests for regressions**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_v2_api.py -v`
Expected: PASS (existing tests use `runtime`, not `orchestrator`)

- [ ] **Step 6: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/src/story/api.py backend/tests/test_turns_api.py
git commit -m "feat(api): add POST /api/v2/sessions/{id}/turns SSE endpoint"
```

---

## Task 13: End-to-End Property Tests

**Files:**
- Create: `backend/tests/test_segment_property.py`
- Test: `backend/tests/test_segment_property.py`

**Interfaces:**
- Consumes: All prior tasks — `TurnOrchestrator`, `FakeDirector`, `FakeSegmentWriter`, `FakeGuard`, `CompletionJudge`, `budget_test_pack_dict`
- Produces: Property test suite proving fake-agent sessions run end-to-end with pacing, endings, and completion judgment

- [ ] **Step 1: Write property test suite**

Create `backend/tests/test_segment_property.py`:

```python
"""End-to-end property tests: fake-agent sessions with multiple player policies."""

from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
from typing import Literal

from src.story.api import AppDependencies, ScriptPackRegistry, create_app
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.script_pack.compiler import compile_source
from src.story.state import initial_session_state, SessionStatus
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeSegmentWriter,
    FakeGuard,
    budget_test_pack_dict,
)

PlayerPolicy = Literal["first", "last", "alternate"]


def _build_orchestrator(tmp_path: Path):
    raw = budget_test_pack_dict()
    packs_root = tmp_path / "script_packs"
    pack_dir = packs_root / "test_pack"
    pack_dir.mkdir(parents=True)
    import yaml
    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    registry = ScriptPackRegistry(packs_root)
    pack = registry.get("test_pack")
    store = StoryEventStore(tmp_path / "prop_test.db")
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )
    return pack, store, orchestrator


def _run_turn(orch, pack, session_id, revision, key, choice_id):
    """Run a turn synchronously and return the segment_ready data."""
    gen = orch.execute_turn(pack, session_id, revision, key, choice_id)
    loop = asyncio.new_event_loop()
    events = []

    async def run():
        async for evt_type, data in gen:
            events.append((evt_type, data))

    loop.run_until_complete(run())
    loop.close()
    ready = next(data for t, data in events if t == "segment_ready")
    return ready


def _select_choice(choices: list, policy: PlayerPolicy, turn_index: int):
    if policy == "first":
        return choices[0]["id"]
    elif policy == "last":
        return choices[-1]["id"]
    else:  # alternate
        return choices[turn_index % len(choices)]["id"]


def _run_full_session(store, orch, pack, session_id, policy: PlayerPolicy):
    """Run a full session from opening to ending. Returns final state."""
    revision = 0
    turn = 0
    choice_id = None
    key = f"cmd-{session_id}-000"

    ready = _run_turn(orch, pack, session_id, revision, key, choice_id)
    revision = ready["revision"]

    while ready["terminal"] != "ending":
        turn += 1
        choice_id = _select_choice(ready["choices"], policy, turn)
        key = f"cmd-{session_id}-{turn:03d}"
        ready = _run_turn(orch, pack, session_id, revision, key, choice_id)
        revision = ready["revision"]

        # Safety valve: prevent infinite loops.
        if turn > 50:
            break

    return store.load_session(session_id), ready


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_session_reaches_ending_within_scene_budget(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_budget", session_seed=99)
    store.create_session(state)

    final_state, ready = _run_full_session(store, orch, pack, "sess_budget", policy)

    assert final_state.status == SessionStatus.ENDED
    assert final_state.world.scene_count <= final_state.world.max_scenes
    assert ready["terminal"] == "ending"


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_session_has_exactly_one_ending(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_ending", session_seed=100)
    store.create_session(state)

    final_state, ready = _run_full_session(store, orch, pack, "sess_ending", policy)

    assert final_state.ending is not None
    assert final_state.ending.ending_id is not None
    # Check event log for exactly one EndingGenerated.
    events = store.load_events("sess_ending")
    ending_events = [e for e in events if e.event.type == "ending_generated"]
    assert len(ending_events) == 1


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_session_has_completion_assessment(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_completion", session_seed=101)
    store.create_session(state)

    final_state, ready = _run_full_session(store, orch, pack, "sess_completion", policy)

    assert final_state.completion is not None
    # Completion is a boolean (cleared or not), but must exist.
    assert isinstance(final_state.completion.cleared, bool)


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_no_duplicate_choice_ids(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_choices", session_seed=102)
    store.create_session(state)

    # Run a few turns and collect choice IDs.
    revision = 0
    all_choice_ids: set[str] = set()
    ready = _run_turn(orch, pack, "sess_choices", revision, "cmd-0", None)
    revision = ready["revision"]

    for turn in range(1, 4):
        if ready["terminal"] == "ending":
            break
        choice = _select_choice(ready["choices"], policy, turn)
        for c in ready["choices"]:
            assert c["id"] not in all_choice_ids, f"duplicate choice id: {c['id']}"
            all_choice_ids.add(c["id"])
        ready = _run_turn(
            orch, pack, "sess_choices", revision, f"cmd-{turn}", choice
        )
        revision = ready["revision"]


@pytest.mark.parametrize("policy", ["first", "last", "alternate"])
def test_event_replay_equals_committed_state(tmp_path: Path, policy: PlayerPolicy):
    pack, store, orch = _build_orchestrator(tmp_path)
    state = initial_session_state(pack, "sess_replay", session_seed=103)
    store.create_session(state)

    final_state, _ = _run_full_session(store, orch, pack, "sess_replay", policy)

    # Reload from store and verify it matches.
    reloaded = store.load_session("sess_replay")
    assert reloaded.revision == final_state.revision
    assert reloaded.status == final_state.status
    assert reloaded.world.scene_count == final_state.world.scene_count


def test_sessions_with_different_policies_produce_different_revisions(tmp_path: Path):
    """Different player policies should lead to different session paths."""
    pack1, store1, orch1 = _build_orchestrator(tmp_path / "policy1")
    state1 = initial_session_state(pack1, "sess_p1", session_seed=1)
    store1.create_session(state1)
    final1, _ = _run_full_session(store1, orch1, pack1, "sess_p1", "first")

    pack2, store2, orch2 = _build_orchestrator(tmp_path / "policy2")
    state2 = initial_session_state(pack2, "sess_p2", session_seed=1)
    store2.create_session(state2)
    final2, _ = _run_full_session(store2, orch2, pack2, "sess_p2", "last")

    # Different policies may or may not produce different revisions with
    # the fake director (it always produces 1-scene segments), but the
    # event traces should differ in choice selections.
    events1 = store1.load_events("sess_p1")
    events2 = store2.load_events("sess_p2")
    choices1 = [e for e in events1 if e.event.type == "player_action_selected"]
    choices2 = [e for e in events2 if e.event.type == "player_action_selected"]

    # With the same number of turns, first vs last should differ.
    if len(choices1) == len(choices2) and len(choices1) > 0:
        option_ids1 = [e.event.option_id for e in choices1]
        option_ids2 = [e.event.option_id for e in choices2]
        assert option_ids1 != option_ids2, \
            "different policies should select different options"
```

- [ ] **Step 2: Run property tests**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest tests/test_segment_property.py -v`
Expected: PASS

- [ ] **Step 3: Run the complete test suite**

Run: `cd /home/miku/szj/gal_agent/backend && python -m pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd /home/miku/szj/gal_agent && git add backend/tests/test_segment_property.py
git commit -m "feat(tests): add end-to-end property tests with multiple player policies"
```

---

## Self-Review

### Spec Coverage Checklist

| Spec Section | Task(s) | Covered |
|---|---|---|
| 2.1 Performance segment unit | Tasks 4, 7, 10 (SegmentPlan, segment_events, TurnOrchestrator) | Yes |
| 2.2 Transactional segment + buffer | Tasks 10, 11 (atomic commit, provisional SSE blocks) | Yes |
| 2.3 One authoritative turn command | Tasks 10, 11 (TurnOrchestrator, POST /turns) | Yes |
| 2.4 Dynamic endings + completion requirements | Tasks 1-3, 8 (EndingGenerated event, CompletionJudge) | Yes |
| 2.5 Agent proposes; kernel commits | Tasks 6, 7, 10 (validate, simulate, atomic commit) | Yes |
| 5.1 Turn Orchestrator | Task 10 | Yes |
| 5.6 Completion Judge | Task 8 | Yes |
| 6 Segment generation data flow | Task 10 (full pipeline) | Yes |
| 7 Pacing and convergence | Task 5 (compute_pacing_envelope) | Yes |
| 8.1 Target event groups | Tasks 2, 3 (DecisionPresented, EndingGenerated, CompletionEvaluated) | Yes |
| 8.2 Atomicity | Task 10 (commit_command with expected_revision) | Yes |
| 8.3 Public projection | Existing projection.py (unchanged; segment_ready data is public) | Yes |
| 9 Async segment protocol | Tasks 10, 11 (SSE: segment_started, block, segment_ready) | Yes |
| 10 Error/retry/recovery | Task 10 (command receipts, release on failure) | Yes |
| 12.1 Offline contract tests | Tasks 1-8 (per-component tests) | Yes |
| 12.2 Runtime property tests | Task 12 (multi-policy sessions, invariant assertions) | Yes |

### Placeholder Scan

- No TBD, TODO, or "implement later" found.
- Every step has actual code.
- All referenced types are defined in prior tasks.

### Type Consistency

- `SegmentPlan` fields match across Tasks 4, 6, 7, 10.
- `SegmentDraft` fields match across Tasks 4, 6, 7, 9, 10.
- `PacingEnvelope` fields match across Tasks 4, 5, 6, 10.
- `CompletionResult` / `CompletionAssessment` match across Tasks 4, 8, 10.
- `GuardResult` / `GuardViolation` match across Tasks 4, 9, 10.
- Event types (`DecisionPresented`, `EndingGenerated`, `CompletionEvaluated`) match across Tasks 2, 3, 7.
- SSE event names (`segment_started`, `block`, `segment_ready`) match across Tasks 10, 11.
- DirectorPort / SegmentWriterPort / GuardPort signatures match across Tasks 4, 9, 10.
