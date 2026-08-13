# Deterministic Dramatic Kernel Implementation Plan

> Implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

> **Status:** Complete. Tasks 1-6 are implemented and verified. Task 6's real-Pack
> migration was completed early in `5acd7c6` while resolving Task 1's executable
> contract review findings.

**Goal:** Build the deterministic Pack schema, dramatic authority, semantic event kernel, same-batch evidence resolution, and full-history completion judge required by the approved `cafe_mystery` vertical slice.

**Architecture:** V2 Packs declare only opening conflict vocabulary and deterministic completion evidence. Immutable semantic events are the sole source of dramatic authority; reducers reconstruct current state, focused derivation helpers emit costs and relationship turning points, and the completion judge evaluates the complete event history. Proposal-local references are resolved to preallocated event IDs before simulation, while SQLite accepts only validated contiguous envelopes.

**Tech Stack:** Python 3.11, Pydantic 2, PyYAML, SQLite, pytest, uv.

---

## File Map

- `backend/src/story/script_pack/models.py`: Pack-authored conflict axes, relationship tags, turning points, obligation kinds, and recursive completion evidence types.
- `backend/src/story/script_pack/compiler.py`: Cross-reference and uniqueness checks for the new Pack declarations and evidence DSL.
- `backend/src/story/script_pack/__init__.py`: Public Pack type exports.
- `backend/src/story/state/models.py`: Replayed character semantics and dramatic runtime state.
- `backend/src/story/state/events.py`: Immutable semantic choice, stance, relationship, promise, obligation, consequence, cost, and turning-point events.
- `backend/src/story/state/reducer.py`: Deterministic transitions for all new semantic events.
- `backend/src/story/state/semantic_derivation.py`: Pure cost and relationship turning-point derivation helpers.
- `backend/src/story/state/event_batch.py`: Proposal-local reference validation and event-ID preallocation.
- `backend/src/story/state/__init__.py`: Public state/event/kernel exports.
- `backend/src/story/storage/event_store.py`: Atomic persistence of preallocated envelopes.
- `backend/src/story/runtime/completion_judge.py`: Recursive evidence evaluation over complete history.
- `backend/src/story/runtime/turn_orchestrator.py`: Include persisted history when the legacy V2 ending path invokes the judge.
- `backend/script_packs/cafe_mystery/pack.yaml`: Machine-verifiable dramatic vocabulary, second truth fact, and approved completion rules.
- `backend/tests/story_factories.py`: Minimal V2 Pack fixture using the new contract.
- `backend/tests/test_script_pack_v2.py`: Pack model/compiler contract tests.
- `backend/tests/test_story_state.py`: Initial dramatic-state construction tests.
- `backend/tests/test_story_reducer.py`: Semantic event replay and invariant tests.
- `backend/tests/test_semantic_derivation.py`: Kernel-derived cost and turning-point tests.
- `backend/tests/test_event_batch.py`: Proposal-local reference and preallocation tests.
- `backend/tests/test_story_event_store.py`: Preallocated-envelope persistence tests.
- `backend/tests/test_completion_judge.py`: Recursive full-history evidence tests.

### Task 1: Pack Dramatic Vocabulary And Completion DSL

**Files:**
- Modify: `backend/src/story/script_pack/models.py`
- Modify: `backend/src/story/script_pack/compiler.py`
- Modify: `backend/src/story/script_pack/__init__.py`
- Modify: `backend/tests/story_factories.py`
- Modify: `backend/tests/test_script_pack_v2.py`

- [x] **Step 1: Replace evidence-hint tests with failing recursive DSL and dramatic vocabulary tests**

Add tests that construct this contract and assert exact validation behavior:

```python
def _dramatic_pack_raw() -> dict:
    raw = minimal_pack_v2_dict()
    raw.update(
        conflict_axes=[
            {
                "id": "trust_vs_evidence",
                "values": ["trust", "evidence"],
                "source_character_ids": ["alice", "bob"],
                "initial_incompatibility": "Alice needs trust while Bob requires proof.",
            }
        ],
        relationship_event_tags=[
            {"id": "public_trust", "description": "Trusted someone in public."},
            {"id": "accepted_truth", "description": "Accepted an inconvenient truth."},
        ],
        relationship_turning_points=[
            {
                "id": "alice_mutual_trust",
                "character_id": "alice",
                "all_of_event_tags": ["public_trust", "accepted_truth"],
                "min_distinct_source_choices": 2,
            }
        ],
        obligation_kinds=[
            {
                "id": "keep_secret",
                "description": "Keep a disclosed secret.",
                "burden": 2,
                "allowed_outcomes": ["fulfilled", "broken", "released"],
            }
        ],
        completion_requirements=[
            {
                "id": "complete_arc",
                "description": "Reveal truth and carry a cost.",
                "all": [
                    {"fact_revealed": {"fact_id": "who_took_notebook"}},
                    {
                        "any": [
                            {"relationship_turning_point": {"turning_point_id": "alice_mutual_trust"}},
                            {"obligation_fulfilled": {"min_burden": 1}},
                            {"cost_incurred": {"min_severity": 1}},
                            {
                                "stance_defended": {
                                    "min_challenges": 1,
                                    "min_cost_severity": 1,
                                }
                            },
                        ]
                    },
                ],
            }
        ],
    )
    return raw


def test_v2_accepts_recursive_completion_evidence():
    compiled = compile_source(_dramatic_pack_raw())
    requirement = compiled.source.completion_requirements[0]
    assert requirement.all[0].fact_revealed.fact_id == "who_took_notebook"


@pytest.mark.parametrize("node", [{"all": []}, {"any": []}, {}])
def test_v2_rejects_empty_completion_evidence(node):
    raw = _dramatic_pack_raw()
    raw["completion_requirements"][0] = {"id": "bad", "description": "bad", **node}
    with pytest.raises(PackCompileError):
        compile_source(raw)


def test_v2_rejects_mixed_completion_evidence_node():
    raw = _dramatic_pack_raw()
    raw["completion_requirements"][0] = {
        "id": "bad",
        "description": "bad",
        "fact_revealed": {"fact_id": "who_took_notebook"},
        "cost_incurred": {"min_severity": 1},
    }
    with pytest.raises(PackCompileError, match="exactly one evidence operator"):
        compile_source(raw)


def test_v2_rejects_unknown_turning_point_reference():
    raw = _dramatic_pack_raw()
    raw["completion_requirements"][0]["all"][1] = {
        "relationship_turning_point": {"turning_point_id": "missing"}
    }
    with pytest.raises(PackCompileError, match="unknown turning point missing"):
        compile_source(raw)


def test_v2_rejects_future_fields_on_conflict_axis():
    raw = _dramatic_pack_raw()
    raw["conflict_axes"][0]["activation"] = "after scene 2"
    with pytest.raises(PackCompileError, match="activation"):
        compile_source(raw)
```

