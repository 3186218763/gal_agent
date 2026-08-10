# Dynamic Galgame V2 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Build the deterministic V2 foundation that compiles a unified script pack, initializes layered story state, applies typed append-only domain events, and persists/replays sessions without invoking any model.

**Architecture:** Add a new \`src.story\` bounded package beside the existing V1 code so every task remains runnable while migration is in progress. Script pack sources compile into a hash-addressed frozen Pydantic model that consumers treat as read-only; SessionState is changed only by validated domain events; a SQLite event store provides atomic append, optimistic revision checks, snapshots, and replay.

**Tech Stack:** Python 3.11+, Pydantic v2, PyYAML, standard-library \`ast\` and \`sqlite3\`, pytest/pytest-asyncio, uv.

**Spec:** \`docs/superpowers/specs/2026-08-10-constrained-dynamic-galgame-design.md\`

---

## Scope And Phase Boundary

The approved V2 spec contains four sequential implementation milestones:

1. **Foundation, this plan:** condition DSL, ScriptPack compiler, state, events, reducer, persistence, and validation CLI.
2. **Narrative runtime:** Context Assembler, Planner/Writer model ports, candidate validation, Drama Manager, SceneDraft, Action Resolver, and Resolution Plan.
3. **Playable application:** session service, WebSocket command protocol, API switch, and pure-text novel reader.
4. **Evaluation:** trace store, automated player policies, 100 deterministic runs, model-backed runs, metrics, and human evaluation workflow.

This plan ends with a working, model-free command:

~~~bash
cd backend
uv run python -m src.story.cli validate script_packs/cafe_mystery
~~~

It does not modify \`backend/src/main.py\`, the V1 WebSocket path, or the frontend. Those remain operational until the application milestone switches the authority to V2.

The compiler in this milestone performs every check that can be decided from the
pack alone, including a guaranteed scene-budget fallback. Full bounded
reachability for the normal endings requires the standard-action transition
model and Consequence Simulator, so it belongs to the narrative-runtime plan.
The \`init-session\` command below is a developer fixture for event-store
verification; the later playable session service must add that reachability
gate before V2 becomes authoritative.

---

## Target File Structure

~~~text
backend/
  script_packs/
    cafe_mystery/
      pack.yaml
  src/
    story/
      __init__.py
      cli.py
      conditions.py
      script_pack/
        __init__.py
        models.py
        compiler.py
      state/
        __init__.py
        models.py
        events.py
        reducer.py
      storage/
        __init__.py
        event_store.py
  tests/
    story_factories.py
    test_story_package.py
    test_condition_dsl.py
    test_script_pack_models.py
    test_script_pack_compiler.py
    test_story_state.py
    test_story_reducer.py
    test_story_event_store.py
    test_story_cli.py
~~~

Ownership is deliberate:

- \`conditions.py\` is the only condition parser/evaluator and never calls \`eval\`.
- \`script_pack/models.py\` contains author-facing source models and the frozen compiled wrapper.
- \`script_pack/compiler.py\` owns loading, include resolution, reference validation, condition compilation, and pack hashing.
- \`state/models.py\` owns snapshots only; it contains no mutation methods.
- \`state/events.py\` owns typed change requests.
- \`state/reducer.py\` is the only pure state transition implementation.
- \`storage/event_store.py\` owns SQLite transactions, snapshots, and replay, but no story rules.
- \`cli.py\` exposes validation and inspection without importing FastAPI.

---

### Task 1: Create The V2 Story Package Boundary

**Files:**
- Create: \`backend/src/story/__init__.py\`
- Create: \`backend/src/story/script_pack/__init__.py\`
- Create: \`backend/src/story/state/__init__.py\`
- Create: \`backend/src/story/storage/__init__.py\`
- Create: \`backend/tests/test_story_package.py\`

- [ ] **Step 1: Write the failing package import test**

~~~python
# backend/tests/test_story_package.py

from src.story import SCRIPT_PACK_SCHEMA_VERSION


def test_story_package_exposes_schema_version():
    assert SCRIPT_PACK_SCHEMA_VERSION == "1.0"
~~~

- [ ] **Step 2: Run the test and confirm the package does not exist**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_package.py -v
~~~

Expected: FAIL with \`ModuleNotFoundError: No module named 'src.story'\`.

- [ ] **Step 3: Add the package boundary**

~~~python
# backend/src/story/__init__.py

"""V2 constrained dynamic narrative domain."""

SCRIPT_PACK_SCHEMA_VERSION = "1.0"

__all__ = ["SCRIPT_PACK_SCHEMA_VERSION"]
~~~

~~~python
# backend/src/story/script_pack/__init__.py

"""Script pack source models and compiler."""
~~~

~~~python
# backend/src/story/state/__init__.py

"""Event-sourced story session state."""
~~~

~~~python
# backend/src/story/storage/__init__.py

"""Persistence adapters for story sessions."""
~~~

- [ ] **Step 4: Run the focused test**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_package.py -v
~~~

Expected: PASS, 1 test passed.

- [ ] **Step 5: Commit**

~~~bash
git add backend/src/story backend/tests/test_story_package.py
git commit -m "feat: add v2 story package boundary"
~~~

---

### Task 2: Implement The Safe Condition DSL

**Files:**
- Create: \`backend/src/story/conditions.py\`
- Create: \`backend/tests/test_condition_dsl.py\`

The DSL accepts boolean operators, comparisons, literal lists/tuples, and dotted state paths. It rejects calls, indexing, arithmetic, comprehensions, lambdas, and every other Python AST node.

- [ ] **Step 1: Write failing DSL tests**

~~~python
# backend/tests/test_condition_dsl.py

import pytest
from src.story.conditions import (
    ConditionEvaluationError,
    ConditionSyntaxError,
    compile_condition,
)


def test_compiles_paths_and_evaluates_boolean_expression():
    program = compile_condition(
        "goals.alice_find_ally.completed "
        "and relationships.alice.trust >= 70 "
        "and facts.notebook.truth_status == 'committed'"
    )

    assert program.paths == (
        "facts.notebook.truth_status",
        "goals.alice_find_ally.completed",
        "relationships.alice.trust",
    )
    assert program.evaluate(
        {
            "goals": {"alice_find_ally": {"completed": True}},
            "relationships": {"alice": {"trust": 72}},
            "facts": {"notebook": {"truth_status": "committed"}},
        }
    )


def test_supports_not_in_and_lowercase_literals():
    program = compile_condition(
        "not session.ended and world.location in ['cafe', 'street'] and true"
    )
    assert program.evaluate(
        {
            "session": {"ended": False},
            "world": {"location": "cafe"},
        }
    )


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "facts['secret']",
        "relationships.alice.trust + 5 > 70",
        "[x for x in facts]",
        "(lambda: true)()",
    ],
)
def test_rejects_executable_or_unsupported_syntax(expression):
    with pytest.raises(ConditionSyntaxError):
        compile_condition(expression)


def test_missing_runtime_path_is_an_explicit_error():
    program = compile_condition("relationships.alice.trust >= 70")
    with pytest.raises(ConditionEvaluationError, match="relationships.alice.trust"):
        program.evaluate({"relationships": {}})
~~~

- [ ] **Step 2: Run the tests and confirm the module is missing**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_condition_dsl.py -v
~~~

Expected: FAIL during collection because \`src.story.conditions\` does not exist.

- [ ] **Step 3: Implement the parser and evaluator without eval**

~~~python
# backend/src/story/conditions.py

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConditionSyntaxError(ValueError):
    """The author supplied an unsupported condition expression."""


class ConditionEvaluationError(ValueError):
    """A compiled condition could not be evaluated against runtime state."""


class ConditionProgram(BaseModel):
    model_config = ConfigDict(frozen=True)

    expression: str
    paths: tuple[str, ...]

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        tree = _parse(self.expression)
        try:
            return bool(_evaluate(tree.body, context))
        except ConditionEvaluationError:
            raise
        except Exception as exc:
            raise ConditionEvaluationError(str(exc)) from exc


_LITERALS: dict[str, Any] = {
    "true": True,
    "false": False,
    "null": None,
    "True": True,
    "False": False,
    "None": None,
}

_COMPARE_OPS = (
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)


def _parse(expression: str) -> ast.Expression:
    if not expression or not expression.strip():
        raise ConditionSyntaxError("condition must not be empty")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ConditionSyntaxError(str(exc)) from exc
    if not isinstance(tree, ast.Expression):
        raise ConditionSyntaxError("condition must be an expression")
    return tree


def _path(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name) and current.id not in _LITERALS:
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _validate(node: ast.AST, paths: set[str]) -> None:
    if isinstance(node, ast.Expression):
        _validate(node.body, paths)
        return
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _validate(value, paths)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _validate(node.operand, paths)
        return
    if isinstance(node, ast.Compare) and all(
        isinstance(op, _COMPARE_OPS) for op in node.ops
    ):
        _validate(node.left, paths)
        for comparator in node.comparators:
            _validate(comparator, paths)
        return
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (str, int, float, bool, type(None))
    ):
        return
    if isinstance(node, (ast.List, ast.Tuple)):
        for item in node.elts:
            _validate(item, paths)
        return
    if isinstance(node, ast.Name) and node.id in _LITERALS:
        return
    dotted = _path(node)
    if dotted is not None:
        paths.add(dotted)
        return
    raise ConditionSyntaxError(
        f"unsupported condition syntax: {ast.dump(node, include_attributes=False)}"
    )


def _resolve(context: Mapping[str, Any], dotted: str) -> Any:
    value: Any = context
    consumed: list[str] = []
    for part in dotted.split("."):
        consumed.append(part)
        if isinstance(value, Mapping) and part in value:
            value = value[part]
            continue
        raise ConditionEvaluationError(
            f"condition path not found: {'.'.join(consumed)} "
            f"(full path: {dotted})"
        )
    return value


def _compare(operator: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(operator, ast.Eq):
        return left == right
    if isinstance(operator, ast.NotEq):
        return left != right
    if isinstance(operator, ast.Lt):
        return left < right
    if isinstance(operator, ast.LtE):
        return left <= right
    if isinstance(operator, ast.Gt):
        return left > right
    if isinstance(operator, ast.GtE):
        return left >= right
    if isinstance(operator, ast.In):
        return left in right
    if isinstance(operator, ast.NotIn):
        return left not in right
    raise ConditionEvaluationError(f"unsupported comparison: {type(operator).__name__}")


def _evaluate(node: ast.AST, context: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in _LITERALS:
        return _LITERALS[node.id]
    if isinstance(node, (ast.Name, ast.Attribute)):
        dotted = _path(node)
        if dotted is None:
            raise ConditionEvaluationError("invalid condition path")
        return _resolve(context, dotted)
    if isinstance(node, ast.List):
        return [_evaluate(item, context) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_evaluate(item, context) for item in node.elts)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(bool(_evaluate(item, context)) for item in node.values)
        return any(bool(_evaluate(item, context)) for item in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not bool(_evaluate(node.operand, context))
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, context)
        for operator, comparator in zip(node.ops, node.comparators):
            right = _evaluate(comparator, context)
            if not _compare(operator, left, right):
                return False
            left = right
        return True
    raise ConditionEvaluationError(f"unsupported node: {type(node).__name__}")


def compile_condition(expression: str) -> ConditionProgram:
    tree = _parse(expression)
    paths: set[str] = set()
    _validate(tree, paths)
    return ConditionProgram(expression=expression.strip(), paths=tuple(sorted(paths)))
~~~

- [ ] **Step 4: Run the DSL tests**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_condition_dsl.py -v
~~~

Expected: PASS, 8 tests passed.

- [ ] **Step 5: Run Ruff on the new module**

Run:

~~~bash
cd backend
uv run --extra dev ruff check src/story/conditions.py tests/test_condition_dsl.py
~~~

Expected: exit 0 with no diagnostics.

- [ ] **Step 6: Commit**

~~~bash
git add backend/src/story/conditions.py backend/tests/test_condition_dsl.py
git commit -m "feat: add safe story condition DSL"
~~~

---

### Task 3: Define Author-Facing Script Pack Models

**Files:**
- Create: \`backend/src/story/script_pack/models.py\`
- Create: \`backend/tests/story_factories.py\`
- Create: \`backend/tests/test_script_pack_models.py\`
- Modify: \`backend/src/story/script_pack/__init__.py\`

- [ ] **Step 1: Add a reusable valid source fixture and failing model tests**

~~~python
# backend/tests/story_factories.py

from __future__ import annotations

from typing import Any


def minimal_script_pack_dict() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "identity": {
            "id": "test_pack",
            "title": "Test Pack",
            "language": "en",
            "genres": ["mystery"],
            "expected_minutes": 60,
        },
        "experience": {
            "viewpoint": "first_person",
            "prose_style": "concise",
            "tone": "quiet mystery",
            "choice_density": "key_moments",
            "min_scenes": 8,
            "max_scenes": 20,
        },
        "protagonist": {
            "id": "protagonist",
            "name": "Ren",
            "personality": {
                "traits": ["observant"],
                "values": ["honesty"],
                "flaws": ["hesitant"],
            },
            "background": "A new student",
            "capabilities": ["ask", "observe"],
            "boundaries": {"cannot": ["use violence"]},
        },
        "world": {
            "premise": "A notebook disappeared.",
            "immutable_rules": ["Dead characters cannot return."],
            "locations": [{"id": "cafe", "name": "Cafe", "tags": ["public"]}],
            "factions": [],
            "initial_situation": {
                "location": "cafe",
                "present_characters": ["alice"],
                "known_facts": ["cafe_is_open"],
            },
        },
        "characters": [
            {
                "id": "alice",
                "name": "Alice",
                "public_profile": "An outgoing student.",
                "personality": {
                    "traits": ["outgoing"],
                    "values": ["friendship"],
                    "fears": ["abandonment"],
                    "flaws": ["impulsive"],
                },
                "voice": {
                    "style": "direct",
                    "forbidden": ["formal speeches"],
                },
                "drives": ["find an ally"],
                "knowledge": ["cafe_is_open"],
                "secrets": ["who_took_notebook"],
                "capabilities": ["ask", "support"],
                "initial_relationship": {"trust": 35, "affection": 5},
            }
        ],
        "facts": {
            "fixed": [
                {
                    "id": "cafe_is_open",
                    "statement": "The cafe is open.",
                    "known_by": ["alice"],
                    "visibility": "revealed",
                }
            ],
            "latent_questions": [
                {
                    "id": "who_took_notebook",
                    "question": "Who took the notebook?",
                    "selection": "lazy_commit",
                    "candidates": [
                        {"value": "alice", "weight": 1.0, "requirements": []},
                        {"value": "stranger", "weight": 1.0, "requirements": []},
                    ],
                    "commit_when": [
                        "first_irreversible_evidence",
                        "explicit_revelation",
                    ],
                    "evidence_required": 1,
                }
            ],
            "derived": [
                {
                    "id": "alice_trusts_player",
                    "condition": "relationships.alice.trust >= 70",
                }
            ],
        },
        "goals": [
            {
                "id": "alice_find_ally",
                "owner": "alice",
                "desire": "Find an ally.",
                "urgency": 0.7,
                "conflicts_with": [],
                "success_condition": "relationships.alice.trust >= 70",
                "failure_condition": "relationships.alice.trust <= 10",
            }
        ],
        "interaction_rules": {
            "enabled_standard": ["ask", "observe", "support", "challenge"],
            "disabled": [],
            "extensions": [],
        },
        "endings": [
            {
                "id": "ally_ending",
                "title": "Together",
                "type": "hopeful",
                "priority": 80,
                "eligibility": {
                    "all": ["relationships.alice.trust >= 70"],
                    "any": [],
                    "none": [],
                },
                "required_outcomes": ["Alice and the protagonist cooperate."],
                "forbidden_outcomes": ["Alice becomes the mastermind."],
                "closing_tone": "hopeful",
            },
            {
                "id": "truth_ending",
                "title": "Truth",
                "type": "neutral",
                "priority": 70,
                "eligibility": {
                    "all": ["facts.who_took_notebook.truth_status == 'committed'"],
                    "any": [],
                    "none": [],
                },
                "required_outcomes": ["Explain the notebook truth."],
                "forbidden_outcomes": [],
                "closing_tone": "reflective",
            },
            {
                "id": "distance_ending",
                "title": "Distance",
                "type": "bittersweet",
                "priority": 60,
                "eligibility": {
                    "all": ["relationships.alice.trust <= 20"],
                    "any": [],
                    "none": [],
                },
                "required_outcomes": ["Alice and the protagonist part."],
                "forbidden_outcomes": [],
                "closing_tone": "bittersweet",
            },
            {
                "id": "fallback_ending",
                "title": "Closing Time",
                "type": "fallback",
                "priority": 1,
                "eligibility": {
                    "all": ["session.scene_count >= 17"],
                    "any": [],
                    "none": [],
                },
                "required_outcomes": ["Close the current conflict."],
                "forbidden_outcomes": [],
                "closing_tone": "quiet",
            },
        ],
        "assets": {},
    }
~~~

~~~python
# backend/tests/test_script_pack_models.py

import pytest
from pydantic import ValidationError
from src.story.script_pack.models import ScriptPackSource
from tests.story_factories import minimal_script_pack_dict


def test_valid_script_pack_source_is_frozen_and_typed():
    source = ScriptPackSource.model_validate(minimal_script_pack_dict())

    assert source.identity.id == "test_pack"
    assert source.experience.reserved_resolution_scenes == 3
    assert source.characters[0].initial_relationship["trust"] == 35
    assert source.facts.latent_questions[0].evidence_required == 1

    with pytest.raises(ValidationError):
        source.identity.title = "Changed"


def test_unknown_author_field_is_rejected():
    raw = minimal_script_pack_dict()
    raw["identity"]["unexpected"] = "typo"

    with pytest.raises(ValidationError, match="unexpected"):
        ScriptPackSource.model_validate(raw)


def test_invalid_id_is_rejected():
    raw = minimal_script_pack_dict()
    raw["characters"][0]["id"] = "Alice Has Spaces"

    with pytest.raises(ValidationError):
        ScriptPackSource.model_validate(raw)


def test_action_effect_bounds_stay_within_kernel_limits():
    raw = minimal_script_pack_dict()
    raw["interaction_rules"]["extensions"] = [
        {
            "id": "reassure",
            "effects": {
                "relationship_axes": {"trust": [-101, 20]},
            },
        }
    ]

    with pytest.raises(ValidationError, match="must stay within -100..100"):
        ScriptPackSource.model_validate(raw)
~~~

- [ ] **Step 2: Run the tests and confirm the model module is missing**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_script_pack_models.py -v
~~~

Expected: FAIL during collection because \`src.story.script_pack.models\` does not exist.

- [ ] **Step 3: Implement strict frozen source models**

~~~python
# backend/src/story/script_pack/models.py

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from src.story.conditions import ConditionProgram

SafeId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$"),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IdentitySource(StrictModel):
    id: SafeId
    title: str = Field(min_length=1, max_length=120)
    language: str = Field(min_length=2, max_length=20)
    genres: tuple[str, ...] = ()
    expected_minutes: int = Field(ge=15, le=360)


class ExperienceSource(StrictModel):
    viewpoint: Literal["first_person", "third_person_limited"]
    prose_style: str = Field(min_length=1, max_length=200)
    tone: str = Field(min_length=1, max_length=200)
    choice_density: Literal["key_moments"] = "key_moments"
    min_scenes: int = Field(ge=4, le=200)
    max_scenes: int = Field(ge=8, le=240)
    reserved_resolution_scenes: int = Field(default=3, ge=1, le=8)
    forbidden_content: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_scene_budget(self) -> ExperienceSource:
        if self.min_scenes >= self.max_scenes:
            raise ValueError("min_scenes must be smaller than max_scenes")
        if self.min_scenes + self.reserved_resolution_scenes > self.max_scenes:
            raise ValueError("scene budget does not leave room for resolution")
        return self


class PersonalitySource(StrictModel):
    traits: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    fears: tuple[str, ...] = ()
    flaws: tuple[str, ...] = ()


class BoundariesSource(StrictModel):
    cannot: tuple[str, ...] = ()


class ProtagonistSource(StrictModel):
    id: SafeId
    name: str = Field(min_length=1, max_length=80)
    personality: PersonalitySource
    background: str = Field(min_length=1)
    capabilities: tuple[SafeId, ...]
    boundaries: BoundariesSource = Field(default_factory=BoundariesSource)


class LocationSource(StrictModel):
    id: SafeId
    name: str = Field(min_length=1, max_length=100)
    tags: tuple[SafeId, ...] = ()


class FactionSource(StrictModel):
    id: SafeId
    name: str = Field(min_length=1, max_length=100)
    description: str = ""


class InitialSituationSource(StrictModel):
    location: SafeId
    present_characters: tuple[SafeId, ...] = ()
    known_facts: tuple[SafeId, ...] = ()
    time_label: str = "opening"


class WorldSource(StrictModel):
    premise: str = Field(min_length=1)
    immutable_rules: tuple[str, ...] = ()
    locations: tuple[LocationSource, ...]
    factions: tuple[FactionSource, ...] = ()
    initial_situation: InitialSituationSource


class VoiceSource(StrictModel):
    style: str = Field(min_length=1)
    forbidden: tuple[str, ...] = ()


class CharacterSource(StrictModel):
    id: SafeId
    name: str = Field(min_length=1, max_length=80)
    public_profile: str = Field(min_length=1)
    personality: PersonalitySource
    voice: VoiceSource
    drives: tuple[str, ...]
    knowledge: tuple[SafeId, ...] = ()
    secrets: tuple[SafeId, ...] = ()
    beliefs: dict[SafeId, Any] = Field(default_factory=dict)
    capabilities: tuple[SafeId, ...] = ()
    initial_relationship: dict[SafeId, int] = Field(default_factory=dict)
    boundaries: BoundariesSource = Field(default_factory=BoundariesSource)


class FixedFactSource(StrictModel):
    id: SafeId
    statement: str = Field(min_length=1)
    known_by: tuple[SafeId, ...] = ()
    visibility: Literal["hidden", "revealed"] = "hidden"


class LatentCandidateSource(StrictModel):
    value: str = Field(min_length=1, max_length=120)
    weight: float = Field(default=1.0, gt=0)
    requirements: tuple[str, ...] = ()


class LatentQuestionSource(StrictModel):
    id: SafeId
    question: str = Field(min_length=1)
    selection: Literal["lazy_commit"] = "lazy_commit"
    candidates: tuple[LatentCandidateSource, ...] = Field(min_length=2)
    commit_when: tuple[
        Literal["first_irreversible_evidence", "explicit_revelation"],
        ...,
    ]
    evidence_required: int = Field(default=1, ge=1, le=10)


class DerivedFactSource(StrictModel):
    id: SafeId
    condition: str = Field(min_length=1)


class FactsSource(StrictModel):
    fixed: tuple[FixedFactSource, ...] = ()
    latent_questions: tuple[LatentQuestionSource, ...] = ()
    derived: tuple[DerivedFactSource, ...] = ()


class GoalSource(StrictModel):
    id: SafeId
    owner: SafeId
    desire: str = Field(min_length=1)
    urgency: float = Field(ge=0, le=1)
    conflicts_with: tuple[SafeId, ...] = ()
    success_condition: str = Field(min_length=1)
    failure_condition: str = Field(min_length=1)


class EffectBoundsSource(StrictModel):
    relationship_axes: dict[SafeId, tuple[int, int]] = Field(default_factory=dict)
    goal_progress: tuple[float, float] = (-0.15, 0.25)
    can_commit_facts: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> EffectBoundsSource:
        for axis, bounds in self.relationship_axes.items():
            if bounds[0] > bounds[1]:
                raise ValueError(f"invalid bounds for relationship axis {axis}")
            if bounds[0] < -100 or bounds[1] > 100:
                raise ValueError(
                    f"relationship bounds for {axis} must stay within -100..100"
                )
        if self.goal_progress[0] > self.goal_progress[1]:
            raise ValueError("invalid goal_progress bounds")
        if self.goal_progress[0] < -1 or self.goal_progress[1] > 1:
            raise ValueError("goal_progress bounds must stay within -1..1")
        return self


class ActionExtensionSource(StrictModel):
    id: SafeId
    preconditions: tuple[str, ...] = ()
    effects: EffectBoundsSource = Field(default_factory=EffectBoundsSource)
    risk_tags: tuple[SafeId, ...] = ()


class InteractionRulesSource(StrictModel):
    enabled_standard: tuple[SafeId, ...]
    disabled: tuple[SafeId, ...] = ()
    extensions: tuple[ActionExtensionSource, ...] = ()


class ConditionGroupSource(StrictModel):
    all: tuple[str, ...] = ()
    any: tuple[str, ...] = ()
    none: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_condition(self) -> ConditionGroupSource:
        if not self.all and not self.any and not self.none:
            raise ValueError("ending eligibility must contain a condition")
        return self


class EndingSource(StrictModel):
    id: SafeId
    title: str = Field(min_length=1, max_length=120)
    type: SafeId
    priority: int = Field(ge=0, le=1000)
    eligibility: ConditionGroupSource
    required_outcomes: tuple[str, ...] = Field(min_length=1)
    forbidden_outcomes: tuple[str, ...] = ()
    closing_tone: str = Field(min_length=1)


class ScriptPackSource(StrictModel):
    schema_version: Literal["1.0"]
    identity: IdentitySource
    experience: ExperienceSource
    protagonist: ProtagonistSource
    world: WorldSource
    characters: tuple[CharacterSource, ...] = Field(min_length=1)
    facts: FactsSource
    goals: tuple[GoalSource, ...] = Field(min_length=1)
    interaction_rules: InteractionRulesSource
    endings: tuple[EndingSource, ...] = Field(min_length=4)
    assets: dict[str, Any] = Field(default_factory=dict)


class CompiledScriptPack(StrictModel):
    source: ScriptPackSource
    pack_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    conditions: dict[str, ConditionProgram]
    character_ids: frozenset[str]
    fact_ids: frozenset[str]
    goal_ids: frozenset[str]
    ending_ids: frozenset[str]
    action_ids: frozenset[str]
~~~

~~~python
# backend/src/story/script_pack/__init__.py

"""Script pack source models and compiler."""

from .models import CompiledScriptPack, ScriptPackSource

__all__ = ["CompiledScriptPack", "ScriptPackSource"]
~~~

- [ ] **Step 4: Run the model tests**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_script_pack_models.py -v
~~~

Expected: PASS, 4 tests passed.

- [ ] **Step 5: Run all V2 tests created so far**

Run:

~~~bash
cd backend
uv run --extra dev pytest \
  tests/test_story_package.py \
  tests/test_condition_dsl.py \
  tests/test_script_pack_models.py -v
~~~

Expected: PASS, 13 tests passed.

- [ ] **Step 6: Commit**

~~~bash
git add \
  backend/src/story/script_pack \
  backend/tests/story_factories.py \
  backend/tests/test_script_pack_models.py
git commit -m "feat: define v2 script pack source models"
~~~

---

### Task 4: Compile And Validate Script Packs

**Files:**
- Create: \`backend/src/story/script_pack/compiler.py\`
- Create: \`backend/tests/test_script_pack_compiler.py\`
- Modify: \`backend/src/story/script_pack/__init__.py\`

- [ ] **Step 1: Write failing compiler tests**

~~~python
# backend/tests/test_script_pack_compiler.py

from pathlib import Path

import pytest
import yaml
from src.story.script_pack.compiler import (
    PackCompileError,
    compile_script_pack,
    compile_source,
    load_script_pack_source,
)
from tests.story_factories import minimal_script_pack_dict


def test_compile_source_collects_conditions_and_stable_hash():
    raw = minimal_script_pack_dict()

    first = compile_source(raw)
    second = compile_source(raw)

    assert first.pack_hash == second.pack_hash
    assert len(first.pack_hash) == 64
    assert "ending.ally_ending.all.0" in first.conditions
    assert first.conditions["goal.alice_find_ally.success"].paths == (
        "relationships.alice.trust",
    )
    assert first.character_ids == frozenset({"alice"})
    assert first.action_ids >= {"ask", "observe", "support", "challenge"}


def test_compile_rejects_duplicate_fact_ids():
    raw = minimal_script_pack_dict()
    raw["facts"]["fixed"].append(dict(raw["facts"]["fixed"][0]))

    with pytest.raises(PackCompileError, match="duplicate fact id"):
        compile_source(raw)


def test_compile_rejects_unknown_character_reference():
    raw = minimal_script_pack_dict()
    raw["facts"]["fixed"][0]["known_by"] = ["missing_character"]

    with pytest.raises(PackCompileError, match="missing_character"):
        compile_source(raw)


def test_compile_rejects_unknown_fact_in_condition():
    raw = minimal_script_pack_dict()
    raw["endings"][0]["eligibility"]["all"] = [
        "facts.missing_fact.truth_status == 'committed'"
    ]

    with pytest.raises(PackCompileError, match="missing_fact"):
        compile_source(raw)


def test_compile_rejects_duplicate_location_ids():
    raw = minimal_script_pack_dict()
    raw["world"]["locations"].append(dict(raw["world"]["locations"][0]))

    with pytest.raises(PackCompileError, match="duplicate location id"):
        compile_source(raw)


def test_compile_rejects_nonfixed_opening_fact():
    raw = minimal_script_pack_dict()
    raw["world"]["initial_situation"]["known_facts"] = [
        "who_took_notebook"
    ]

    with pytest.raises(PackCompileError, match="opening known fact must be fixed"):
        compile_source(raw)


def test_compile_rejects_character_knowledge_of_uncommitted_fact():
    raw = minimal_script_pack_dict()
    raw["characters"][0]["knowledge"] = ["who_took_notebook"]

    with pytest.raises(PackCompileError, match="opening knowledge must be fixed"):
        compile_source(raw)


def test_compile_rejects_unknown_disabled_action():
    raw = minimal_script_pack_dict()
    raw["interaction_rules"]["disabled"] = ["teleport"]

    with pytest.raises(PackCompileError, match="unknown disabled action"):
        compile_source(raw)


def test_compile_requires_fallback_reachable_by_max_scene_count():
    raw = minimal_script_pack_dict()
    raw["endings"][-1]["eligibility"]["all"] = [
        "session.scene_count >= 99"
    ]

    with pytest.raises(PackCompileError, match="guaranteed fallback"):
        compile_source(raw)


def test_modular_pack_loader_resolves_safe_includes(tmp_path: Path):
    raw = minimal_script_pack_dict()
    characters = raw.pop("characters")
    raw["includes"] = {"characters": "characters.yaml"}
    (tmp_path / "pack.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / "characters.yaml").write_text(
        yaml.safe_dump(characters, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    source = load_script_pack_source(tmp_path)

    assert source.characters[0].id == "alice"
    assert compile_script_pack(tmp_path).source.identity.id == "test_pack"


def test_modular_pack_loader_rejects_parent_traversal(tmp_path: Path):
    raw = minimal_script_pack_dict()
    raw.pop("characters")
    raw["includes"] = {"characters": "../characters.yaml"}
    (tmp_path / "pack.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(PackCompileError, match="inside the pack directory"):
        load_script_pack_source(tmp_path)
~~~

- [ ] **Step 2: Run the tests and confirm the compiler is missing**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_script_pack_compiler.py -v
~~~

Expected: FAIL during collection because \`src.story.script_pack.compiler\` does not exist.

- [ ] **Step 3: Implement loading, validation, condition compilation, and hashing**

~~~python
# backend/src/story/script_pack/compiler.py

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from src.story.conditions import (
    ConditionEvaluationError,
    ConditionProgram,
    ConditionSyntaxError,
    compile_condition,
)
from src.story.script_pack.models import CompiledScriptPack, ScriptPackSource

STANDARD_ACTION_IDS = frozenset(
    {
        "ask",
        "observe",
        "support",
        "challenge",
        "withhold",
        "disclose",
        "follow",
        "leave",
    }
)

_INCLUDE_KEYS = frozenset(
    {
        "protagonist",
        "world",
        "characters",
        "facts",
        "goals",
        "interaction_rules",
        "endings",
        "assets",
    }
)

_CONDITION_ROOTS = frozenset(
    {
        "facts",
        "relationships",
        "goals",
        "world",
        "session",
        "threads",
    }
)


class PackCompileError(ValueError):
    def __init__(self, errors: str | Iterable[str]) -> None:
        if isinstance(errors, str):
            self.errors = (errors,)
        else:
            self.errors = tuple(errors)
        super().__init__("\n".join(self.errors))


def _yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackCompileError(f"script pack file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PackCompileError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackCompileError(f"expected a YAML mapping in {path}")
    return data


def _included_value(path: Path) -> Any:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackCompileError(f"included file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PackCompileError(f"invalid YAML in {path}: {exc}") from exc
    return value


def load_script_pack_source(pack_path: Path | str) -> ScriptPackSource:
    supplied = Path(pack_path)
    manifest = supplied if supplied.is_file() else supplied / "pack.yaml"
    root = manifest.parent.resolve()
    raw = _yaml_mapping(manifest)
    includes = raw.pop("includes", {})

    if not isinstance(includes, dict):
        raise PackCompileError("includes must be a mapping of field to relative file")

    for key, relative in includes.items():
        if key not in _INCLUDE_KEYS:
            raise PackCompileError(f"unsupported include field: {key}")
        if key in raw:
            raise PackCompileError(f"field {key} cannot be inline and included")
        if not isinstance(relative, str) or not relative:
            raise PackCompileError(f"include path for {key} must be a string")

        resolved = (root / relative).resolve()
        if not resolved.is_relative_to(root):
            raise PackCompileError(
                f"include for {key} must stay inside the pack directory"
            )
        raw[key] = _included_value(resolved)

    try:
        return ScriptPackSource.model_validate(raw)
    except ValidationError as exc:
        raise PackCompileError(str(exc)) from exc


def _duplicate_ids(label: str, values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return [
        f"duplicate {label} id: {value}"
        for value, count in sorted(counts.items())
        if count > 1
    ]


def _condition_entries(source: ScriptPackSource) -> Iterable[tuple[str, str]]:
    for fact in source.facts.derived:
        yield f"fact.{fact.id}.derived", fact.condition
    for question in source.facts.latent_questions:
        for candidate in question.candidates:
            for index, expression in enumerate(candidate.requirements):
                yield (
                    f"fact.{question.id}.candidate.{candidate.value}.requirement.{index}",
                    expression,
                )
    for goal in source.goals:
        yield f"goal.{goal.id}.success", goal.success_condition
        yield f"goal.{goal.id}.failure", goal.failure_condition
    for action in source.interaction_rules.extensions:
        for index, expression in enumerate(action.preconditions):
            yield f"action.{action.id}.precondition.{index}", expression
    for ending in source.endings:
        for group_name in ("all", "any", "none"):
            for index, expression in enumerate(
                getattr(ending.eligibility, group_name)
            ):
                yield f"ending.{ending.id}.{group_name}.{index}", expression


def _condition_reference_errors(
    programs: Mapping[str, ConditionProgram],
    character_ids: set[str],
    fact_ids: set[str],
    goal_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    for condition_key, program in programs.items():
        for dotted in program.paths:
            parts = dotted.split(".")
            root = parts[0]
            if root not in _CONDITION_ROOTS:
                errors.append(
                    f"{condition_key}: unsupported condition root {root}"
                )
                continue
            if root == "facts" and (len(parts) < 2 or parts[1] not in fact_ids):
                errors.append(
                    f"{condition_key}: unknown fact in path {dotted}"
                )
            if root == "goals" and (len(parts) < 2 or parts[1] not in goal_ids):
                errors.append(
                    f"{condition_key}: unknown goal in path {dotted}"
                )
            if root == "relationships" and (
                len(parts) < 2 or parts[1] not in character_ids
            ):
                errors.append(
                    f"{condition_key}: unknown character in path {dotted}"
                )
    return errors


def _compile_programs(
    source: ScriptPackSource,
) -> tuple[dict[str, ConditionProgram], list[str]]:
    programs: dict[str, ConditionProgram] = {}
    errors: list[str] = []
    for key, expression in _condition_entries(source):
        try:
            programs[key] = compile_condition(expression)
        except ConditionSyntaxError as exc:
            errors.append(f"{key}: {exc}")
    return programs, errors


def _reference_errors(
    source: ScriptPackSource,
    character_ids: set[str],
    fixed_ids: set[str],
    fact_ids: set[str],
    goal_ids: set[str],
    action_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    location_ids = {item.id for item in source.world.locations}
    fixed_known_by = {
        item.id: set(item.known_by) for item in source.facts.fixed
    }

    if source.world.initial_situation.location not in location_ids:
        errors.append(
            "initial_situation references unknown location "
            f"{source.world.initial_situation.location}"
        )
    for character_id in source.world.initial_situation.present_characters:
        if character_id not in character_ids:
            errors.append(
                f"initial_situation references unknown character {character_id}"
            )
    for fact_id in source.world.initial_situation.known_facts:
        if fact_id not in fact_ids:
            errors.append(f"initial_situation references unknown fact {fact_id}")
        elif fact_id not in fixed_ids:
            errors.append(
                f"opening known fact must be fixed: {fact_id}"
            )

    for fact in source.facts.fixed:
        for character_id in fact.known_by:
            if character_id not in character_ids:
                errors.append(
                    f"fact {fact.id} known_by references {character_id}"
                )

    for character in source.characters:
        for fact_id in character.knowledge:
            if fact_id not in fact_ids:
                errors.append(
                    f"character {character.id} references unknown fact {fact_id}"
                )
            elif fact_id not in fixed_ids:
                errors.append(
                    "opening knowledge must be fixed: "
                    f"{character.id} -> {fact_id}"
                )
            elif character.id not in fixed_known_by[fact_id]:
                errors.append(
                    "opening knowledge is not granted by fact known_by: "
                    f"{character.id} -> {fact_id}"
                )
        for fact_id in character.secrets:
            if fact_id not in fact_ids:
                errors.append(
                    f"character {character.id} references unknown fact {fact_id}"
                )
        for action_id in character.capabilities:
            if action_id not in action_ids:
                errors.append(
                    f"character {character.id} has unknown action {action_id}"
                )

    for action_id in source.protagonist.capabilities:
        if action_id not in action_ids:
            errors.append(f"protagonist has unknown action {action_id}")

    owners = character_ids | {source.protagonist.id}
    for goal in source.goals:
        if goal.owner not in owners:
            errors.append(f"goal {goal.id} has unknown owner {goal.owner}")
        for conflict in goal.conflicts_with:
            if conflict not in goal_ids:
                errors.append(
                    f"goal {goal.id} conflicts with unknown goal {conflict}"
                )

    for question in source.facts.latent_questions:
        errors.extend(
            _duplicate_ids(
                f"candidate value for {question.id}",
                (candidate.value for candidate in question.candidates),
            )
        )
    return errors


def _has_guaranteed_fallback(
    source: ScriptPackSource,
    programs: Mapping[str, ConditionProgram],
) -> bool:
    context = {"session": {"scene_count": source.experience.max_scenes}}
    for ending in source.endings:
        if ending.type != "fallback":
            continue
        if ending.eligibility.any or ending.eligibility.none:
            continue
        keys = [
            f"ending.{ending.id}.all.{index}"
            for index in range(len(ending.eligibility.all))
        ]
        try:
            if all(
                set(programs[key].paths) <= {"session.scene_count"}
                and programs[key].evaluate(context)
                for key in keys
            ):
                return True
        except ConditionEvaluationError:
            continue
    return False


def compile_source(raw: Mapping[str, Any] | ScriptPackSource) -> CompiledScriptPack:
    try:
        source = (
            raw
            if isinstance(raw, ScriptPackSource)
            else ScriptPackSource.model_validate(raw)
        )
    except ValidationError as exc:
        raise PackCompileError(str(exc)) from exc

    character_ids = {item.id for item in source.characters}
    fixed_ids = {item.id for item in source.facts.fixed}
    latent_ids = {item.id for item in source.facts.latent_questions}
    derived_ids = {item.id for item in source.facts.derived}
    fact_ids = fixed_ids | latent_ids | derived_ids
    goal_ids = {item.id for item in source.goals}
    ending_ids = {item.id for item in source.endings}
    fact_id_values = [
        *(item.id for item in source.facts.fixed),
        *(item.id for item in source.facts.latent_questions),
        *(item.id for item in source.facts.derived),
    ]
    extension_id_values = [
        item.id for item in source.interaction_rules.extensions
    ]
    extension_ids = set(extension_id_values)
    action_ids = (
        set(source.interaction_rules.enabled_standard)
        | extension_ids
    ) - set(source.interaction_rules.disabled)

    errors: list[str] = []
    errors.extend(_duplicate_ids("character", (x.id for x in source.characters)))
    errors.extend(_duplicate_ids("fact", fact_id_values))
    errors.extend(_duplicate_ids("goal", (x.id for x in source.goals)))
    errors.extend(_duplicate_ids("ending", (x.id for x in source.endings)))
    errors.extend(_duplicate_ids("location", (x.id for x in source.world.locations)))
    errors.extend(_duplicate_ids("faction", (x.id for x in source.world.factions)))
    errors.extend(_duplicate_ids("action", extension_id_values))
    if source.protagonist.id in character_ids:
        errors.append(
            f"protagonist id collides with character id: {source.protagonist.id}"
        )
    errors.extend(
        f"action extension cannot replace standard action: {item}"
        for item in sorted(extension_ids & STANDARD_ACTION_IDS)
    )

    unknown_standard = (
        set(source.interaction_rules.enabled_standard) - STANDARD_ACTION_IDS
    )
    errors.extend(
        f"unknown standard action: {item}" for item in sorted(unknown_standard)
    )
    unknown_disabled = set(source.interaction_rules.disabled) - (
        STANDARD_ACTION_IDS | extension_ids
    )
    errors.extend(
        f"unknown disabled action: {item}" for item in sorted(unknown_disabled)
    )

    normal_endings = [item for item in source.endings if item.type != "fallback"]
    fallback_endings = [item for item in source.endings if item.type == "fallback"]
    if len(normal_endings) < 3:
        errors.append("script pack requires at least 3 normal endings")
    if not fallback_endings:
        errors.append("script pack requires at least 1 fallback ending")

    errors.extend(
        _reference_errors(
            source,
            character_ids,
            fixed_ids,
            fact_ids,
            goal_ids,
            action_ids,
        )
    )

    programs, condition_errors = _compile_programs(source)
    errors.extend(condition_errors)
    errors.extend(
        _condition_reference_errors(
            programs,
            character_ids,
            fact_ids,
            goal_ids,
        )
    )
    if not condition_errors and not _has_guaranteed_fallback(source, programs):
        errors.append(
            "script pack requires a guaranteed fallback that is true at "
            "max_scenes and depends only on session.scene_count"
        )

    if errors:
        raise PackCompileError(errors)

    canonical = json.dumps(
        source.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return CompiledScriptPack(
        source=source,
        pack_hash=hashlib.sha256(canonical).hexdigest(),
        conditions=programs,
        character_ids=frozenset(character_ids),
        fact_ids=frozenset(fact_ids),
        goal_ids=frozenset(goal_ids),
        ending_ids=frozenset(ending_ids),
        action_ids=frozenset(action_ids),
    )


def compile_script_pack(pack_path: Path | str) -> CompiledScriptPack:
    return compile_source(load_script_pack_source(pack_path))
~~~

~~~python
# backend/src/story/script_pack/__init__.py

"""Script pack source models and compiler."""

from .compiler import (
    PackCompileError,
    compile_script_pack,
    compile_source,
    load_script_pack_source,
)
from .models import CompiledScriptPack, ScriptPackSource

__all__ = [
    "CompiledScriptPack",
    "PackCompileError",
    "ScriptPackSource",
    "compile_script_pack",
    "compile_source",
    "load_script_pack_source",
]
~~~

- [ ] **Step 4: Run the compiler tests**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_script_pack_compiler.py -v
~~~

Expected: PASS, 11 tests passed.

- [ ] **Step 5: Run all V2 tests**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_package.py tests/test_condition_dsl.py \
  tests/test_script_pack_models.py tests/test_script_pack_compiler.py -v
~~~

Expected: PASS, 24 tests passed.

- [ ] **Step 6: Commit**

~~~bash
git add \
  backend/src/story/script_pack \
  backend/tests/test_script_pack_compiler.py
git commit -m "feat: compile and validate v2 script packs"
~~~

---

### Task 5: Add The First V2 Script Pack

**Files:**
- Create: \`backend/script_packs/cafe_mystery/pack.yaml\`
- Create: \`backend/tests/test_cafe_mystery_pack.py\`

- [ ] **Step 1: Write a failing test for the real pack**

~~~python
# backend/tests/test_cafe_mystery_pack.py

from pathlib import Path

from src.story.script_pack import compile_script_pack

PACK_DIR = (
    Path(__file__).resolve().parents[1]
    / "script_packs"
    / "cafe_mystery"
)


def test_cafe_mystery_pack_compiles_without_fixed_plot():
    compiled = compile_script_pack(PACK_DIR)
    dumped = compiled.source.model_dump(mode="json")

    assert compiled.source.identity.id == "cafe_mystery"
    assert len(compiled.source.characters) == 3
    assert len(
        [ending for ending in compiled.source.endings if ending.type != "fallback"]
    ) >= 3
    assert any(
        ending.type == "fallback" for ending in compiled.source.endings
    )
    assert len(compiled.source.facts.latent_questions) >= 2
    assert "plot" not in dumped
    assert "beats" not in dumped
    assert "scenes" not in dumped


def test_cafe_mystery_pack_hash_is_stable():
    assert (
        compile_script_pack(PACK_DIR).pack_hash
        == compile_script_pack(PACK_DIR).pack_hash
    )
~~~

- [ ] **Step 2: Run the test and confirm the pack is missing**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_cafe_mystery_pack.py -v
~~~

Expected: FAIL with \`script pack file not found\`.

- [ ] **Step 3: Add the complete text-only pack**

~~~yaml
# backend/script_packs/cafe_mystery/pack.yaml
schema_version: "1.0"

identity:
  id: cafe_mystery
  title: "咖啡馆疑云"
  language: zh-CN
  genres: [romance, mystery]
  expected_minutes: 120

experience:
  viewpoint: first_person
  prose_style: "克制的轻小说风格，重视动作、停顿和对话潜台词"
  tone: "温柔、日常表面下的悬疑"
  choice_density: key_moments
  min_scenes: 20
  max_scenes: 60
  reserved_resolution_scenes: 3
  forbidden_content:
    - "露骨色情内容"
    - "无铺垫的极端暴力"

protagonist:
  id: protagonist
  name: "悠真"
  personality:
    traits: [谨慎, 善于观察]
    values: [诚实, 不轻易伤害别人]
    fears: [被卷入无法控制的冲突]
    flaws: [容易犹豫]
  background: "刚搬到这座城市的大学生，在咖啡馆等待迟到的朋友。"
  capabilities: [ask, observe, support, challenge, withhold, disclose, follow, leave]
  boundaries:
    cannot:
      - "使用暴力逼供"
      - "凭空掌握尚未发现的秘密"
      - "无理由羞辱陌生人"

world:
  premise: >
    玩家在街角咖啡馆遇到寻找遗失笔记本的艾丽丝。笔记本记录了一个神秘组织的
    符号和活动线索。谨慎的鲍勃很快介入，咖啡馆店长美奈似乎也知道一些事情。
  immutable_rules:
    - "神秘组织真实存在，但不会公开展示超自然力量。"
    - "已经被玩家确认的证据不能被后续场景否认。"
    - "角色只能依据自己的知识、信念和推测行动。"
  locations:
    - id: cafe
      name: "街角咖啡馆"
      tags: [public, warm, first_meeting]
    - id: back_alley
      name: "咖啡馆后巷"
      tags: [quiet, risky]
    - id: old_library
      name: "旧图书馆"
      tags: [clues, restricted]
  factions:
    - id: veiled_circle
      name: "隐环"
      description: "活动目的不明、成员身份隐秘的组织。"
  initial_situation:
    location: cafe
    present_characters: [alice]
    known_facts: [cafe_is_open]
    time_label: "周六下午"

characters:
  - id: alice
    name: "艾丽丝"
    public_profile: "外向的大学生，自称遗失了一本记录神秘符号的笔记本。"
    personality:
      traits: [外向, 好奇, 冲动]
      values: [朋友, 真相]
      fears: [被抛弃, 连累别人]
      flaws: [紧张时容易说漏嘴]
    voice:
      style: "直接、短句，尴尬时会轻微自嘲"
      forbidden: ["无缘由地冷酷", "长篇学术演讲"]
    drives:
      - "找到可信任的调查伙伴"
      - "在别人发现前补救自己造成的失误"
    knowledge: [org_exists, alice_lost_notebook]
    secrets: [alice_lost_notebook, notebook_holder, alice_hidden_motive]
    capabilities: [ask, observe, support, disclose, follow, leave]
    initial_relationship:
      trust: 35
      affection: 5
      suspicion: 10
    boundaries:
      cannot: ["主动伤害无辜者"]

  - id: bob
    name: "鲍勃"
    public_profile: "说话谨慎的研究生，警告玩家不要轻信艾丽丝。"
    personality:
      traits: [理性, 谨慎, 固执]
      values: [证据, 安全]
      fears: [过去的错误重演]
      flaws: [把关心表现成控制]
    voice:
      style: "简短、精确，倾向先陈述可验证事实"
      forbidden: ["轻浮调情", "未经压力就全盘托出"]
    drives:
      - "阻止没有准备的人接触隐环"
      - "确认笔记本是否仍然安全"
    knowledge: [org_exists, bob_has_org_history]
    secrets: [bob_has_org_history, notebook_holder]
    capabilities: [ask, observe, challenge, withhold, follow, leave]
    initial_relationship:
      trust: 25
      affection: 0
      suspicion: 30
    boundaries:
      cannot: ["伪造实体证据"]

  - id: mina
    name: "美奈"
    public_profile: "咖啡馆店长，熟悉附近每一位常客。"
    personality:
      traits: [沉着, 体贴, 善于观察]
      values: [秩序, 保护客人]
      fears: [店内发生无法收拾的冲突]
      flaws: [习惯替别人决定什么最好]
    voice:
      style: "礼貌、含蓄，重要提醒往往藏在日常措辞里"
      forbidden: ["突然失控大喊", "炫耀秘密"]
    drives:
      - "让咖啡馆远离隐环的注意"
      - "判断玩家是否会让局势变得更糟"
    knowledge: [cafe_is_open, org_exists]
    secrets: [notebook_holder]
    capabilities: [ask, observe, support, challenge, withhold, disclose, leave]
    initial_relationship:
      trust: 40
      affection: 0
      suspicion: 15
    boundaries:
      cannot: ["主动把客人交给危险组织"]

facts:
  fixed:
    - id: cafe_is_open
      statement: "咖啡馆将在傍晚六点打烊。"
      known_by: [alice, bob, mina]
      visibility: revealed
    - id: org_exists
      statement: "名为隐环的秘密组织真实存在。"
      known_by: [alice, bob, mina]
      visibility: hidden
    - id: alice_lost_notebook
      statement: "艾丽丝确实遗失了自己的调查笔记本。"
      known_by: [alice]
      visibility: hidden
    - id: bob_has_org_history
      statement: "鲍勃过去曾因隐环遭受损失。"
      known_by: [bob]
      visibility: hidden

  latent_questions:
    - id: notebook_holder
      question: "现在谁持有笔记本？"
      selection: lazy_commit
      candidates:
        - value: bob
          weight: 1.0
          requirements: ["relationships.bob.suspicion >= 20"]
        - value: mina
          weight: 1.0
          requirements: ["relationships.mina.trust >= 30"]
        - value: courier
          weight: 0.8
          requirements: ["session.scene_count >= 4"]
      commit_when: [first_irreversible_evidence, explicit_revelation]
      evidence_required: 2
    - id: alice_hidden_motive
      question: "艾丽丝为什么急于找回笔记本？"
      selection: lazy_commit
      candidates:
        - value: protect_friend
          weight: 1.0
          requirements: ["relationships.alice.affection >= 5"]
        - value: clear_her_name
          weight: 1.0
          requirements: ["relationships.alice.suspicion >= 10"]
      commit_when: [first_irreversible_evidence, explicit_revelation]
      evidence_required: 1

  derived:
    - id: alice_trusts_player
      condition: "relationships.alice.trust >= 70"
    - id: bob_trusts_player
      condition: "relationships.bob.trust >= 65"
    - id: core_truth_known
      condition: "facts.notebook_holder.visibility == 'revealed'"

goals:
  - id: alice_find_ally
    owner: alice
    desire: "找到愿意共同调查且不会利用她失误的人。"
    urgency: 0.8
    conflicts_with: [bob_limit_exposure]
    success_condition: "relationships.alice.trust >= 70"
    failure_condition: "relationships.alice.trust <= 10"
  - id: bob_limit_exposure
    owner: bob
    desire: "确认玩家不会把隐环线索扩散给更多人。"
    urgency: 0.7
    conflicts_with: [alice_find_ally]
    success_condition: "relationships.bob.trust >= 65"
    failure_condition: "relationships.bob.trust <= 10"
  - id: protagonist_learn_truth
    owner: protagonist
    desire: "查明笔记本去向，并理解三人的真实立场。"
    urgency: 0.6
    conflicts_with: []
    success_condition: "facts.notebook_holder.visibility == 'revealed'"
    failure_condition: "session.scene_count >= 57 and facts.notebook_holder.visibility != 'revealed'"

interaction_rules:
  enabled_standard: [ask, observe, support, challenge, withhold, disclose, follow, leave]
  disabled: []
  extensions: []

endings:
  - id: alice_alliance
    title: "共同追寻"
    type: hopeful
    priority: 100
    eligibility:
      all:
        - "goals.alice_find_ally.completed"
        - "relationships.alice.trust >= 70"
        - "facts.notebook_holder.truth_status == 'committed'"
      any: []
      none: []
    required_outcomes:
      - "玩家与艾丽丝决定继续合作。"
      - "本局已经出现的笔记本证据得到解释。"
    forbidden_outcomes:
      - "无依据地将艾丽丝写成隐环首领。"
    closing_tone: "温暖，但保留对隐环的未知风险"

  - id: bob_alliance
    title: "谨慎的同盟"
    type: hopeful
    priority: 90
    eligibility:
      all:
        - "goals.bob_limit_exposure.completed"
        - "relationships.bob.trust >= 65"
      any: []
      none: []
    required_outcomes:
      - "鲍勃承认玩家有资格继续调查。"
      - "说明艾丽丝对这一选择的反应。"
    forbidden_outcomes:
      - "鲍勃突然放弃对证据的重视。"
    closing_tone: "克制、可靠"

  - id: independent_truth
    title: "自己的答案"
    type: neutral
    priority: 80
    eligibility:
      all:
        - "goals.protagonist_learn_truth.completed"
        - "facts.notebook_holder.visibility == 'revealed'"
      any: []
      none:
        - "relationships.alice.trust >= 70"
        - "relationships.bob.trust >= 65"
    required_outcomes:
      - "玩家独立判断笔记本真相。"
      - "回应艾丽丝、鲍勃和美奈的立场。"
    forbidden_outcomes:
      - "把未提交的潜在事实当成真相。"
    closing_tone: "清醒、略带遗憾"

  - id: trust_broken
    title: "无法挽回的距离"
    type: tragic
    priority: 70
    eligibility:
      all:
        - "relationships.alice.trust <= 10"
        - "relationships.bob.trust <= 10"
      any: []
      none: []
    required_outcomes:
      - "说明哪些玩家选择导致双方都失去信任。"
    forbidden_outcomes:
      - "无铺垫地让所有关系恢复。"
    closing_tone: "冷静、苦涩"

  - id: closing_time
    title: "未竟的傍晚"
    type: fallback
    priority: 1
    eligibility:
      all: ["session.scene_count >= 57"]
      any: []
      none: []
    required_outcomes:
      - "关闭当前冲突，不新增核心谜团。"
      - "明确故事暂时停在什么状态。"
    forbidden_outcomes:
      - "凭空宣布一个未提交的幕后真相。"
    closing_tone: "安静、留有余韵"

assets: {}
~~~

- [ ] **Step 4: Run the real-pack tests**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_cafe_mystery_pack.py -v
~~~

Expected: PASS, 2 tests passed.

- [ ] **Step 5: Commit**

~~~bash
git add backend/script_packs/cafe_mystery backend/tests/test_cafe_mystery_pack.py
git commit -m "feat: add cafe mystery v2 script pack"
~~~

---

### Task 6: Initialize Layered Session State

**Files:**
- Create: \`backend/src/story/state/models.py\`
- Create: \`backend/tests/test_story_state.py\`
- Modify: \`backend/src/story/state/__init__.py\`

- [ ] **Step 1: Write failing initialization tests**

~~~python
# backend/tests/test_story_state.py

from pathlib import Path

import pytest
from pydantic import ValidationError
from src.story.script_pack import compile_script_pack, compile_source
from src.story.state import (
    FactTruthStatus,
    FactVisibility,
    StoryPhase,
    initial_session_state,
)
from tests.story_factories import minimal_script_pack_dict


def test_initial_state_separates_truth_visibility_and_character_knowledge():
    pack = compile_source(minimal_script_pack_dict())

    state = initial_session_state(pack, "session_01", session_seed=42)

    assert state.revision == 0
    assert state.world.phase == StoryPhase.OPENING
    assert state.world.location_id == "cafe"
    assert state.facts["cafe_is_open"].truth_status == FactTruthStatus.COMMITTED
    assert state.facts["cafe_is_open"].visibility == FactVisibility.REVEALED
    assert (
        state.facts["who_took_notebook"].truth_status
        == FactTruthStatus.POSSIBLE
    )
    assert state.facts["who_took_notebook"].value is None
    assert "cafe_is_open" in state.characters["alice"].knowledge
    assert state.world.relationships["alice"]["trust"] == 35
    assert state.world.goals["alice_find_ally"].progress == 0


def test_real_pack_state_keeps_private_fixed_fact_hidden():
    pack = compile_script_pack(
        Path(__file__).resolve().parents[1]
        / "script_packs"
        / "cafe_mystery"
    )

    state = initial_session_state(pack, "session_02", session_seed=7)

    assert state.facts["org_exists"].truth_status == FactTruthStatus.COMMITTED
    assert state.facts["org_exists"].visibility == FactVisibility.HIDDEN
    assert "org_exists" in state.characters["alice"].knowledge
    assert "org_exists" in state.characters["bob"].knowledge


def test_session_state_is_immutable():
    state = initial_session_state(
        compile_source(minimal_script_pack_dict()),
        "session_01",
        session_seed=42,
    )

    with pytest.raises(ValidationError):
        state.revision = 1
~~~

- [ ] **Step 2: Run the tests and confirm state models are missing**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_state.py -v
~~~

Expected: FAIL during collection because the state exports do not exist.

- [ ] **Step 3: Implement frozen layered state models**

~~~python
# backend/src/story/state/models.py

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from src.story.script_pack.models import CompiledScriptPack


def utc_now() -> datetime:
    return datetime.now(UTC)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionStatus(str, Enum):
    ACTIVE = "active"
    RESOLVING = "resolving"
    ENDED = "ended"


class StoryPhase(str, Enum):
    OPENING = "opening"
    EXPLORATION = "exploration"
    ESCALATION = "escalation"
    CRISIS = "crisis"
    RESOLUTION = "resolution"


class FactTruthStatus(str, Enum):
    POSSIBLE = "possible"
    STAGED = "staged"
    COMMITTED = "committed"


class FactVisibility(str, Enum):
    HIDDEN = "hidden"
    EVIDENCED = "evidenced"
    REVEALED = "revealed"


class GoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ThreadStatus(str, Enum):
    OPEN = "open"
    ADVANCING = "advancing"
    DORMANT = "dormant"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class FactRecord(FrozenModel):
    id: str
    truth_status: FactTruthStatus
    value: Any = None
    visibility: FactVisibility = FactVisibility.HIDDEN
    evidence_required: int = Field(default=0, ge=0)
    evidence_event_ids: tuple[str, ...] = ()
    committed_by_event_id: str | None = None
    known_by: frozenset[str] = frozenset()


class BeliefRecord(FrozenModel):
    value: Any
    confidence: float = Field(default=0.5, ge=0, le=1)
    source_event_id: str | None = None


class CharacterRuntime(FrozenModel):
    character_id: str
    knowledge: frozenset[str] = frozenset()
    beliefs: dict[str, BeliefRecord] = Field(default_factory=dict)
    suspicions: dict[str, BeliefRecord] = Field(default_factory=dict)
    intentions: tuple[str, ...] = ()
    emotional_state: dict[str, float] = Field(default_factory=dict)


class GoalRuntime(FrozenModel):
    goal_id: str
    status: GoalStatus = GoalStatus.ACTIVE
    progress: float = Field(default=0, ge=0, le=1)
    evidence_event_ids: tuple[str, ...] = ()

    @property
    def completed(self) -> bool:
        return self.status == GoalStatus.COMPLETED


class NarrativeThread(FrozenModel):
    id: str
    type: str
    status: ThreadStatus = ThreadStatus.OPEN
    introduced_at: str
    involved_character_ids: tuple[str, ...] = ()
    related_fact_ids: tuple[str, ...] = ()
    urgency: float = Field(default=0.5, ge=0, le=1)
    payoff_due_before: StoryPhase = StoryPhase.RESOLUTION
    last_advanced_event_id: str | None = None


class PendingSceneReference(FrozenModel):
    scene_id: str
    revision: int = Field(ge=1)
    terminal: str


class PendingDecisionReference(FrozenModel):
    decision_id: str
    scene_id: str
    revision: int = Field(ge=1)


class EndingRuntime(FrozenModel):
    ending_id: str
    entered_at_revision: int = Field(ge=1)
    required_payoffs: tuple[str, ...]
    final_scene_budget: int = Field(ge=1)


class WorldSnapshot(FrozenModel):
    location_id: str
    time_label: str
    present_character_ids: tuple[str, ...]
    object_states: dict[str, Any] = Field(default_factory=dict)
    relationships: dict[str, dict[str, int]] = Field(default_factory=dict)
    goals: dict[str, GoalRuntime] = Field(default_factory=dict)
    phase: StoryPhase = StoryPhase.OPENING
    pressure: float = Field(default=0.1, ge=0, le=1)
    scene_count: int = Field(default=0, ge=0)
    max_scenes: int = Field(ge=1)
    reserved_resolution_scenes: int = Field(ge=1)


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


def initial_session_state(
    pack: CompiledScriptPack,
    session_id: str,
    session_seed: int,
) -> SessionState:
    source = pack.source
    initial_known = set(source.world.initial_situation.known_facts)

    facts: dict[str, FactRecord] = {}
    for fact in source.facts.fixed:
        visibility = (
            FactVisibility.REVEALED
            if fact.visibility == "revealed" or fact.id in initial_known
            else FactVisibility.HIDDEN
        )
        facts[fact.id] = FactRecord(
            id=fact.id,
            truth_status=FactTruthStatus.COMMITTED,
            value=True,
            visibility=visibility,
            known_by=frozenset(fact.known_by),
        )
    for question in source.facts.latent_questions:
        facts[question.id] = FactRecord(
            id=question.id,
            truth_status=FactTruthStatus.POSSIBLE,
            visibility=FactVisibility.HIDDEN,
            evidence_required=question.evidence_required,
        )

    characters: dict[str, CharacterRuntime] = {}
    for character in source.characters:
        knowledge = set(character.knowledge)
        knowledge.update(
            fact.id
            for fact in source.facts.fixed
            if character.id in fact.known_by
        )
        characters[character.id] = CharacterRuntime(
            character_id=character.id,
            knowledge=frozenset(knowledge),
            beliefs={
                key: BeliefRecord(value=value)
                for key, value in character.beliefs.items()
            },
        )

    world = WorldSnapshot(
        location_id=source.world.initial_situation.location,
        time_label=source.world.initial_situation.time_label,
        present_character_ids=source.world.initial_situation.present_characters,
        relationships={
            character.id: dict(character.initial_relationship)
            for character in source.characters
        },
        goals={
            goal.id: GoalRuntime(goal_id=goal.id)
            for goal in source.goals
        },
        max_scenes=source.experience.max_scenes,
        reserved_resolution_scenes=(
            source.experience.reserved_resolution_scenes
        ),
    )

    return SessionState(
        session_id=session_id,
        pack_id=source.identity.id,
        pack_hash=pack.pack_hash,
        session_seed=session_seed,
        world=world,
        facts=facts,
        characters=characters,
    )
~~~

~~~python
# backend/src/story/state/__init__.py

"""Event-sourced story session state."""

from .models import (
    CharacterRuntime,
    FactRecord,
    FactTruthStatus,
    FactVisibility,
    GoalRuntime,
    GoalStatus,
    NarrativeThread,
    SessionState,
    SessionStatus,
    StoryPhase,
    ThreadStatus,
    WorldSnapshot,
    initial_session_state,
)

__all__ = [
    "CharacterRuntime",
    "FactRecord",
    "FactTruthStatus",
    "FactVisibility",
    "GoalRuntime",
    "GoalStatus",
    "NarrativeThread",
    "SessionState",
    "SessionStatus",
    "StoryPhase",
    "ThreadStatus",
    "WorldSnapshot",
    "initial_session_state",
]
~~~

- [ ] **Step 4: Run the state tests**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_state.py -v
~~~

Expected: PASS, 3 tests passed.

- [ ] **Step 5: Commit**

~~~bash
git add backend/src/story/state backend/tests/test_story_state.py
git commit -m "feat: initialize layered v2 session state"
~~~

---

### Task 7: Add Typed Domain Events And The Pure Reducer

**Files:**
- Create: \`backend/src/story/state/events.py\`
- Create: \`backend/src/story/state/reducer.py\`
- Create: \`backend/tests/test_story_reducer.py\`
- Modify: \`backend/src/story/state/__init__.py\`

- [ ] **Step 1: Write failing reducer tests**

~~~python
# backend/tests/test_story_reducer.py

import pytest
from src.story.script_pack import compile_source
from src.story.state import (
    CharacterLearnedFact,
    EndingEntered,
    EndingRuntime,
    EventEnvelope,
    FactCommitted,
    FactRevealed,
    FactTruthStatus,
    FactVisibility,
    PhaseAdvanced,
    RelationshipChanged,
    SceneAcknowledged,
    SceneCommitted,
    SessionEnded,
    StoryPhase,
    apply_event,
    apply_events,
    initial_session_state,
)
from src.story.state.reducer import StateTransitionError
from tests.story_factories import minimal_script_pack_dict


def _state():
    return initial_session_state(
        compile_source(minimal_script_pack_dict()),
        "session_01",
        session_seed=42,
    )


def _envelope(state, event, offset=1):
    return EventEnvelope(
        session_id=state.session_id,
        sequence=state.revision + offset,
        event=event,
    )


def test_fact_commit_evidence_and_reveal_are_separate():
    original = _state()
    committed = apply_event(
        original,
        _envelope(
            original,
            FactCommitted(
                fact_id="who_took_notebook",
                value="alice",
                evidence_event_ids=("evidence_01",),
            ),
        ),
    )

    fact = committed.facts["who_took_notebook"]
    assert fact.truth_status == FactTruthStatus.COMMITTED
    assert fact.visibility == FactVisibility.EVIDENCED
    assert original.facts["who_took_notebook"].value is None

    revealed = apply_event(
        committed,
        _envelope(
            committed,
            FactRevealed(fact_id="who_took_notebook"),
        ),
    )
    assert (
        revealed.facts["who_took_notebook"].visibility
        == FactVisibility.REVEALED
    )


def test_committed_fact_cannot_be_rewritten():
    state = _state()
    state = apply_event(
        state,
        _envelope(
            state,
            FactCommitted(
                fact_id="who_took_notebook",
                value="alice",
                evidence_event_ids=("evidence_01",),
            ),
        ),
    )

    with pytest.raises(StateTransitionError, match="already committed"):
        apply_event(
            state,
            _envelope(
                state,
                FactCommitted(
                    fact_id="who_took_notebook",
                    value="stranger",
                    evidence_event_ids=("evidence_02",),
                ),
            ),
        )


def test_character_learning_updates_both_knowledge_indexes():
    state = _state()
    state = apply_event(
        state,
        _envelope(
            state,
            FactCommitted(
                fact_id="who_took_notebook",
                value="alice",
                evidence_event_ids=("evidence_01",),
            ),
        ),
    )
    state = apply_event(
        state,
        _envelope(
            state,
            CharacterLearnedFact(
                character_id="alice",
                fact_id="who_took_notebook",
            ),
        ),
    )

    assert "who_took_notebook" in state.characters["alice"].knowledge
    assert "alice" in state.facts["who_took_notebook"].known_by


def test_event_batch_is_atomic_when_later_event_fails():
    original = _state()
    events = [
        _envelope(
            original,
            RelationshipChanged(
                character_id="alice",
                axis="trust",
                delta=5,
            ),
            offset=1,
        ),
        _envelope(
            original,
            FactRevealed(fact_id="who_took_notebook"),
            offset=2,
        ),
    ]

    with pytest.raises(StateTransitionError):
        apply_events(original, events)

    assert original.world.relationships["alice"]["trust"] == 35
    assert original.revision == 0


def test_scene_acknowledgement_requires_matching_pending_scene():
    original = _state()
    committed = apply_event(
        original,
        _envelope(
            original,
            SceneCommitted(
                scene_id="scene_01",
                terminal="continue",
                location_id="cafe",
                present_character_ids=("alice",),
            ),
        ),
    )
    assert committed.pending_scene.scene_id == "scene_01"
    assert committed.world.scene_count == 1

    acknowledged = apply_event(
        committed,
        _envelope(
            committed,
            SceneAcknowledged(scene_id="scene_01"),
        ),
    )
    assert acknowledged.pending_scene is None


def test_phase_can_only_advance_one_step():
    state = _state()
    with pytest.raises(StateTransitionError, match="one step"):
        apply_event(
            state,
            _envelope(
                state,
                PhaseAdvanced(phase=StoryPhase.ESCALATION),
            ),
        )


def test_decision_id_is_only_valid_for_decision_scene():
    state = _state()

    with pytest.raises(StateTransitionError, match="decision_id"):
        apply_event(
            state,
            _envelope(
                state,
                SceneCommitted(
                    scene_id="scene_01",
                    terminal="continue",
                    location_id="cafe",
                    present_character_ids=("alice",),
                    decision_id="decision_01",
                ),
            ),
        )


def test_ending_entry_revision_must_match_event_sequence():
    state = _state()

    with pytest.raises(StateTransitionError, match="ending revision"):
        apply_event(
            state,
            _envelope(
                state,
                EndingEntered(
                    ending=EndingRuntime(
                        ending_id="fallback_ending",
                        entered_at_revision=2,
                        required_payoffs=("Close the current conflict.",),
                        final_scene_budget=1,
                    )
                ),
            ),
        )


def test_ended_session_rejects_new_events():
    state = _state()
    state = apply_event(
        state,
        _envelope(
            state,
            EndingEntered(
                ending=EndingRuntime(
                    ending_id="fallback_ending",
                    entered_at_revision=1,
                    required_payoffs=("Close the current conflict.",),
                    final_scene_budget=1,
                )
            ),
        ),
    )
    state = apply_event(
        state,
        _envelope(
            state,
            SessionEnded(ending_id="fallback_ending"),
        ),
    )

    with pytest.raises(StateTransitionError, match="ended session"):
        apply_event(
            state,
            _envelope(
                state,
                RelationshipChanged(
                    character_id="alice",
                    axis="trust",
                    delta=1,
                ),
            ),
        )
~~~

- [ ] **Step 2: Run the tests and confirm event exports are missing**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_reducer.py -v
~~~

Expected: FAIL during collection because typed events and reducer exports do not exist.

- [ ] **Step 3: Define the event protocol**

~~~python
# backend/src/story/state/events.py

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field
from src.story.state.models import (
    BeliefRecord,
    EndingRuntime,
    FrozenModel,
    GoalStatus,
    NarrativeThread,
    StoryPhase,
    ThreadStatus,
    utc_now,
)


class SceneCommitted(FrozenModel):
    type: Literal["scene_committed"] = "scene_committed"
    scene_id: str
    terminal: Literal["continue", "decision", "ending"]
    location_id: str
    present_character_ids: tuple[str, ...]
    decision_id: str | None = None


class SceneAcknowledged(FrozenModel):
    type: Literal["scene_acknowledged"] = "scene_acknowledged"
    scene_id: str


class PlayerActionSelected(FrozenModel):
    type: Literal["player_action_selected"] = "player_action_selected"
    decision_id: str
    option_id: str
    idempotency_key: str


class ActionResolved(FrozenModel):
    type: Literal["action_resolved"] = "action_resolved"
    action_id: str
    outcome: Literal["success", "partial", "resisted", "backfire"]


class FactCommitted(FrozenModel):
    type: Literal["fact_committed"] = "fact_committed"
    fact_id: str
    value: Any
    evidence_event_ids: tuple[str, ...] = ()


class FactEvidenced(FrozenModel):
    type: Literal["fact_evidenced"] = "fact_evidenced"
    fact_id: str
    evidence_event_id: str


class FactRevealed(FrozenModel):
    type: Literal["fact_revealed"] = "fact_revealed"
    fact_id: str


class CharacterLearnedFact(FrozenModel):
    type: Literal["character_learned_fact"] = "character_learned_fact"
    character_id: str
    fact_id: str


class BeliefChanged(FrozenModel):
    type: Literal["belief_changed"] = "belief_changed"
    character_id: str
    belief_id: str
    belief: BeliefRecord


class RelationshipChanged(FrozenModel):
    type: Literal["relationship_changed"] = "relationship_changed"
    character_id: str
    axis: str
    delta: int = Field(ge=-100, le=100)


class GoalAdvanced(FrozenModel):
    type: Literal["goal_advanced"] = "goal_advanced"
    goal_id: str
    delta: float = Field(ge=-1, le=1)
    status: GoalStatus | None = None
    evidence_event_id: str | None = None


class ThreadOpened(FrozenModel):
    type: Literal["thread_opened"] = "thread_opened"
    thread: NarrativeThread


class ThreadAdvanced(FrozenModel):
    type: Literal["thread_advanced"] = "thread_advanced"
    thread_id: str
    urgency: float | None = Field(default=None, ge=0, le=1)


class ThreadClosed(FrozenModel):
    type: Literal["thread_closed"] = "thread_closed"
    thread_id: str
    status: Literal[ThreadStatus.RESOLVED, ThreadStatus.ABANDONED]


class PhaseAdvanced(FrozenModel):
    type: Literal["phase_advanced"] = "phase_advanced"
    phase: StoryPhase


class EndingEntered(FrozenModel):
    type: Literal["ending_entered"] = "ending_entered"
    ending: EndingRuntime


class SessionEnded(FrozenModel):
    type: Literal["session_ended"] = "session_ended"
    ending_id: str


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
    | SessionEnded,
    Field(discriminator="type"),
]


class EventEnvelope(FrozenModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    sequence: int = Field(ge=1)
    occurred_at: datetime = Field(default_factory=utc_now)
    event: StoryEvent
~~~

- [ ] **Step 4: Implement the pure reducer**

~~~python
# backend/src/story/state/reducer.py

from __future__ import annotations

from collections.abc import Iterable

from src.story.state.events import (
    ActionResolved,
    BeliefChanged,
    CharacterLearnedFact,
    EndingEntered,
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
from src.story.state.models import (
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


class StateTransitionError(ValueError):
    """A domain event violates a story-state invariant."""


_PHASES = (
    StoryPhase.OPENING,
    StoryPhase.EXPLORATION,
    StoryPhase.ESCALATION,
    StoryPhase.CRISIS,
    StoryPhase.RESOLUTION,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StateTransitionError(message)


def apply_event(state: SessionState, envelope: EventEnvelope) -> SessionState:
    _require(
        envelope.session_id == state.session_id,
        "event session does not match state session",
    )
    _require(
        envelope.sequence == state.revision + 1,
        f"expected event sequence {state.revision + 1}, got {envelope.sequence}",
    )
    _require(
        state.status != SessionStatus.ENDED,
        "ended session cannot accept new events",
    )

    event = envelope.event
    next_state = state

    if isinstance(event, SceneCommitted):
        _require(next_state.pending_scene is None, "a scene is already pending")
        _require(
            next_state.world.scene_count < next_state.world.max_scenes,
            "max scene count reached",
        )
        _require(
            (event.terminal == "decision") == (event.decision_id is not None),
            "decision_id must be present only for a decision scene",
        )
        world = next_state.world.model_copy(
            update={
                "location_id": event.location_id,
                "present_character_ids": event.present_character_ids,
                "scene_count": next_state.world.scene_count + 1,
            }
        )
        pending_scene = PendingSceneReference(
            scene_id=event.scene_id,
            revision=envelope.sequence,
            terminal=event.terminal,
        )
        pending_decision = (
            PendingDecisionReference(
                decision_id=event.decision_id,
                scene_id=event.scene_id,
                revision=envelope.sequence,
            )
            if event.decision_id is not None
            else None
        )
        next_state = next_state.model_copy(
            update={
                "world": world,
                "pending_scene": pending_scene,
                "pending_decision": pending_decision,
            }
        )

    elif isinstance(event, SceneAcknowledged):
        _require(next_state.pending_scene is not None, "no scene is pending")
        _require(
            next_state.pending_scene.scene_id == event.scene_id,
            "scene acknowledgement does not match pending scene",
        )
        _require(
            next_state.pending_decision is None,
            "decision scenes are acknowledged by player_action_selected",
        )
        next_state = next_state.model_copy(update={"pending_scene": None})

    elif isinstance(event, PlayerActionSelected):
        _require(next_state.pending_decision is not None, "no decision is pending")
        _require(
            next_state.pending_decision.decision_id == event.decision_id,
            "player action does not match pending decision",
        )
        next_state = next_state.model_copy(
            update={"pending_scene": None, "pending_decision": None}
        )

    elif isinstance(event, ActionResolved):
        pass

    elif isinstance(event, FactCommitted):
        _require(event.fact_id in next_state.facts, "unknown fact")
        current = next_state.facts[event.fact_id]
        _require(
            current.truth_status in {
                FactTruthStatus.POSSIBLE,
                FactTruthStatus.STAGED,
            },
            f"fact {event.fact_id} is already committed",
        )
        evidence = tuple(dict.fromkeys(event.evidence_event_ids))
        visibility = (
            FactVisibility.EVIDENCED
            if evidence
            else FactVisibility.HIDDEN
        )
        updated = current.model_copy(
            update={
                "truth_status": FactTruthStatus.COMMITTED,
                "value": event.value,
                "visibility": visibility,
                "evidence_event_ids": evidence,
                "committed_by_event_id": envelope.event_id,
            }
        )
        facts = dict(next_state.facts)
        facts[event.fact_id] = updated
        next_state = next_state.model_copy(update={"facts": facts})

    elif isinstance(event, FactEvidenced):
        _require(event.fact_id in next_state.facts, "unknown fact")
        current = next_state.facts[event.fact_id]
        _require(
            current.truth_status == FactTruthStatus.COMMITTED,
            "evidence can only attach to a committed fact",
        )
        evidence = tuple(
            dict.fromkeys(
                (*current.evidence_event_ids, event.evidence_event_id)
            )
        )
        visibility = (
            current.visibility
            if current.visibility == FactVisibility.REVEALED
            else FactVisibility.EVIDENCED
        )
        facts = dict(next_state.facts)
        facts[event.fact_id] = current.model_copy(
            update={
                "evidence_event_ids": evidence,
                "visibility": visibility,
            }
        )
        next_state = next_state.model_copy(update={"facts": facts})

    elif isinstance(event, FactRevealed):
        _require(event.fact_id in next_state.facts, "unknown fact")
        current = next_state.facts[event.fact_id]
        _require(
            current.truth_status == FactTruthStatus.COMMITTED,
            "only a committed fact can be revealed",
        )
        _require(
            len(current.evidence_event_ids) >= current.evidence_required,
            f"fact {event.fact_id} lacks required evidence",
        )
        facts = dict(next_state.facts)
        facts[event.fact_id] = current.model_copy(
            update={"visibility": FactVisibility.REVEALED}
        )
        next_state = next_state.model_copy(update={"facts": facts})

    elif isinstance(event, CharacterLearnedFact):
        _require(event.character_id in next_state.characters, "unknown character")
        _require(event.fact_id in next_state.facts, "unknown fact")
        _require(
            next_state.facts[event.fact_id].truth_status
            == FactTruthStatus.COMMITTED,
            "character cannot learn an uncommitted fact",
        )
        character = next_state.characters[event.character_id]
        characters = dict(next_state.characters)
        characters[event.character_id] = character.model_copy(
            update={"knowledge": character.knowledge | {event.fact_id}}
        )
        fact = next_state.facts[event.fact_id]
        facts = dict(next_state.facts)
        facts[event.fact_id] = fact.model_copy(
            update={"known_by": fact.known_by | {event.character_id}}
        )
        next_state = next_state.model_copy(
            update={"characters": characters, "facts": facts}
        )

    elif isinstance(event, BeliefChanged):
        _require(event.character_id in next_state.characters, "unknown character")
        character = next_state.characters[event.character_id]
        beliefs = dict(character.beliefs)
        beliefs[event.belief_id] = event.belief
        characters = dict(next_state.characters)
        characters[event.character_id] = character.model_copy(
            update={"beliefs": beliefs}
        )
        next_state = next_state.model_copy(update={"characters": characters})

    elif isinstance(event, RelationshipChanged):
        _require(
            event.character_id in next_state.world.relationships,
            "unknown relationship character",
        )
        relationships = {
            key: dict(value)
            for key, value in next_state.world.relationships.items()
        }
        current = relationships[event.character_id].get(event.axis, 0)
        relationships[event.character_id][event.axis] = max(
            0, min(100, current + event.delta)
        )
        world = next_state.world.model_copy(
            update={"relationships": relationships}
        )
        next_state = next_state.model_copy(update={"world": world})

    elif isinstance(event, GoalAdvanced):
        _require(event.goal_id in next_state.world.goals, "unknown goal")
        current = next_state.world.goals[event.goal_id]
        progress = max(0.0, min(1.0, current.progress + event.delta))
        status = event.status or current.status
        if progress >= 1:
            status = GoalStatus.COMPLETED
        evidence = current.evidence_event_ids
        if event.evidence_event_id:
            evidence = tuple(
                dict.fromkeys((*evidence, event.evidence_event_id))
            )
        goals = dict(next_state.world.goals)
        goals[event.goal_id] = current.model_copy(
            update={
                "progress": progress,
                "status": status,
                "evidence_event_ids": evidence,
            }
        )
        world = next_state.world.model_copy(update={"goals": goals})
        next_state = next_state.model_copy(update={"world": world})

    elif isinstance(event, ThreadOpened):
        _require(event.thread.id not in next_state.threads, "thread already exists")
        threads = dict(next_state.threads)
        threads[event.thread.id] = event.thread
        next_state = next_state.model_copy(update={"threads": threads})

    elif isinstance(event, ThreadAdvanced):
        _require(event.thread_id in next_state.threads, "unknown thread")
        current = next_state.threads[event.thread_id]
        _require(
            current.status not in {
                ThreadStatus.RESOLVED,
                ThreadStatus.ABANDONED,
            },
            "closed thread cannot advance",
        )
        threads = dict(next_state.threads)
        threads[event.thread_id] = current.model_copy(
            update={
                "status": ThreadStatus.ADVANCING,
                "urgency": (
                    event.urgency
                    if event.urgency is not None
                    else current.urgency
                ),
                "last_advanced_event_id": envelope.event_id,
            }
        )
        next_state = next_state.model_copy(update={"threads": threads})

    elif isinstance(event, ThreadClosed):
        _require(event.thread_id in next_state.threads, "unknown thread")
        current = next_state.threads[event.thread_id]
        threads = dict(next_state.threads)
        threads[event.thread_id] = current.model_copy(
            update={"status": ThreadStatus(event.status)}
        )
        next_state = next_state.model_copy(update={"threads": threads})

    elif isinstance(event, PhaseAdvanced):
        current_index = _PHASES.index(next_state.world.phase)
        target_index = _PHASES.index(event.phase)
        _require(
            target_index == current_index + 1,
            "phase must advance exactly one step",
        )
        world = next_state.world.model_copy(update={"phase": event.phase})
        next_state = next_state.model_copy(update={"world": world})

    elif isinstance(event, EndingEntered):
        _require(next_state.ending is None, "ending already entered")
        _require(
            event.ending.entered_at_revision == envelope.sequence,
            "ending revision must match event sequence",
        )
        world = next_state.world.model_copy(
            update={"phase": StoryPhase.RESOLUTION}
        )
        next_state = next_state.model_copy(
            update={
                "status": SessionStatus.RESOLVING,
                "world": world,
                "ending": event.ending,
            }
        )

    elif isinstance(event, SessionEnded):
        _require(next_state.ending is not None, "cannot end without ending state")
        _require(
            next_state.ending.ending_id == event.ending_id,
            "ending id does not match entered ending",
        )
        next_state = next_state.model_copy(
            update={
                "status": SessionStatus.ENDED,
                "pending_scene": None,
                "pending_decision": None,
            }
        )

    return next_state.model_copy(update={"revision": envelope.sequence})


def apply_events(
    state: SessionState,
    envelopes: Iterable[EventEnvelope],
) -> SessionState:
    candidate = state
    for envelope in envelopes:
        candidate = apply_event(candidate, envelope)
    return candidate
~~~

- [ ] **Step 5: Export the events and reducer**

~~~python
# backend/src/story/state/__init__.py

"""Event-sourced story session state."""

from .events import (
    ActionResolved,
    BeliefChanged,
    CharacterLearnedFact,
    EndingEntered,
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
from .models import (
    BeliefRecord,
    CharacterRuntime,
    EndingRuntime,
    FactRecord,
    FactTruthStatus,
    FactVisibility,
    GoalRuntime,
    GoalStatus,
    NarrativeThread,
    PendingDecisionReference,
    PendingSceneReference,
    SessionState,
    SessionStatus,
    StoryPhase,
    ThreadStatus,
    WorldSnapshot,
    initial_session_state,
)
from .reducer import StateTransitionError, apply_event, apply_events

__all__ = [
    "ActionResolved",
    "BeliefChanged",
    "BeliefRecord",
    "CharacterLearnedFact",
    "CharacterRuntime",
    "EndingEntered",
    "EndingRuntime",
    "EventEnvelope",
    "FactCommitted",
    "FactEvidenced",
    "FactRecord",
    "FactRevealed",
    "FactTruthStatus",
    "FactVisibility",
    "GoalAdvanced",
    "GoalRuntime",
    "GoalStatus",
    "NarrativeThread",
    "PendingDecisionReference",
    "PendingSceneReference",
    "PhaseAdvanced",
    "PlayerActionSelected",
    "RelationshipChanged",
    "SceneAcknowledged",
    "SceneCommitted",
    "SessionEnded",
    "SessionState",
    "SessionStatus",
    "StateTransitionError",
    "StoryPhase",
    "ThreadAdvanced",
    "ThreadClosed",
    "ThreadOpened",
    "ThreadStatus",
    "WorldSnapshot",
    "apply_event",
    "apply_events",
    "initial_session_state",
]
~~~

- [ ] **Step 6: Run reducer and state tests**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_state.py tests/test_story_reducer.py -v
~~~

Expected: PASS, 12 tests passed.

- [ ] **Step 7: Commit**

~~~bash
git add \
  backend/src/story/state \
  backend/tests/test_story_reducer.py
git commit -m "feat: apply typed story events with pure reducer"
~~~

---

### Task 8: Persist Events, Snapshots, And Revisions In SQLite

**Files:**
- Create: \`backend/src/story/storage/event_store.py\`
- Create: \`backend/tests/test_story_event_store.py\`
- Modify: \`backend/src/story/storage/__init__.py\`

- [ ] **Step 1: Write failing event-store tests**

~~~python
# backend/tests/test_story_event_store.py

from pathlib import Path

import pytest
from src.story.script_pack import compile_source
from src.story.state import (
    FactRevealed,
    RelationshipChanged,
    initial_session_state,
)
from src.story.state.reducer import StateTransitionError
from src.story.storage import (
    RevisionConflict,
    SessionAlreadyExists,
    StoryEventStore,
)
from tests.story_factories import minimal_script_pack_dict


def _state():
    return initial_session_state(
        compile_source(minimal_script_pack_dict()),
        "session_01",
        session_seed=42,
    )


def test_create_and_load_initial_session(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    state = _state()

    store.create_session(state)
    loaded = store.load_session(state.session_id)

    assert loaded == state
    assert store.list_sessions() == ["session_01"]
    assert store.event_count("session_01") == 0


def test_append_assigns_sequences_and_replays_after_snapshot(tmp_path: Path):
    database = tmp_path / "story.db"
    store = StoryEventStore(database, snapshot_every=2)
    store.create_session(_state())

    state, first_batch = store.append(
        "session_01",
        expected_revision=0,
        events=[
            RelationshipChanged(
                character_id="alice",
                axis="trust",
                delta=5,
            ),
            RelationshipChanged(
                character_id="alice",
                axis="trust",
                delta=4,
            ),
        ],
    )
    state, second_batch = store.append(
        "session_01",
        expected_revision=state.revision,
        events=[
            RelationshipChanged(
                character_id="alice",
                axis="trust",
                delta=3,
            )
        ],
    )

    assert [event.sequence for event in first_batch] == [1, 2]
    assert [event.sequence for event in second_batch] == [3]
    assert state.world.relationships["alice"]["trust"] == 47

    reopened = StoryEventStore(database, snapshot_every=2)
    replayed = reopened.load_session("session_01")
    assert replayed == state
    assert reopened.event_count("session_01") == 3


def test_stale_expected_revision_is_rejected(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    store.create_session(_state())
    store.append(
        "session_01",
        expected_revision=0,
        events=[
            RelationshipChanged(
                character_id="alice",
                axis="trust",
                delta=1,
            )
        ],
    )

    with pytest.raises(RevisionConflict, match="expected 0, current 1"):
        store.append(
            "session_01",
            expected_revision=0,
            events=[
                RelationshipChanged(
                    character_id="alice",
                    axis="trust",
                    delta=1,
                )
            ],
        )


def test_invalid_batch_rolls_back_every_event(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    original = _state()
    store.create_session(original)

    with pytest.raises(StateTransitionError):
        store.append(
            "session_01",
            expected_revision=0,
            events=[
                RelationshipChanged(
                    character_id="alice",
                    axis="trust",
                    delta=5,
                ),
                FactRevealed(fact_id="who_took_notebook"),
            ],
        )

    assert store.load_session("session_01") == original
    assert store.event_count("session_01") == 0


def test_duplicate_session_is_rejected(tmp_path: Path):
    store = StoryEventStore(tmp_path / "story.db")
    state = _state()
    store.create_session(state)

    with pytest.raises(SessionAlreadyExists):
        store.create_session(state)
~~~

- [ ] **Step 2: Run the tests and confirm the store is missing**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_event_store.py -v
~~~

Expected: FAIL during collection because storage exports do not exist.

- [ ] **Step 3: Implement the SQLite event store**

~~~python
# backend/src/story/storage/event_store.py

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from src.story.state.events import EventEnvelope, StoryEvent
from src.story.state.models import SessionState
from src.story.state.reducer import apply_events


class StoryStoreError(RuntimeError):
    """Base persistence error."""


class SessionAlreadyExists(StoryStoreError):
    pass


class SessionNotFound(StoryStoreError):
    pass


class RevisionConflict(StoryStoreError):
    pass


class StoryEventStore:
    def __init__(
        self,
        database_path: Path | str,
        snapshot_every: int = 20,
    ) -> None:
        if snapshot_every < 1:
            raise ValueError("snapshot_every must be positive")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_every = snapshot_every
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS story_sessions (
                    session_id TEXT PRIMARY KEY,
                    pack_id TEXT NOT NULL,
                    pack_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    snapshot_revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS story_events (
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, sequence),
                    FOREIGN KEY (session_id)
                        REFERENCES story_sessions(session_id)
                        ON DELETE CASCADE
                );
                """
            )

    def create_session(self, state: SessionState) -> None:
        if state.revision != 0:
            raise StoryStoreError("new session state must have revision 0")
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO story_sessions (
                        session_id,
                        pack_id,
                        pack_hash,
                        revision,
                        snapshot_revision,
                        snapshot_json,
                        created_at
                    ) VALUES (?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        state.session_id,
                        state.pack_id,
                        state.pack_hash,
                        state.model_dump_json(),
                        state.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SessionAlreadyExists(state.session_id) from exc

    def _load_with_connection(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> tuple[SessionState, sqlite3.Row]:
        row = connection.execute(
            """
            SELECT *
            FROM story_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise SessionNotFound(session_id)

        state = SessionState.model_validate_json(row["snapshot_json"])
        event_rows = connection.execute(
            """
            SELECT event_json
            FROM story_events
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence
            """,
            (session_id, row["snapshot_revision"]),
        ).fetchall()
        envelopes = [
            EventEnvelope.model_validate_json(item["event_json"])
            for item in event_rows
        ]
        state = apply_events(state, envelopes)
        if state.revision != row["revision"]:
            raise StoryStoreError(
                f"session {session_id} revision mismatch: "
                f"state={state.revision}, row={row['revision']}"
            )
        return state, row

    def load_session(self, session_id: str) -> SessionState:
        with self._connect() as connection:
            state, _ = self._load_with_connection(connection, session_id)
            return state

    def append(
        self,
        session_id: str,
        expected_revision: int,
        events: Iterable[StoryEvent],
    ) -> tuple[SessionState, tuple[EventEnvelope, ...]]:
        event_list = tuple(events)
        if not event_list:
            raise StoryStoreError("append requires at least one event")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            state, row = self._load_with_connection(connection, session_id)
            if state.revision != expected_revision:
                raise RevisionConflict(
                    f"session {session_id}: expected {expected_revision}, "
                    f"current {state.revision}"
                )

            envelopes = tuple(
                EventEnvelope(
                    session_id=session_id,
                    sequence=state.revision + index,
                    event=event,
                )
                for index, event in enumerate(event_list, start=1)
            )
            updated = apply_events(state, envelopes)

            connection.executemany(
                """
                INSERT INTO story_events (
                    session_id,
                    sequence,
                    event_id,
                    event_json
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        envelope.session_id,
                        envelope.sequence,
                        envelope.event_id,
                        envelope.model_dump_json(),
                    )
                    for envelope in envelopes
                ],
            )

            snapshot_revision = row["snapshot_revision"]
            snapshot_json = row["snapshot_json"]
            if updated.revision - snapshot_revision >= self.snapshot_every:
                snapshot_revision = updated.revision
                snapshot_json = updated.model_dump_json()

            connection.execute(
                """
                UPDATE story_sessions
                SET revision = ?,
                    snapshot_revision = ?,
                    snapshot_json = ?
                WHERE session_id = ?
                """,
                (
                    updated.revision,
                    snapshot_revision,
                    snapshot_json,
                    session_id,
                ),
            )
            connection.commit()
            return updated, envelopes
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_sessions(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id
                FROM story_sessions
                ORDER BY session_id
                """
            ).fetchall()
            return [row["session_id"] for row in rows]

    def event_count(self, session_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM story_events
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            return int(row["count"])
~~~

~~~python
# backend/src/story/storage/__init__.py

"""Persistence adapters for story sessions."""

from .event_store import (
    RevisionConflict,
    SessionAlreadyExists,
    SessionNotFound,
    StoryEventStore,
    StoryStoreError,
)

__all__ = [
    "RevisionConflict",
    "SessionAlreadyExists",
    "SessionNotFound",
    "StoryEventStore",
    "StoryStoreError",
]
~~~

- [ ] **Step 4: Run event-store tests**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_event_store.py -v
~~~

Expected: PASS, 5 tests passed.

- [ ] **Step 5: Run reducer and store tests together**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_reducer.py tests/test_story_event_store.py -v
~~~

Expected: PASS, 14 tests passed.

- [ ] **Step 6: Commit**

~~~bash
git add \
  backend/src/story/storage \
  backend/tests/test_story_event_store.py
git commit -m "feat: persist story events and snapshots"
~~~

---

### Task 9: Add A Model-Free Validation And Inspection CLI

**Files:**
- Create: \`backend/src/story/cli.py\`
- Create: \`backend/tests/test_story_cli.py\`
- Modify: \`backend/README.md\`

- [ ] **Step 1: Write failing CLI tests**

~~~python
# backend/tests/test_story_cli.py

import json
from pathlib import Path

from src.story.cli import main

PACK_DIR = (
    Path(__file__).resolve().parents[1]
    / "script_packs"
    / "cafe_mystery"
)


def test_validate_command_prints_compiled_summary(capsys):
    exit_code = main(["validate", str(PACK_DIR)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["pack_id"] == "cafe_mystery"
    assert len(output["pack_hash"]) == 64
    assert output["normal_endings"] >= 3
    assert output["fallback_endings"] >= 1


def test_init_and_inspect_session(tmp_path: Path, capsys):
    database = tmp_path / "story.db"

    assert main(
        [
            "init-session",
            str(PACK_DIR),
            "--database",
            str(database),
            "--session-id",
            "cli_session",
            "--seed",
            "17",
        ]
    ) == 0
    init_output = json.loads(capsys.readouterr().out)
    assert init_output["revision"] == 0

    assert main(
        [
            "inspect-session",
            "cli_session",
            "--database",
            str(database),
        ]
    ) == 0
    inspect_output = json.loads(capsys.readouterr().out)
    assert inspect_output["session_id"] == "cli_session"
    assert inspect_output["pack_id"] == "cafe_mystery"
    assert inspect_output["phase"] == "opening"


def test_validate_missing_pack_returns_nonzero(tmp_path: Path, capsys):
    assert main(["validate", str(tmp_path / "missing")]) == 2
    assert "script pack file not found" in capsys.readouterr().err
~~~

- [ ] **Step 2: Run the tests and confirm the CLI is missing**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_cli.py -v
~~~

Expected: FAIL during collection because \`src.story.cli\` does not exist.

- [ ] **Step 3: Implement the CLI**

~~~python
# backend/src/story/cli.py

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from src.story.script_pack import PackCompileError, compile_script_pack
from src.story.state import initial_session_state
from src.story.storage import (
    SessionAlreadyExists,
    SessionNotFound,
    StoryEventStore,
)


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.story.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("pack_path", type=Path)

    initialize = commands.add_parser("init-session")
    initialize.add_argument("pack_path", type=Path)
    initialize.add_argument("--database", type=Path, required=True)
    initialize.add_argument("--session-id", required=True)
    initialize.add_argument("--seed", type=int, required=True)

    inspect = commands.add_parser("inspect-session")
    inspect.add_argument("session_id")
    inspect.add_argument("--database", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            pack = compile_script_pack(args.pack_path)
            _print(
                {
                    "pack_id": pack.source.identity.id,
                    "pack_hash": pack.pack_hash,
                    "characters": len(pack.character_ids),
                    "facts": len(pack.fact_ids),
                    "goals": len(pack.goal_ids),
                    "normal_endings": len(
                        [
                            ending
                            for ending in pack.source.endings
                            if ending.type != "fallback"
                        ]
                    ),
                    "fallback_endings": len(
                        [
                            ending
                            for ending in pack.source.endings
                            if ending.type == "fallback"
                        ]
                    ),
                }
            )
            return 0

        if args.command == "init-session":
            pack = compile_script_pack(args.pack_path)
            state = initial_session_state(
                pack,
                session_id=args.session_id,
                session_seed=args.seed,
            )
            store = StoryEventStore(args.database)
            store.create_session(state)
            _print(
                {
                    "session_id": state.session_id,
                    "pack_id": state.pack_id,
                    "pack_hash": state.pack_hash,
                    "revision": state.revision,
                }
            )
            return 0

        store = StoryEventStore(args.database)
        state = store.load_session(args.session_id)
        _print(
            {
                "session_id": state.session_id,
                "pack_id": state.pack_id,
                "pack_hash": state.pack_hash,
                "revision": state.revision,
                "phase": state.world.phase.value,
                "scene_count": state.world.scene_count,
                "status": state.status.value,
            }
        )
        return 0
    except (
        PackCompileError,
        SessionAlreadyExists,
        SessionNotFound,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
~~~

- [ ] **Step 4: Run CLI tests**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/test_story_cli.py -v
~~~

Expected: PASS, 3 tests passed.

- [ ] **Step 5: Document the foundation commands**

Append this section to \`backend/README.md\`:

~~~~markdown
## V2 Story Foundation

The V2 domain can validate a script pack and initialize an event-sourced
session without an API key:

~~~bash
uv run python -m src.story.cli validate script_packs/cafe_mystery

uv run python -m src.story.cli init-session \
  script_packs/cafe_mystery \
  --database data/story.db \
  --session-id local_demo \
  --seed 17

uv run python -m src.story.cli inspect-session local_demo \
  --database data/story.db
~~~

The V1 FastAPI and WebSocket entry point remains unchanged during this
foundation milestone.
~~~~

- [ ] **Step 6: Run the real validation command**

Run:

~~~bash
cd backend
uv run python -m src.story.cli validate script_packs/cafe_mystery
~~~

Expected: exit 0 and JSON containing \`"pack_id": "cafe_mystery"\`, a 64-character \`pack_hash\`, at least 3 normal endings, and at least 1 fallback ending.

- [ ] **Step 7: Commit**

~~~bash
git add \
  backend/src/story/cli.py \
  backend/tests/test_story_cli.py \
  backend/README.md
git commit -m "feat: add v2 story validation CLI"
~~~

---

### Task 10: Verify The Foundation Milestone

**Files:**
- No source changes expected.

- [ ] **Step 1: Run every V2 foundation test**

Run:

~~~bash
cd backend
uv run --extra dev pytest \
  tests/test_story_package.py \
  tests/test_condition_dsl.py \
  tests/test_script_pack_models.py \
  tests/test_script_pack_compiler.py \
  tests/test_cafe_mystery_pack.py \
  tests/test_story_state.py \
  tests/test_story_reducer.py \
  tests/test_story_event_store.py \
  tests/test_story_cli.py -v
~~~

Expected: PASS, 46 tests passed.

- [ ] **Step 2: Run the complete backend regression suite**

Run:

~~~bash
cd backend
uv run --extra dev pytest tests/ -q
~~~

Expected: PASS, 99 tests passed: the existing 53 plus 46 V2 foundation tests.

- [ ] **Step 3: Run static checks on all new Python files**

Run:

~~~bash
cd backend
uv run --extra dev ruff check src/story tests/story_factories.py \
  tests/test_story_package.py tests/test_condition_dsl.py \
  tests/test_script_pack_models.py tests/test_script_pack_compiler.py \
  tests/test_cafe_mystery_pack.py tests/test_story_state.py \
  tests/test_story_reducer.py tests/test_story_event_store.py \
  tests/test_story_cli.py
~~~

Expected: exit 0 with no diagnostics.

- [ ] **Step 4: Prove pack validation is deterministic**

Run:

~~~bash
cd backend
first_hash="$(uv run python -m src.story.cli validate \
  script_packs/cafe_mystery | sed -n 's/.*"pack_hash": "\(.*\)".*/\1/p')"
second_hash="$(uv run python -m src.story.cli validate \
  script_packs/cafe_mystery | sed -n 's/.*"pack_hash": "\(.*\)".*/\1/p')"
test -n "$first_hash"
test "$first_hash" = "$second_hash"
~~~

Expected: exit 0.

- [ ] **Step 5: Confirm migration isolation**

Run:

~~~bash
git diff --exit-code aa94329 -- backend/src/main.py frontend/src
~~~

Expected: exit 0. The V1 API and frontend have not changed in this milestone.

- [ ] **Step 6: Confirm a clean worktree**

Run:

~~~bash
git status --short
~~~

Expected: no output.

---

## Foundation Completion Contract

The milestone is complete only when all of these statements have fresh evidence:

- A monolithic or safely modular ScriptPack compiles to a stable SHA-256 hash.
- Condition expressions never execute arbitrary Python.
- Invalid IDs, references, includes, actions, and conditions fail before session creation.
- At least one fallback contract is guaranteed true by the maximum scene count.
- Fixed, latent, derived, committed, evidenced, and revealed concepts are not conflated.
- Character knowledge is isolated from player visibility and world truth.
- Every state change is a typed event applied by one pure reducer.
- A failed event batch leaves both state and SQLite history unchanged.
- Revision conflicts prevent stale writers from appending.
- A new store instance reconstructs the same state from snapshot plus events.
- The real \`cafe_mystery\` pack validates without any fixed plot, beat list, or API key.
- All existing V1 backend tests still pass.

The next implementation plan starts from these verified interfaces and adds the
headless narrative runtime, including the standard-action transition model and
bounded reachability gate for normal endings. It must not bypass the compiler,
reducer, or event store, and V2 must not become the playable authority until
that remaining compiler gate passes.
