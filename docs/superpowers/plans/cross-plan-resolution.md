# Cross-Plan Architecture Resolution

> **Authority:** This document overrides conflicting content in individual plans.
> All 4 plans must conform to these decisions. Last updated: 2026-08-12.

## 1. Execution Order

```
Plan 1 (Pack 2.0) → Plan 2 (Segment Engine) → Plan 3 (Agent Pipeline) → Plan 4 (Frontend)
```

Plan 2 defines segment contracts; Plan 3 imports and implements them. Plan 4
consumes Plan 2's API. No plan redefines types owned by an earlier plan.

## 2. Authoritative Type Locations

| Category | File | Owner |
|----------|------|-------|
| Pack schema (v1/v2) | `script_pack/models.py` | Plan 1 |
| Segment contracts | `runtime/segment_contracts.py` | Plan 2 |
| Event types | `events.py` | Plan 2 |
| State models | `state/models.py` | Plan 2 |
| Agent implementations | `runtime/director.py`, `runtime/segment_writer.py`, `runtime/guard.py` | Plan 3 |
| Context builders | `runtime/segment_context.py` | Plan 3 |
| Frontend types | `frontend/src/stream.ts`, `frontend/src/api.ts` | Plan 4 |

**Plan 3 MUST import from `segment_contracts.py`. It MUST NOT redefine
SegmentPlan, SegmentDraft, EndingProposal, ThreadOperation, PacingEnvelope,
DirectorPort, SegmentWriterPort, GuardPort, GuardResult, GuardViolation,
CompletionAssessment, or CompletionResult in `contracts.py`.**

## 3. Unified Type Definitions

### ThreadOperation (in segment_contracts.py)

```python
class ThreadOperation(RuntimeModel):
    kind: Literal["open", "advance", "close"]
    thread_id: str | None = None       # None only for "open"
    thread_type: str = ""              # for "open" operations
    close_status: Literal["resolved", "abandoned"] | None = None  # for "close"
    urgency: float | None = None       # for "advance"
    involved_character_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
```

### EndingDraft (in contracts.py, extended by Plan 2)

The existing `EndingDraft` in `contracts.py` is extended — NOT replaced:

```python
class EndingDraft(RuntimeModel):
    ending_id: str
    title: str                          # Field(min_length=1, max_length=120)
    blocks: tuple[NarrativeBlock, ...]  # min_length=1
    tone: str = ""                      # Field(default="", max_length=80)
    terminal_state_summary: str = ""    # Field(default="", max_length=600)
```

### SegmentDraft (in segment_contracts.py)

```python
class SegmentDraft(RuntimeModel):
    segment_id: str
    scene_drafts: tuple[SceneDraft, ...]
    choices: tuple[WrittenChoice, ...] = ()   # properly typed, NOT bare tuple
    ending: EndingDraft | None = None
```

### EndingProposal (in segment_contracts.py)

```python
class EndingProposal(RuntimeModel):
    title: str                          # Field(min_length=1, max_length=120)
    tone: str                           # Field(min_length=1, max_length=80)
    terminal_state_summary: str         # Field(min_length=1, max_length=600)
```

### ScenePlan terminal extension

The existing `ScenePlan` in `contracts.py` gets `terminal` extended to include
`"ending"`. Do NOT rename to LegacyScenePlan:

```python
terminal: Literal["continue", "decision", "ending"]
```

### SegmentPlan (in segment_contracts.py)

Single definition with model_validator. Plan 3 imports this:

```python
class SegmentPlan(RuntimeModel):
    segment_id: str
    scenes: tuple[ScenePlan, ...]       # min_length=1
    terminal: Literal["decision", "ending"]
    ending_proposal: EndingProposal | None = None   # required iff terminal="ending"
    thread_ops: tuple[ThreadOperation, ...] = ()
    new_facts: tuple[FactCommitPlan, ...] = ()
    phase_after: StoryPhase | None = None

    @model_validator(mode="after")
    def _validate_terminal_consistency(self):
        last = self.scenes[-1]
        if self.terminal == "ending":
            assert last.terminal == "ending", "last scene must have terminal='ending'"
            assert self.ending_proposal is not None, "ending_proposal required"
        elif self.terminal == "decision":
            assert last.terminal == "decision", "last scene must have terminal='decision'"
        else:
            for s in self.scenes:
                assert s.terminal == "continue", "non-terminal scenes must be continue"
        return self
```

## 4. File Conflict Rules

### fakes.py

- **Plan 1** creates `backend/tests/fakes.py` with: FakePlanner, FakeWriter,
  FakeStreamingGenerator, utility helpers.
- **Plan 2** APPENDS to the same file: FakeDirector, FakeSegmentWriter,
  FakeGuard, segment test helpers. Does NOT overwrite.
- **Plan 3** does NOT create or modify fakes.py.
- Each plan must check: "If fakes.py already exists, append your additions."

### test_segment_contracts.py