- [x] **Step 2: Run the focused Pack tests and verify RED**

Run: `cd backend && uv run pytest tests/test_script_pack_v2.py -q`

Expected: FAIL because `conflict_axes`, semantic declaration types, and recursive evidence operators are not accepted and `evidence_hints` is still the active contract.

- [x] **Step 3: Implement the Pack source types**

Replace `EvidenceHintsSource` with these strict models and add them to `ScriptPackSourceV2`:

```python
class ConflictAxisSource(StrictModel):
    id: SafeId
    values: tuple[SafeId, ...] = Field(min_length=2)
    source_character_ids: tuple[SafeId, ...] = Field(min_length=2)
    initial_incompatibility: str = Field(min_length=1)


class RelationshipEventTagSource(StrictModel):
    id: SafeId
    description: str = Field(min_length=1)


class RelationshipTurningPointSource(StrictModel):
    id: SafeId
    character_id: SafeId
    all_of_event_tags: tuple[SafeId, ...] = Field(min_length=1)
    min_distinct_source_choices: int = Field(default=1, ge=1)


class ObligationKindSource(StrictModel):
    id: SafeId
    description: str = Field(min_length=1)
    burden: int = Field(ge=1, le=3)
    allowed_outcomes: tuple[Literal["fulfilled", "broken", "released"], ...] = Field(
        min_length=1
    )


class FactRevealedEvidenceSource(StrictModel):
    fact_id: SafeId


class RelationshipTurningPointEvidenceSource(StrictModel):
    turning_point_id: SafeId


class ObligationFulfilledEvidenceSource(StrictModel):
    min_burden: int = Field(ge=1, le=3)


class CostIncurredEvidenceSource(StrictModel):
    min_severity: int = Field(ge=1, le=3)


class StanceDefendedEvidenceSource(StrictModel):
    min_challenges: int = Field(ge=1)
    min_cost_severity: int = Field(ge=1, le=3)


class CompletionEvidenceSource(StrictModel):
    all: tuple["CompletionEvidenceSource", ...] | None = None
    any: tuple["CompletionEvidenceSource", ...] | None = None
    fact_revealed: FactRevealedEvidenceSource | None = None
    relationship_turning_point: RelationshipTurningPointEvidenceSource | None = None
    obligation_fulfilled: ObligationFulfilledEvidenceSource | None = None
    cost_incurred: CostIncurredEvidenceSource | None = None
    stance_defended: StanceDefendedEvidenceSource | None = None

    @model_validator(mode="after")
    def require_one_operator(self) -> "CompletionEvidenceSource":
        values = [
            self.all,
            self.any,
            self.fact_revealed,
            self.relationship_turning_point,
            self.obligation_fulfilled,
            self.cost_incurred,
            self.stance_defended,
        ]
        if sum(value is not None for value in values) != 1:
            raise ValueError("completion node must contain exactly one evidence operator")
        if self.all is not None and not self.all:
            raise ValueError("completion all group cannot be empty")
        if self.any is not None and not self.any:
            raise ValueError("completion any group cannot be empty")
        return self


class CompletionRequirementSource(CompletionEvidenceSource):
    id: SafeId
    description: str = Field(min_length=1)
```

Add these V2 fields:

```python
conflict_axes: tuple[ConflictAxisSource, ...] = Field(min_length=1)
relationship_event_tags: tuple[RelationshipEventTagSource, ...] = Field(min_length=1)
relationship_turning_points: tuple[RelationshipTurningPointSource, ...] = Field(min_length=1)
obligation_kinds: tuple[ObligationKindSource, ...] = Field(min_length=1)
```

- [x] **Step 4: Implement compiler uniqueness and recursive reference validation**

Add recursive traversal and validations:

```python
def _walk_completion_evidence(node: CompletionEvidenceSource):
    if node.all is not None:
        for child in node.all:
            yield from _walk_completion_evidence(child)
    elif node.any is not None:
        for child in node.any:
            yield from _walk_completion_evidence(child)
    else:
        yield node


def _v2_dramatic_reference_errors(source: ScriptPackSourceV2) -> list[str]:
    errors: list[str] = []
    character_ids = {item.id for item in source.characters}
    fact_ids = {
        *(item.id for item in source.facts.fixed),
        *(item.id for item in source.facts.latent_questions),
        *(item.id for item in source.facts.derived),
    }
    tag_ids = {item.id for item in source.relationship_event_tags}
    turning_point_ids = {item.id for item in source.relationship_turning_points}
    for axis in source.conflict_axes:
        for character_id in axis.source_character_ids:
            if character_id not in character_ids:
                errors.append(f"conflict axis {axis.id} references unknown character {character_id}")
    for turning_point in source.relationship_turning_points:
        if turning_point.character_id not in character_ids:
            errors.append(
                f"turning point {turning_point.id} references unknown character "
                f"{turning_point.character_id}"
            )
        for tag in turning_point.all_of_event_tags:
            if tag not in tag_ids:
                errors.append(f"turning point {turning_point.id} references unknown tag {tag}")
    for requirement in source.completion_requirements:
        for leaf in _walk_completion_evidence(requirement):
            if leaf.fact_revealed and leaf.fact_revealed.fact_id not in fact_ids:
                errors.append(
                    f"completion requirement {requirement.id} references unknown fact "
                    f"{leaf.fact_revealed.fact_id}"
                )
            if (
                leaf.relationship_turning_point
                and leaf.relationship_turning_point.turning_point_id not in turning_point_ids
            ):
                errors.append(
                    f"completion requirement {requirement.id} references unknown turning point "
                    f"{leaf.relationship_turning_point.turning_point_id}"
                )
    return errors
```

Also add duplicate-ID checks for conflict axes, relationship tags, turning points, and obligation kinds, plus duplicate `values`, `source_character_ids`, `all_of_event_tags`, and `allowed_outcomes` within their declarations.

- [x] **Step 5: Update the minimal V2 fixture and public exports**

Give `minimal_pack_v2_dict()` one conflict axis, two relationship tags, one Alice turning point, one obligation kind, and requirements using `fact_revealed`, `relationship_turning_point`, and `cost_incurred`. Export every new source type and remove `EvidenceHintsSource` from `script_pack.__all__`.

- [x] **Step 6: Run Pack tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_script_pack_v2.py tests/test_script_pack_compiler.py tests/test_script_pack_models.py -q`

Expected: PASS.

- [x] **Step 7: Commit Task 1**

```bash
git add backend/src/story/script_pack backend/tests/story_factories.py backend/tests/test_script_pack_v2.py
git commit -m "feat: define dramatic pack evidence contract"
```

### Task 2: Dramatic State, Semantic Events, And Reducer

**Files:**
- Modify: `backend/src/story/state/models.py`
- Modify: `backend/src/story/state/events.py`
- Modify: `backend/src/story/state/reducer.py`
- Modify: `backend/src/story/state/__init__.py`
- Modify: `backend/tests/test_story_state.py`
- Modify: `backend/tests/test_story_reducer.py`

- [x] **Step 1: Write failing initial-state and reducer tests**

Add tests covering default dramatic authority and immutable replay:

```python
def test_v2_initial_state_contains_empty_dramatic_authority():
    state = initial_session_state(compile_source(minimal_pack_v2_dict()), "s1", 7)
    assert state.drama.arc_phase == DramaticArcPhase.APPROACH
    assert state.drama.primary_question is None
    assert state.drama.promises == {}
    assert state.drama.obligations == {}
    assert state.drama.stances == {}


def test_relationship_event_updates_character_semantics():
    state = _make_minimal_state(characters={"alice": CharacterRuntime(character_id="alice")})
    event = RelationshipEventRecorded(
        character_id="alice",
        tag="public_trust",
        source_choice_event_id="choice-1",
        scene_event_id="scene-1",
    )
    result = apply_event(state, EventEnvelope(event_id="relationship-1", session_id="s1", sequence=1, event=event))
    assert result.characters["alice"].relationship_event_ids == ("relationship-1",)


def test_obligation_cannot_be_resolved_twice():
    state = apply_event(
        _make_minimal_state(),
        EventEnvelope(
            event_id="created-1",
            session_id="s1",
            sequence=1,
            event=ObligationCreated(
                obligation_id="secret-1",
                kind="keep_secret",
                burden=2,
                source_choice_event_id="choice-1",
            ),
        ),
    )
    state = apply_event(
        state,
        EventEnvelope(
            event_id="resolved-1",
            session_id="s1",
            sequence=2,
            event=ObligationResolved(
                obligation_id="secret-1",
                outcome="fulfilled",
                resolution_scene_event_id="scene-2",
            ),
        ),
    )
    with pytest.raises(StateTransitionError, match="already resolved"):
        apply_event(
            state,
            EventEnvelope(
                event_id="resolved-2",
                session_id="s1",
                sequence=3,
                event=ObligationResolved(
                    obligation_id="secret-1",
                    outcome="broken",
                    resolution_scene_event_id="scene-3",
                ),
            ),
        )
```

Cover stance establishment/reinforcement/challenge, promise state transitions, scheduled consequence realization, one-time turning points, cost recording, primary dramatic question replacement, and monotonic arc phases.

- [x] **Step 2: Run state tests and verify RED**

Run: `cd backend && uv run pytest tests/test_story_state.py tests/test_story_reducer.py -q`

Expected: FAIL on missing `DramaticState`, semantic event classes, and reducer branches.

- [x] **Step 3: Add dramatic runtime models**

Add frozen models and attach `drama: DramaticState = Field(default_factory=DramaticState)` to `SessionState`:

```python
class DramaticArcPhase(str, Enum):
    APPROACH = "approach"
    FRACTURE = "fracture"
    ACCOUNTABILITY = "accountability"


class PromiseStatus(str, Enum):
    OPEN = "open"
    ESCALATED = "escalated"
    TRANSFORMED = "transformed"
    FULFILLED = "fulfilled"
    BROKEN = "broken"


class DramaticQuestionRuntime(FrozenModel):
    key: str
    text: str
    source_event_id: str


class PromiseRuntime(FrozenModel):
    promise_id: str
    expectation: str
    source_event_id: str
    involved_character_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
    opened_at_decision: int = Field(ge=0)
    soft_deadline_decision: int = Field(ge=1)
    hard_deadline_decision: int = Field(ge=1)
    status: PromiseStatus = PromiseStatus.OPEN
    payoff_event_ids: tuple[str, ...] = ()


class ObligationRuntime(FrozenModel):
    obligation_id: str
    kind: str
    burden: int = Field(ge=1, le=3)
    source_choice_event_id: str
    status: Literal["open", "fulfilled", "broken", "released"] = "open"
    resolution_scene_event_id: str | None = None
    resolution_event_id: str | None = None


class StanceRuntime(FrozenModel):
    key: str
    axis: str
    value: str
    relation: Literal["established", "reinforced", "qualified", "contradicted"]
    expression_event_ids: tuple[str, ...]
    source_choice_event_ids: tuple[str, ...]
    challenge_event_ids: tuple[str, ...] = ()


class ScheduledConsequenceRuntime(FrozenModel):
    consequence_id: str
    cause_event_id: str
    required_effect: str
    due_after_decision: int = Field(ge=1)
    hard_deadline_decision: int = Field(ge=1)
    status: Literal["scheduled", "realized", "broken"] = "scheduled"
    realization_event_id: str | None = None


class DramaticState(FrozenModel):
    primary_question: DramaticQuestionRuntime | None = None
    promises: dict[str, PromiseRuntime] = Field(default_factory=dict)
    obligations: dict[str, ObligationRuntime] = Field(default_factory=dict)
    stances: dict[str, StanceRuntime] = Field(default_factory=dict)
    scheduled_consequences: dict[str, ScheduledConsequenceRuntime] = Field(default_factory=dict)
    reached_turning_point_ids: frozenset[str] = frozenset()
    cost_event_ids: tuple[str, ...] = ()
    arc_phase: DramaticArcPhase = DramaticArcPhase.APPROACH
    decision_count: int = Field(default=0, ge=0)
```

Extend `CharacterRuntime` with `current_desire`, `current_fear`, `emotional_condition`, `judgment_of_protagonist`, `boundary_being_tested`, `relationship_event_ids`, `unresolved_obligation_ids`, and `turning_point_ids`, all defaulted for saved-state compatibility during development reset.

- [x] **Step 4: Add semantic event types**

Extend `PlayerActionSelected` with optional kernel-copied choice meaning:

```python
stance_axis: str | None = None
stance_value: str | None = None
accepted_cost_category: str | None = None
potential_obligation_kind: str | None = None
conflict_axis_id: str | None = None
```

Extend `RelationshipChanged` with optional `source_choice_event_id` and
`relationship_event_id`. Legacy callers may omit them, but a relationship loss
qualifies as a semantic cost only when both links are present and validated.

Add `DramaticQuestionSet`, `StanceExpressed`, `StanceChallenged`, `RelationshipEventRecorded`, `RelationshipTurningPointReached`, `PromiseOpened`, `PromiseChanged`, `ObligationCreated`, `ObligationResolved`, `ConsequenceScheduled`, `ConsequenceRealized`, `CostIncurred`, and `ArcPressureAdvanced` to `StoryEvent`. Every evidence link is an explicit `*_event_id` or `*_event_ids` field.

- [x] **Step 5: Implement reducer branches and invariants**

Implement copy-on-write transitions. Enforce unknown character rejection, unique IDs, valid lifecycle transitions, matching stance keys, one-time turning points, unresolved-obligation membership, and monotonic `approach -> fracture -> accountability` progression. Increment `drama.decision_count` on `PlayerActionSelected`.

- [x] **Step 6: Run state tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_story_state.py tests/test_story_reducer.py -q`

Expected: PASS.

- [x] **Step 7: Commit Task 2**

```bash
git add backend/src/story/state backend/tests/test_story_state.py backend/tests/test_story_reducer.py
git commit -m "feat: add replayable dramatic authority"
```

### Task 3: Deterministic Semantic Derivation

**Files:**
- Create: `backend/src/story/state/semantic_derivation.py`
- Create: `backend/tests/test_semantic_derivation.py`
- Modify: `backend/src/story/state/__init__.py`

- [x] **Step 1: Write failing turning-point derivation tests**