- **Plan 2** creates `backend/tests/test_segment_contracts.py`.
- **Plan 3** APPENDS agent-specific schema tests. Does NOT overwrite.

### AppDependencies (in api.py)

All new fields added cumulatively:
```python
@dataclass
class AppDependencies:
    store: StoryEventStore
    registry: ScriptPackRegistry
    runtime: RuntimeService
    generator: StreamingGeneratorPort | None = None     # existing
    orchestrator: TurnOrchestrator | None = None         # Plan 2 adds
    director: DirectorPort | None = None                 # Plan 3 adds
    segment_writer: SegmentWriterPort | None = None      # Plan 3 adds
    guard: GuardPort | None = None                       # Plan 3 adds
```

### v2_minimal_script_pack_dict naming

Plan 2's function that creates a v1.0 pack with adjusted budgets must be
renamed to `budget_test_pack_dict()`. If a real v2.0 pack is needed,
import `minimal_pack_v2_dict()` from Plan 1's `story_factories.py`.

## 5. v1/v2 Pack Access Helpers

Plan 3's `segment_context.py` must handle both pack versions:

```python
def _get_world_setting(source):
    """Return world setting from v1.0 or v2.0 pack."""
    if hasattr(source, "world_setting"):
        return source.world_setting
    return source.world  # v1.0 fallback

def _get_completion_requirements(source):
    """Return completion requirements (empty for v1.0)."""
    return getattr(source, "completion_requirements", ())

def _get_immutable_rules(source):
    if hasattr(source, "world_setting"):
        return source.world_setting.immutable_rules
    return source.world.immutable_rules

def _get_forbidden_content(source):
    if hasattr(source, "world_setting"):
        return source.world_setting.forbidden_content
    return ()
```

## 6. SSE segment_ready Payload (Canonical)

Backend and frontend must agree on this shape:

```json
{
  "segment_id": "seg-...",
  "revision": 18,
  "terminal": "decision",
  "choices": [{"id":"...","action_id":"...","label":"...","intent":"...","target_character_id":null,"preview":null}],
  "ending": null,
  "blocks": []
}
```

For ending terminal:
```json
{
  "segment_id": "seg-...",
  "revision": 20,
  "terminal": "ending",
  "choices": null,
  "ending": {"ending_id":"...","title":"...","tone":"...","terminal_state_summary":"..."},
  "blocks": []
}
```

Frontend `PresentedChoice` must include `target_character_id?: string | null`
and `preview?: string | null`.

Frontend `SegmentReadyData` must include:
```typescript
interface SegmentReadyData {
  segment_id: string
  revision: number
  terminal: "decision" | "ending"
  choices: PresentedChoice[] | null
  ending: { ending_id: string; title: string; tone: string; terminal_state_summary: string } | null
  blocks: NarrativeBlock[]
}
```

## 7. Session Projection (Plan 2 Must Update)

Plan 2 adds a task to update `projection.py`. `SessionProjection` gains:

```python
segment_blocks: tuple[NarrativeBlock, ...] = ()
segment_revision: int | None = None
segment_choices: tuple[PresentedChoice, ...] = ()
segment_ending: EndingProjection | None = None
cleared: bool | None = None  # only when ended
completion_summaries: tuple[CompletionSummary, ...] = ()
```

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

## 8. CompletionAssessment Conversion

TurnOrchestrator converts runtime types to state types before events:

```python
records = tuple(
    CompletionAssessmentRecord(
        requirement_id=a.requirement_id,
        satisfied=a.satisfied,
        cited_event_ids=a.cited_event_ids,
        rationale=a.rationale,
    )
    for a in completion_result.assessments
)
```

## 9. Python Version

All plans: **Python 3.11+**.

## 10. Action Resolver

Plan 2's TurnOrchestrator must not hardcode `ActionResolution(action_id=..., outcome="success")`.
It must call `self.planner.resolve_action(pack, state, choice)` (existing PlannerPort)
and `validate_action_resolution()` on the result. The PlannerPort is already
injected into RuntimeService. TurnOrchestrator receives it.

If a separate `ActionResolverPort` is desired, it can alias `PlannerPort.resolve_action`.
But the key requirement is: call the real resolver + validator, not inline a stub.

## 11. Guard Minimum Implementation

Plan 2 must include at least a `DeterministicGuard` (not just FakeGuard):
- Check segment/scene ID consistency between plan and draft
- Check all speakers in drafts exist in plan's present_character_ids
- Check all choice IDs in draft match plan's choice IDs
- Check narration blocks have no character_id
- Check scene count does not exceed max_scenes

Plan 3 adds the semantic critic layer on top.

## 12. Heartbeat and retry_after

Plan 2's SSE stream must include:
- `heartbeat` events during long generation (every 15s)
- `retry_after` event when command lease is still active for a duplicate command

Frontend (Plan 4) must handle both (heartbeat = no-op, retry_after = show retry message).