```python
def test_turning_point_requires_all_tags_and_distinct_choices():
    definition = RelationshipTurningPointSource(
        id="alice_mutual_trust",
        character_id="alice",
        all_of_event_tags=("public_trust", "accepted_truth"),
        min_distinct_source_choices=2,
    )
    trace = (
        envelope("r1", 1, RelationshipEventRecorded(
            character_id="alice", tag="public_trust",
            source_choice_event_id="choice-1", scene_event_id="scene-1")),
        envelope("r2", 2, RelationshipEventRecorded(
            character_id="alice", tag="accepted_truth",
            source_choice_event_id="choice-2", scene_event_id="scene-2")),
    )
    events = derive_relationship_turning_points((definition,), trace)
    assert events == (
        RelationshipTurningPointReached(
            turning_point_id="alice_mutual_trust",
            character_id="alice",
            relationship_event_ids=("r1", "r2"),
        ),
    )


def test_turning_point_is_not_derived_after_it_was_reached():
    trace = relationship_trace() + (
        envelope("tp1", 3, RelationshipTurningPointReached(
            turning_point_id="alice_mutual_trust",
            character_id="alice",
            relationship_event_ids=("r1", "r2"))),
    )
    assert derive_relationship_turning_points((definition(),), trace) == ()
```

- [x] **Step 2: Write failing cost derivation association tests**

```python
def test_relationship_loss_derives_cost_for_same_choice_and_category():
    choice = PlayerActionSelected(
        decision_id="d1",
        option_id="protect_alice",
        idempotency_key="k1",
        accepted_cost_category="bob_trust",
    )
    change = RelationshipChanged(
        character_id="bob",
        axis="trust",
        delta=-10,
        source_choice_event_id="choice-1",
        relationship_event_id="relationship-1",
    )
    semantic = RelationshipEventRecorded(
        character_id="bob",
        tag="resented_public_choice",
        source_choice_event_id="choice-1",
        scene_event_id="scene-1",
    )
    assert derive_cost_incurred("choice-1", choice, "effect-1", change, semantic) == CostIncurred(
        severity=2,
        category="bob_trust",
        source_choice_event_id="choice-1",
        effect_event_ids=("effect-1", "relationship-1"),
    )


def test_unrelated_relationship_loss_cannot_satisfy_choice_cost():
    change = relationship_change(source_choice_event_id="choice-2")
    assert derive_cost_incurred("choice-1", costly_choice(), "effect-1", change, semantic_event()) is None


def test_obligation_cost_uses_pack_burden_not_model_severity():
    obligation = ObligationCreated(
        obligation_id="secret-1",
        kind="keep_secret",
        burden=3,
        source_choice_event_id="choice-1",
    )
    cost = derive_cost_incurred("choice-1", costly_choice("responsibility"), "obligation-1", obligation)
    assert cost.severity == 3
    assert cost.category == "responsibility"
```

- [x] **Step 3: Run derivation tests and verify RED**

Run: `cd backend && uv run pytest tests/test_semantic_derivation.py -q`

Expected: FAIL because the module and derivation functions do not exist.

- [x] **Step 4: Implement focused pure derivation helpers**

Implement:

```python
def derive_relationship_turning_points(
    definitions: tuple[RelationshipTurningPointSource, ...],
    event_trace: tuple[EventEnvelope, ...],
) -> tuple[RelationshipTurningPointReached, ...]:
    reached = {
        envelope.event.turning_point_id
        for envelope in event_trace
        if isinstance(envelope.event, RelationshipTurningPointReached)
    }
    relationship_events = [
        envelope for envelope in event_trace
        if isinstance(envelope.event, RelationshipEventRecorded)
    ]
    derived = []
    for definition in definitions:
        if definition.id in reached:
            continue
        matching = [
            envelope for envelope in relationship_events
            if envelope.event.character_id == definition.character_id
            and envelope.event.tag in definition.all_of_event_tags
        ]
        tags = {envelope.event.tag for envelope in matching}
        choices = {envelope.event.source_choice_event_id for envelope in matching}
        if set(definition.all_of_event_tags) <= tags and (
            len(choices) >= definition.min_distinct_source_choices
        ):
            constituent_ids = tuple(
                envelope.event_id for envelope in matching
                if envelope.event.tag in definition.all_of_event_tags
            )
            derived.append(RelationshipTurningPointReached(
                turning_point_id=definition.id,
                character_id=definition.character_id,
                relationship_event_ids=constituent_ids,
            ))
    return tuple(derived)
```

Implement `derive_cost_incurred()` so relationship loss requires `delta < 0`, a semantic relationship event, matching character, matching relationship-event ID, and the same source choice on every input. Severity is `min(3, abs(delta) // 5)`. Obligation cost requires the same source choice and copies `burden`. Both require `accepted_cost_category` on the selected choice and copy it exactly into `CostIncurred`.

- [x] **Step 5: Run derivation tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_semantic_derivation.py -q`

Expected: PASS.

- [x] **Step 6: Commit Task 3**

```bash
git add backend/src/story/state/semantic_derivation.py backend/src/story/state/__init__.py backend/tests/test_semantic_derivation.py
git commit -m "feat: derive dramatic costs and turning points"
```

### Task 4: Proposal-Local References And Preallocated Persistence

**Files:**
- Create: `backend/src/story/state/event_batch.py`
- Create: `backend/tests/test_event_batch.py`
- Modify: `backend/src/story/state/__init__.py`
- Modify: `backend/src/story/storage/event_store.py`
- Modify: `backend/tests/test_story_event_store.py`

- [x] **Step 1: Write failing proposal-local resolution tests**

```python
def test_prepare_event_batch_resolves_forward_and_backward_local_refs():
    proposals = (
        ProposedEvent(
            local_ref="choice:selected",
            event=PlayerActionSelected(
                decision_id="d1", option_id="a", idempotency_key="k1",
                accepted_cost_category="alice_trust",
            ),
        ),
        ProposedEvent(
            local_ref="effect:relationship_1",
            event=RelationshipChanged(
                character_id="alice", axis="trust", delta=-5,
                source_choice_event_id="choice:selected",
                relationship_event_id="relationship:current",
            ),
        ),
        ProposedEvent(
            local_ref="relationship:current",
            event=RelationshipEventRecorded(
                character_id="alice", tag="hurt_by_distance",
                source_choice_event_id="choice:selected",
                scene_event_id="scene:current",
            ),
        ),
        ProposedEvent(
            local_ref="scene:current",
            event=SceneCommitted(
                scene_id="scene-1", terminal="continue", location_id="cafe",
                present_character_ids=("alice",),
                blocks=(NarrativeBlock(kind="narration", text="Alice steps back."),),
            ),
        ),
    )
    envelopes = prepare_event_batch("s1", 0, proposals)
    ids = {proposal.local_ref: envelope.event_id for proposal, envelope in zip(proposals, envelopes)}
    assert envelopes[1].event.source_choice_event_id == ids["choice:selected"]
    assert envelopes[1].event.relationship_event_id == ids["relationship:current"]
    assert envelopes[2].event.scene_event_id == ids["scene:current"]


def test_prepare_event_batch_rejects_unknown_event_reference():
    proposals = (
        ProposedEvent(
            local_ref="relationship:current",
            event=RelationshipEventRecorded(
                character_id="alice", tag="public_trust",
                source_choice_event_id="choice:missing",
                scene_event_id="committed-scene-id",
            ),
        ),
    )
    with pytest.raises(EventReferenceError, match="choice:missing"):
        prepare_event_batch("s1", 0, proposals, committed_event_ids={"committed-scene-id"})


def test_prepare_event_batch_rejects_duplicate_local_refs():
    with pytest.raises(EventReferenceError, match="duplicate local reference"):
        prepare_event_batch("s1", 0, duplicate_ref_proposals())
```

- [x] **Step 2: Write failing preallocated store tests**

```python
def test_append_envelopes_preserves_preallocated_ids(tmp_path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    envelopes = prepare_event_batch(
        "session_01",
        0,
        (ProposedEvent(local_ref="effect:one", event=RelationshipChanged(
            character_id="alice", axis="trust", delta=1)),),
    )
    state, persisted = store.append_envelopes("session_01", 0, envelopes)
    assert persisted[0].event_id == envelopes[0].event_id
    assert store.load_events("session_01") == envelopes
    assert state.revision == 1


@pytest.mark.parametrize("mutation", ["wrong_session", "gap", "duplicate_id"])
def test_append_envelopes_rejects_invalid_preallocated_batch(tmp_path, mutation):
    store = prepared_store(tmp_path)
    with pytest.raises(StoryStoreError):
        store.append_envelopes("session_01", 0, invalid_envelopes(mutation))
    assert store.event_count("session_01") == 0
```

- [x] **Step 3: Run batch/store tests and verify RED**

Run: `cd backend && uv run pytest tests/test_event_batch.py tests/test_story_event_store.py -q`

Expected: FAIL because `ProposedEvent`, `prepare_event_batch`, and `append_envelopes` do not exist.

- [x] **Step 4: Implement structured event-reference resolution**

Create:

```python
class EventReferenceError(ValueError):
    pass


class ProposedEvent(FrozenModel):
    local_ref: str = Field(pattern=r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")
    event: StoryEvent


def prepare_event_batch(
    session_id: str,
    current_revision: int,
    proposals: tuple[ProposedEvent, ...],
    *,
    committed_event_ids: Collection[str] = (),
    event_id_factory: Callable[[], str] = lambda: str(uuid4()),
) -> tuple[EventEnvelope, ...]:
    if not proposals:
        raise EventReferenceError("event proposal batch cannot be empty")
    local_refs = [proposal.local_ref for proposal in proposals]
    if len(local_refs) != len(set(local_refs)):
        raise EventReferenceError("duplicate local reference")
    allocated = {local_ref: event_id_factory() for local_ref in local_refs}
    resolved_events = tuple(
        _resolve_event_references(proposal.event, allocated, set(committed_event_ids))
        for proposal in proposals
    )
    return tuple(
        EventEnvelope(
            event_id=allocated[proposal.local_ref],
            session_id=session_id,
            sequence=current_revision + index,
            event=event,
        )
        for index, (proposal, event) in enumerate(zip(proposals, resolved_events), start=1)
    )
```

Implement `_resolve_event_references()` through `model_dump(mode="python")` recursion, resolving only fields named `*_event_id` and `*_event_ids`, then reconstructing the event with `type(event).model_validate(data)`. A reference is valid only when it is a known local ref or an explicitly supplied committed event ID.

- [x] **Step 5: Implement `StoryEventStore.append_envelopes`**

Validate non-empty input, matching session IDs, contiguous sequences beginning at `expected_revision + 1`, and unique event IDs before `apply_events`. Persist the supplied envelopes unchanged in the existing immediate SQLite transaction. Refactor `append()` to allocate ordinary envelopes and delegate to the same private transactional path.

- [x] **Step 6: Run batch/store tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_event_batch.py tests/test_story_event_store.py -q`

Expected: PASS, including forward-reference resolution and atomic rollback tests.

- [x] **Step 7: Commit Task 4**

```bash
git add backend/src/story/state/event_batch.py backend/src/story/state/__init__.py backend/src/story/storage/event_store.py backend/tests/test_event_batch.py backend/tests/test_story_event_store.py
git commit -m "feat: preallocate semantic event batches"
```

### Task 5: Full-History Completion Evaluation

**Files:**
- Modify: `backend/src/story/runtime/completion_judge.py`
- Modify: `backend/src/story/runtime/turn_orchestrator.py`
- Replace: `backend/tests/test_completion_judge.py`
- Modify: `backend/tests/test_turn_orchestrator.py`

- [x] **Step 1: Write failing recursive leaf tests**

Build a trace whose evidence occurs across early and terminal turns, then assert:

```python
def test_judge_evaluates_recursive_requirements_from_complete_history():
    requirement = CompletionRequirementSource(
        id="complete_arc",
        description="Reveal truth, form a bond, and carry a cost.",
        all=(
            CompletionEvidenceSource(fact_revealed={"fact_id": "notebook_holder"}),
            CompletionEvidenceSource(any=(
                CompletionEvidenceSource(relationship_turning_point={
                    "turning_point_id": "alice_mutual_trust"
                }),
                CompletionEvidenceSource(obligation_fulfilled={"min_burden": 2}),
            )),
            CompletionEvidenceSource(cost_incurred={"min_severity": 1}),
        ),
    )
    result = CompletionJudge().evaluate((requirement,), final_state(), full_history_trace())
    assert result.cleared is True
    assert set(result.assessments[0].cited_event_ids) >= {
        "fact-revealed", "fact-committed", "fact-evidence",
        "turning-point", "relationship-1", "relationship-2",
        "cost-1", "choice-1", "effect-1",
    }
```

Add isolated tests for all five leaves, nested `all`/`any`, unsatisfied branches, obligation burden, and stance defense ordering.

- [x] **Step 2: Add the unrelated-cost regression test**

Create a trace where `choice-1` declares category `alice_safety`, but `CostIncurred` references `choice-2` or category `bob_trust`. Assert `cost_incurred` and `stance_defended` are unsatisfied. This is a mandatory review finding: no unrelated relationship loss may satisfy the chosen risk.

- [x] **Step 3: Add the legacy orchestrator full-history regression test**

Persist an early `FactCommitted` and `FactRevealed`, then invoke the terminal branch with a final batch that contains only the ending. Assert the completion judge receives persisted envelopes plus the final pending envelopes, in sequence order.

- [x] **Step 4: Run completion/orchestrator tests and verify RED**

Run: `cd backend && uv run pytest tests/test_completion_judge.py tests/test_turn_orchestrator.py -q`

Expected: FAIL because the judge still reads `evidence_hints` and the orchestrator passes only the terminal batch.

- [x] **Step 5: Implement recursive deterministic evaluation**

Implement a private result type and evaluator:

```python
@dataclass(frozen=True)
class _EvidenceResult:
    satisfied: bool
    cited_event_ids: tuple[str, ...] = ()
    rationale: str = ""


def _evaluate_node(
    node: CompletionEvidenceSource,
    final_state: SessionState,
    event_trace: tuple[EventEnvelope, ...],
) -> _EvidenceResult:
    if node.all is not None:
        children = tuple(_evaluate_node(child, final_state, event_trace) for child in node.all)
        return _combine_all(children)
    if node.any is not None:
        children = tuple(_evaluate_node(child, final_state, event_trace) for child in node.any)
        return _combine_any(children)
    if node.fact_revealed is not None:
        return _fact_revealed(node.fact_revealed.fact_id, event_trace)
    if node.relationship_turning_point is not None:
        return _turning_point(node.relationship_turning_point.turning_point_id, event_trace)
    if node.obligation_fulfilled is not None:
        return _obligation_fulfilled(node.obligation_fulfilled.min_burden, event_trace)
    if node.cost_incurred is not None:
        return _cost_incurred(node.cost_incurred.min_severity, event_trace)
    assert node.stance_defended is not None
    return _stance_defended(
        node.stance_defended.min_challenges,
        node.stance_defended.min_cost_severity,
        event_trace,
    )
```

For `any`, select the first satisfied branch in authored order. Deduplicate citations while preserving event order. Defensively validate that a cost's selected choice exists, the cost category equals `PlayerActionSelected.accepted_cost_category`, and every effect event exists. For stance defense, require an earlier established event for the same key, challenges strictly between establishment and reinforcement, and a qualifying cost whose `source_choice_event_id` equals the reinforcement's source choice.

- [x] **Step 6: Pass complete history from the legacy terminal caller**

In the ending branch, use:

```python
persisted_history = self.store.load_events(session_id)
completion_result = self.completion_judge.evaluate(
    reqs,
    final_state,
    persisted_history + judge_envelopes,
)
```

This is a compatibility fix only; the new drama turn service remains a later plan.

- [x] **Step 7: Run completion/orchestrator tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_completion_judge.py tests/test_turn_orchestrator.py -q`

Expected: PASS.

- [x] **Step 8: Commit Task 5**

```bash
git add backend/src/story/runtime/completion_judge.py backend/src/story/runtime/turn_orchestrator.py backend/tests/test_completion_judge.py backend/tests/test_turn_orchestrator.py
git commit -m "feat: judge completion from full semantic history"
```

### Task 6: `cafe_mystery` Machine-Verifiable Kernel Configuration

**Files:**
- Modify: `backend/script_packs/cafe_mystery/pack.yaml`
- Modify: `backend/tests/test_script_pack_v2.py`
- Modify: `backend/tests/test_cafe_mystery_pack.py`

- [x] **Step 1: Write failing real-Pack assertions**

```python
def test_cafe_mystery_declares_dramatic_kernel_vocabulary():
    source = compile_script_pack(cafe_pack_path()).source
    assert {axis.id for axis in source.conflict_axes} >= {
        "trust_vs_evidence", "protection_vs_agency"
    }
    assert {point.id for point in source.relationship_turning_points} == {
        "alice_mutual_trust", "bob_earned_respect", "mina_shared_responsibility"
    }
    assert {kind.id for kind in source.obligation_kinds} >= {
        "keep_secret", "explain_lie", "share_risk"
    }


def test_cafe_mystery_completion_is_machine_verifiable():
    compiled = compile_script_pack(cafe_pack_path())
    assert {requirement.id for requirement in compiled.source.completion_requirements} == {
        "truth_understood", "meaningful_bond", "accepted_cost"
    }
    assert "notebook_disappearance_cause" in compiled.fact_ids


def test_cafe_mystery_targets_vertical_slice_bounds():
    source = compile_script_pack(cafe_pack_path()).source
    assert source.identity.expected_minutes == 45
    assert source.experience.min_scenes == 8
    assert source.experience.max_scenes == 14
```

- [x] **Step 2: Run real-Pack tests and verify RED**

Run: `cd backend && uv run pytest tests/test_script_pack_v2.py tests/test_cafe_mystery_pack.py -q`

Expected: FAIL because the Pack still uses evidence hints, lacks dramatic vocabulary, and targets 120 minutes/60 scenes.

- [x] **Step 3: Migrate `pack.yaml`**

Set `expected_minutes: 45`, `min_scenes: 8`, `max_scenes: 14`, and keep three reserved resolution scenes. Add opening-only conflict axes, finite relationship tags, the three approved turning points, and obligation kinds with burdens 1-3. Add latent fact `notebook_disappearance_cause` with authored possible causes and evidence requirement 2.

Replace completion requirements with:

```yaml
completion_requirements:
  - id: truth_understood
    description: "玩家理解谁拿走了笔记本以及笔记本为何易手。"
    all:
      - fact_revealed: {fact_id: notebook_holder}
      - fact_revealed: {fact_id: notebook_disappearance_cause}
  - id: meaningful_bond
    description: "玩家与至少一名核心角色形成不可逆的关系变化。"
    any:
      - relationship_turning_point: {turning_point_id: alice_mutual_trust}
      - relationship_turning_point: {turning_point_id: bob_earned_respect}
      - relationship_turning_point: {turning_point_id: mina_shared_responsibility}
  - id: accepted_cost
    description: "玩家承担了自己立场带来的真实代价或责任。"
    any:
      - obligation_fulfilled: {min_burden: 1}
      - cost_incurred: {min_severity: 1}
      - stance_defended: {min_challenges: 1, min_cost_severity: 1}
```

Do not add activation conditions, scene beats, deadlines, future actions, or payoff instructions to Pack declarations.

- [x] **Step 4: Run real-Pack tests and verify GREEN**

Run: `cd backend && uv run pytest tests/test_script_pack_v2.py tests/test_cafe_mystery_pack.py -q`

Expected: PASS.

- [x] **Step 5: Run format, lint, and deterministic kernel regression suites**

Run:

```bash
cd backend
uv run black --check src/story tests
uv run ruff check src/story tests
uv run pytest \
  tests/test_script_pack_models.py \
  tests/test_script_pack_compiler.py \
  tests/test_script_pack_v2.py \
  tests/test_cafe_mystery_pack.py \
  tests/test_story_state.py \
  tests/test_story_reducer.py \
  tests/test_semantic_derivation.py \
  tests/test_event_batch.py \
  tests/test_story_event_store.py \
  tests/test_completion_judge.py \
  tests/test_turn_orchestrator.py -q
```

Expected: all commands exit 0.

- [x] **Step 6: Run the complete non-live backend suite**

Run: `cd backend && uv run pytest -m "not live" -q`

Expected: PASS. Live provider tests remain outside this deterministic-kernel milestone.

- [x] **Step 7: Commit Task 6**

```bash
git add backend/script_packs/cafe_mystery/pack.yaml backend/tests/test_script_pack_v2.py backend/tests/test_cafe_mystery_pack.py
git commit -m "feat: configure cafe mystery dramatic kernel"
```

## Self-Review Checklist

- Every approved deterministic-kernel requirement maps to a task above.
- Warm opening-seed allocation is intentionally excluded because it belongs to the rolling pre-generation plan; that later plan must assign warmed `session_seed` to the newly created Web session before state fingerprinting.
- Complete history is explicit in Task 5 and includes persisted plus pending terminal events.
- Cost evidence is tied to the same selected choice and copied cost category in Tasks 3 and 5.
- Proposal-local forward references and full same-batch storage identity are tested in Task 4.
- Existing development saves are not upcast. This milestone relies on the approved explicit development-data reset when the new drama service becomes authoritative.
- The plan contains no `TBD`, `TODO`, or undefined implementation placeholder.
