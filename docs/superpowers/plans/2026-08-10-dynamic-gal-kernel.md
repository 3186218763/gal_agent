# Dynamic Gal Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace beat-script-driven gameplay with a state/goal-driven Game Kernel that loads a setting pack, generates reading turns, offers validated options only when rules allow, and ends via multi-goal multi-ending evaluation—no free text, no backtracking.

**Architecture:** Deterministic `GameKernel` owns the turn loop and all state writes. Rule modules handle phase/tension, option trigger, option validation, goal progress, and ending evaluation. OpenAI Agents SDK agents (Director, Character, Choice, Memory) return structured outputs only; a stub mode runs without API keys for tests. Setting packs live under `backend/scripts/<pack_id>/setting_pack.yaml`.

**Tech Stack:** Python 3.11+, Pydantic v2, PyYAML, pytest/pytest-asyncio, FastAPI + WebSocket, OpenAI Agents SDK (`openai-agents`), React frontend (message-compatible).

**Spec:** `docs/superpowers/specs/2026-08-10-dynamic-gal-agent-design.md`

## Global Constraints

- V1 is text-only; player may only send `player_choice` with `option_index`.
- No backtracking: Event DB is append-only; options disappear after choice.
- Agents never write World State directly; Kernel applies validated consequences.
- Ending conditions and option trigger are rule-evaluated (not pure LLM judgment).
- `max_steps` forces a fallback ending to prevent infinite sessions.
- Preview is narrative soft text only—never expose numeric deltas in UI.
- Prefer new modules under `backend/src/kernel/`, `backend/src/rules/`, `backend/src/domain/`; leave old `game_loop`/`plot.md` path unused once Kernel is wired, do not delete until Task 10.
- Tests must run without OpenAI API key using stub generators.
- Package imports use `src.` prefix when running from `backend/` (existing layout).

---

## File Structure (target)

```
backend/
  src/
    domain/                    # NEW pure data models
      __init__.py
      enums.py                 # Phase, EndingType, GoalStatus, EventType
      setting_pack.py          # SettingPack + nested models
      world_state.py           # WorldState, GoalRuntime, Relationship
      events.py                # GameEvent, EventDatabase
      options.py               # ChoiceOption, Consequences
      scene.py                 # SceneIntent, SceneBeat output
    rules/                     # NEW deterministic logic
      __init__.py
      phase_tension.py
      option_trigger.py
      option_validator.py
      goal_tracker.py
      ending_evaluator.py
    kernel/                    # NEW orchestrator
      __init__.py
      game_kernel.py
      ports.py                 # Protocols for Director/Character/Choice/Memory
      stubs.py                 # Deterministic stub generators for tests
    agents/                    # EXTEND existing
      director.py              # Slim: scene intent only
      character.py             # Keep + memory context
      choice.py                # NEW
      memory.py                # NEW
    content/
      setting_pack_loader.py   # NEW YAML loader
    core/
      state_manager.py         # EXTEND: persist WorldState + events
      # game_loop.py left in place until API switch
    main.py                    # Wire Kernel + pack_id
  scripts/
    chapter_01/
      setting_pack.yaml        # NEW primary content
  tests/
    conftest.py
    test_domain_models.py
    test_setting_pack_loader.py
    test_phase_tension.py
    test_option_trigger.py
    test_option_validator.py
    test_goal_tracker.py
    test_ending_evaluator.py
    test_kernel_stub.py
    test_state_manager_world.py
docs/superpowers/specs/2026-08-10-dynamic-gal-agent-design.md
```

---

### Task 1: Test harness and domain enums

**Files:**
- Create: `backend/tests/conftest.py`
- Create: `backend/src/domain/__init__.py`
- Create: `backend/src/domain/enums.py`
- Create: `backend/tests/test_domain_models.py` (enums section first)
- Modify: `backend/requirements.txt` (ensure pytest listed for local test installs)

**Interfaces:**
- Produces: `Phase`, `EndingType`, `GoalStatus`, `GoalType`, `EventType` string enums in `src.domain.enums`

- [ ] **Step 1: Ensure pytest is installable**

Add to `backend/requirements.txt` if missing:
```
pytest==8.3.3
pytest-asyncio==0.24.0
```

- [ ] **Step 2: Write failing enum import test**

```python
# backend/tests/test_domain_models.py
from src.domain.enums import Phase, EndingType, GoalStatus, GoalType, EventType


def test_phase_order():
    assert [p.value for p in Phase] == ["setup", "rising", "climax", "falling"]


def test_ending_types_include_fallback():
    assert EndingType.FALLBACK.value == "fallback"
    assert EndingType.VICTORY.value == "victory"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_domain_models.py -v`
Expected: FAIL (module not found)

- [ ] **Step 4: Implement enums**

```python
# backend/src/domain/enums.py
from enum import Enum


class Phase(str, Enum):
    SETUP = "setup"
    RISING = "rising"
    CLIMAX = "climax"
    FALLING = "falling"


class EndingType(str, Enum):
    VICTORY = "victory"
    BRANCH = "branch"
    GAME_OVER = "game_over"
    FALLBACK = "fallback"


class GoalStatus(str, Enum):
    LOCKED = "locked"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class GoalType(str, Enum):
    PURSUE = "pursue"
    AVOID = "avoid"
    DISCOVER = "discover"


class EventType(str, Enum):
    NARRATION = "narration"
    DIALOGUE = "dialogue"
    PLAYER_CHOICE = "player_choice"
    SYSTEM = "system"
```

```python
# backend/src/domain/__init__.py
from .enums import Phase, EndingType, GoalStatus, GoalType, EventType

__all__ = ["Phase", "EndingType", "GoalStatus", "GoalType", "EventType"]
```

```python
# backend/tests/conftest.py
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd backend && python -m pytest tests/test_domain_models.py -v`
Expected: PASS

- [ ] **Step 6: Commit** (if git available)

```bash
git add backend/src/domain backend/tests backend/requirements.txt
git commit -m "feat: add domain enums and pytest harness"
```

---

### Task 2: Core domain models (SettingPack, WorldState, Events, Options)

**Files:**
- Create: `backend/src/domain/setting_pack.py`
- Create: `backend/src/domain/world_state.py`
- Create: `backend/src/domain/events.py`
- Create: `backend/src/domain/options.py`
- Create: `backend/src/domain/scene.py`
- Modify: `backend/src/domain/__init__.py`
- Modify: `backend/tests/test_domain_models.py`

**Interfaces:**
- Produces:
  - `SettingPack` (Pydantic) with `characters`, `goals`, `endings`, `max_steps`, `opening_seed`, …
  - `WorldState` with `phase`, `tension`, `flags`, `relationships`, `goal_progress`, `steps`, `turns_since_last_option`, `pending_options`
  - `GoalRuntime(status, progress, evidence_event_ids)`
  - `GameEvent`, `EventDatabase.append/list/recent`
  - `PredictedConsequences`, `ChoiceOption`
  - `SceneIntent` (director output shape)

- [ ] **Step 1: Write failing model construction tests**

Append to `backend/tests/test_domain_models.py`:

```python
from src.domain.setting_pack import SettingPack, CharacterDef, GoalDef, EndingDef
from src.domain.world_state import WorldState, GoalRuntime, RelationshipState
from src.domain.events import EventDatabase, GameEvent
from src.domain.enums import EventType, GoalStatus, Phase
from src.domain.options import ChoiceOption, PredictedConsequences


def test_setting_pack_minimal():
    pack = SettingPack(
        pack_id="t",
        title="T",
        premise="p",
        characters=[
            CharacterDef(id="alice", name="Alice", personality="curious")
        ],
        goals=[GoalDef(id="g1", title="G", description="d")],
        endings=[
            EndingDef(
                id="e1",
                title="E",
                condition="steps >= 1",
                type="fallback",
                priority=1,
                content="end",
            )
        ],
        opening_seed="seed",
    )
    assert pack.max_steps == 24
    assert pack.characters[0].initial_relationship.trust == 50


def test_world_state_from_pack_defaults():
    # helper will be world_state.initial_world_state(pack, session_id)
    from src.domain.world_state import initial_world_state

    pack = SettingPack(
        pack_id="t",
        title="T",
        premise="p",
        characters=[
            CharacterDef(id="alice", name="Alice", personality="x", initial_relationship={"trust": 40, "romance": 0})
        ],
        goals=[GoalDef(id="g1", title="G", description="d")],
        endings=[
            EndingDef(id="e1", title="E", condition="steps >= 99", type="fallback", priority=1, content="end")
        ],
        opening_seed="seed",
        initial_flags={"game_started": False},
    )
    state = initial_world_state(pack, session_id="s1")
    assert state.session_id == "s1"
    assert state.phase == Phase.SETUP
    assert state.relationships["alice"].trust == 40
    assert state.goal_progress["g1"].status == GoalStatus.ACTIVE
    assert state.flags["game_started"] is False


def test_event_database_append_only():
    db = EventDatabase()
    e = GameEvent(id="1", step=0, type=EventType.NARRATION, payload={"content": "hi"})
    db.append(e)
    assert len(db.list()) == 1
    assert db.recent(1)[0].payload["content"] == "hi"


def test_choice_option_fingerprint_fields():
    opt = ChoiceOption(
        text="听她说完",
        stance="cautious",
        predicted_consequences=PredictedConsequences(
            flag_changes={"listened": True},
            relationship_deltas={"alice": {"trust": 5}},
        ),
        narrative_preview="她松了口气",
    )
    assert opt.text
    assert opt.predicted_consequences.flag_changes["listened"] is True
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd backend && python -m pytest tests/test_domain_models.py -v`
Expected: FAIL import errors

- [ ] **Step 3: Implement models**

```python
# backend/src/domain/setting_pack.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .enums import EndingType, GoalType


class RelationshipInit(BaseModel):
    trust: int = 50
    romance: int = 0


class CharacterDef(BaseModel):
    id: str
    name: str
    personality: str
    public_info: str = ""
    private_info: str = ""
    initial_relationship: RelationshipInit = Field(default_factory=RelationshipInit)


class LocationDef(BaseModel):
    id: str
    name: str
    tags: List[str] = Field(default_factory=list)


class FactionDef(BaseModel):
    id: str
    name: str
    description: str = ""


class WorldDef(BaseModel):
    locations: List[LocationDef] = Field(default_factory=list)
    factions: List[FactionDef] = Field(default_factory=list)


class GoalDef(BaseModel):
    id: str
    title: str
    description: str
    type: GoalType = GoalType.PURSUE
    weight: float = 1.0
    conflicts_with: List[str] = Field(default_factory=list)
    success_hint: str = ""
    suggests_flags: List[str] = Field(default_factory=list)


class EndingDef(BaseModel):
    id: str
    title: str
    condition: str
    type: EndingType = EndingType.BRANCH
    priority: int = 50
    content: str = ""


class SettingPack(BaseModel):
    pack_id: str
    title: str
    premise: str
    characters: List[CharacterDef]
    goals: List[GoalDef]
    endings: List[EndingDef]
    world: WorldDef = Field(default_factory=WorldDef)
    opening_seed: str = ""
    initial_flags: Dict[str, Any] = Field(default_factory=dict)
    max_steps: int = 24
```

```python
# backend/src/domain/world_state.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .enums import GoalStatus, Phase
from .options import ChoiceOption
from .setting_pack import SettingPack


class RelationshipState(BaseModel):
    trust: int = 50
    romance: int = 0


class GoalRuntime(BaseModel):
    status: GoalStatus = GoalStatus.ACTIVE
    progress: float = 0.0
    evidence_event_ids: List[str] = Field(default_factory=list)


class WorldState(BaseModel):
    session_id: str
    pack_id: str
    steps: int = 0
    phase: Phase = Phase.SETUP
    tension: int = 5
    flags: Dict[str, Any] = Field(default_factory=dict)
    relationships: Dict[str, RelationshipState] = Field(default_factory=dict)
    goal_progress: Dict[str, GoalRuntime] = Field(default_factory=dict)
    turns_since_last_option: int = 0
    summary: str = ""
    pending_options: List[ChoiceOption] = Field(default_factory=list)
    ended: bool = False
    ending_id: Optional[str] = None


def initial_world_state(pack: SettingPack, session_id: str) -> WorldState:
    relationships = {
        c.id: RelationshipState(
            trust=c.initial_relationship.trust,
            romance=c.initial_relationship.romance,
        )
        for c in pack.characters
    }
    goal_progress = {
        g.id: GoalRuntime(status=GoalStatus.ACTIVE, progress=0.0)
        for g in pack.goals
    }
    return WorldState(
        session_id=session_id,
        pack_id=pack.pack_id,
        flags=dict(pack.initial_flags),
        relationships=relationships,
        goal_progress=goal_progress,
    )
```

```python
# backend/src/domain/options.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GoalEffect(BaseModel):
    goal_id: str
    delta_progress: float = 0.0
    force_complete: bool = False


class PredictedConsequences(BaseModel):
    flag_changes: Dict[str, Any] = Field(default_factory=dict)
    relationship_deltas: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    goal_effects: List[GoalEffect] = Field(default_factory=list)
    tension_delta: int = 0
    tags: List[str] = Field(default_factory=list)


class ChoiceOption(BaseModel):
    id: str = ""
    text: str
    stance: str = "neutral"
    player_intent: str = ""
    predicted_consequences: PredictedConsequences = Field(default_factory=PredictedConsequences)
    narrative_preview: str = ""
```

```python
# backend/src/domain/events.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .enums import EventType, Phase


class GameEvent(BaseModel):
    id: str
    step: int
    type: EventType
    payload: Dict[str, Any] = Field(default_factory=dict)
    phase: Optional[Phase] = None
    tension: Optional[int] = None
    tags: List[str] = Field(default_factory=list)


class EventDatabase(BaseModel):
    events: List[GameEvent] = Field(default_factory=list)

    def append(self, event: GameEvent) -> None:
        self.events.append(event)

    def list(self) -> List[GameEvent]:
        return list(self.events)

    def recent(self, n: int) -> List[GameEvent]:
        return self.events[-n:] if n > 0 else []
```

```python
# backend/src/domain/scene.py
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class SceneIntent(BaseModel):
    """Director structured output."""
    narration: str
    mood: str = "neutral"
    location_id: Optional[str] = None
    speaking_character_ids: List[str] = Field(default_factory=list)
    dialogue_directives: Dict[str, str] = Field(default_factory=dict)  # char_id -> brief
    focus_goal_ids: List[str] = Field(default_factory=list)
    suggested_tension_delta: int = 0
    wants_option: bool = False
    decision_pressure: bool = False
    event_tags: List[str] = Field(default_factory=list)
    phase_hint: Optional[str] = None
```

Update `domain/__init__.py` to re-export public types used by kernel/rules.

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd backend && python -m pytest tests/test_domain_models.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/src/domain backend/tests/test_domain_models.py
git commit -m "feat: add setting pack, world state, event, option domain models"
```

---

### Task 3: Setting pack YAML loader

**Files:**
- Create: `backend/src/content/__init__.py`
- Create: `backend/src/content/setting_pack_loader.py`
- Create: `backend/scripts/chapter_01/setting_pack.yaml` (minimal valid pack for tests; full flavor in Task 11)
- Create: `backend/tests/test_setting_pack_loader.py`

**Interfaces:**
- Consumes: `SettingPack`
- Produces: `load_setting_pack(scripts_dir: Path | str, pack_id: str) -> SettingPack`
- Raises: `FileNotFoundError` if missing; `ValidationError` if invalid

- [ ] **Step 1: Write failing loader test**

```python
# backend/tests/test_setting_pack_loader.py
from pathlib import Path
import pytest
from src.content.setting_pack_loader import load_setting_pack


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_load_chapter_01_pack():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    assert pack.pack_id == "chapter_01"
    assert len(pack.characters) >= 2
    assert len(pack.goals) >= 2
    assert len(pack.endings) >= 3
    assert any(e.type.value == "fallback" or "steps" in e.condition for e in pack.endings)


def test_missing_pack_raises():
    with pytest.raises(FileNotFoundError):
        load_setting_pack(SCRIPTS, "no_such_pack")
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement loader + minimal YAML**

```python
# backend/src/content/setting_pack_loader.py
from pathlib import Path
import yaml
from src.domain.setting_pack import SettingPack


def load_setting_pack(scripts_dir: Path | str, pack_id: str) -> SettingPack:
    base = Path(scripts_dir) / pack_id
    path = base / "setting_pack.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Setting pack not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid setting pack YAML: {path}")
    return SettingPack.model_validate(data)
```

Create `backend/scripts/chapter_01/setting_pack.yaml` with alice/bob, goals `ally_alice`/`ally_bob`/`learn_org_truth`, endings including fallback on `steps >= max` style condition, `max_steps: 24`, `opening_seed` from existing plot intro. (Full text adapted from existing `metadata.yaml` + premise.)

Example structure (fill real Chinese content from existing chapter):

```yaml
pack_id: "chapter_01"
title: "邂逅"
premise: |
  玩家被卷入与神秘组织有关的线索。
world:
  locations:
    - id: cafe
      name: "街角咖啡馆"
      tags: [public, first_meet]
characters:
  - id: alice
    name: "艾丽丝"
    personality: "外向、好奇、有点粗心"
    initial_relationship: {trust: 50, romance: 0}
  - id: bob
    name: "鲍勃"
    personality: "谨慎、理性、善于分析"
    initial_relationship: {trust: 40, romance: 0}
goals:
  - id: ally_alice
    title: "与艾丽丝结盟"
    description: "取得信任并同意合作"
    weight: 1.0
    conflicts_with: [ally_bob]
  - id: ally_bob
    title: "站在鲍勃一边"
    description: "采纳警告并保持距离"
    weight: 1.0
    conflicts_with: [ally_alice]
  - id: learn_org_truth
    title: "摸清组织一角"
    description: "获得可靠情报"
    type: discover
    weight: 0.8
endings:
  - id: alice_route
    title: "信任的开始"
    priority: 100
    type: victory
    condition: "goals.ally_alice.completed and alice_trust >= 70"
    content: "你选择相信艾丽丝……"
  - id: bob_route
    title: "理性的选择"
    priority: 90
    type: victory
    condition: "goals.ally_bob.completed and bob_trust >= 60"
    content: "你站在鲍勃一边……"
  - id: bad_trust
    title: "信任破裂"
    priority: 80
    type: game_over
    condition: "alice_trust < 20 and met_alice"
    content: "艾丽丝离开了……"
  - id: timeout_fallback
    title: "未竟的午后"
    priority: 1
    type: fallback
    condition: "steps >= 24"
    content: "咖啡馆打烊了，故事暂时告一段落。"
initial_flags: {}
opening_seed: |
  午后的咖啡馆里，你独自坐在靠窗位置……
max_steps: 24
```

- [ ] **Step 4: Run tests — PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: load setting_pack.yaml into SettingPack"
```

---

### Task 4: Phase & Tension rules

**Files:**
- Create: `backend/src/rules/__init__.py`
- Create: `backend/src/rules/phase_tension.py`
- Create: `backend/tests/test_phase_tension.py`

**Interfaces:**
- Produces:
  - `update_tension(current: int, suggested_delta: int, event_tags: list[str], phase: Phase) -> int`
  - `maybe_advance_phase(state: WorldState, pack: SettingPack, major_choice: bool = False) -> Phase`
  - `clamp_phase_hint(current: Phase, hint: str | None) -> Phase` (at most +1 step)

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_phase_tension.py
from src.domain.enums import Phase
from src.rules.phase_tension import update_tension, maybe_advance_phase, clamp_phase_hint
from src.domain.world_state import WorldState, GoalRuntime
from src.domain.enums import GoalStatus


def test_tension_clamped_and_tags():
    assert update_tension(5, 2, ["confrontation"], Phase.RISING) >= 7
    assert update_tension(2, -5, ["calm"], Phase.SETUP) == 1
    assert update_tension(9, 5, [], Phase.CLIMAX) == 10


def test_phase_hint_max_one_step():
    assert clamp_phase_hint(Phase.SETUP, "climax") == Phase.RISING
    assert clamp_phase_hint(Phase.RISING, "setup") == Phase.RISING  # no backward for V1


def test_advance_setup_to_rising_by_steps():
    state = WorldState(session_id="s", pack_id="t", steps=3, phase=Phase.SETUP)
    assert maybe_advance_phase(state, pack=None) == Phase.RISING
```

For `maybe_advance_phase`, if `pack` is unused for step thresholds, pass `None` or a minimal pack; implement thresholds as constants matching the spec:

```python
SETUP_TO_RISING_STEPS = 3
RISING_TO_CLIMAX_STEPS = 10
CLIMAX_TO_FALLING_STEPS = 16
```

Also advance on progress/tension thresholds from spec.

- [ ] **Step 2: Implement `phase_tension.py`**

```python
# backend/src/rules/phase_tension.py
from __future__ import annotations
from typing import List, Optional, TYPE_CHECKING
from src.domain.enums import Phase

if TYPE_CHECKING:
    from src.domain.world_state import WorldState
    from src.domain.setting_pack import SettingPack

PHASE_ORDER = [Phase.SETUP, Phase.RISING, Phase.CLIMAX, Phase.FALLING]

TAG_DELTA = {
    "confrontation": 2,
    "reveal": 1,
    "calm": -1,
}


def update_tension(
    current: int,
    suggested_delta: int,
    event_tags: List[str],
    phase: Phase,
) -> int:
    delta = max(-2, min(2, suggested_delta))
    for t in event_tags:
        delta += TAG_DELTA.get(t, 0)
    value = current + delta
    if phase == Phase.CLIMAX:
        value = max(value, 6)
    return max(1, min(10, value))


def clamp_phase_hint(current: Phase, hint: Optional[str]) -> Phase:
    if not hint:
        return current
    try:
        target = Phase(hint)
    except ValueError:
        return current
    ci = PHASE_ORDER.index(current)
    ti = PHASE_ORDER.index(target)
    if ti <= ci:
        return current
    return PHASE_ORDER[min(ci + 1, ti)]


def maybe_advance_phase(
    state: "WorldState",
    pack: Optional["SettingPack"] = None,
    major_choice: bool = False,
) -> Phase:
    phase = state.phase
    max_progress = max((g.progress for g in state.goal_progress.values()), default=0.0)
    any_completed = any(g.status.value == "completed" for g in state.goal_progress.values())

    if phase == Phase.SETUP and (
        state.steps >= 3 or max_progress >= 0.2 or state.tension >= 5
    ):
        return Phase.RISING
    if phase == Phase.RISING and (
        state.steps >= 10 or max_progress >= 0.6 or state.tension >= 8
    ):
        return Phase.CLIMAX
    if phase == Phase.CLIMAX and (
        state.steps >= 16 or major_choice or any_completed
    ):
        return Phase.FALLING
    return phase
```

- [ ] **Step 3: Tests PASS**

- [ ] **Step 4: Commit** `feat: phase and tension rule module`

---

### Task 5: Option trigger scoring

**Files:**
- Create: `backend/src/rules/option_trigger.py`
- Create: `backend/tests/test_option_trigger.py`

**Interfaces:**
- Produces: `should_trigger_option(*, turns_since_last_option, tension, phase, wants_option, decision_pressure, threshold=50, min_cooldown=2) -> dict` with keys `should_trigger: bool`, `score: int`, `reasons: list[str]`

- [ ] **Step 1: Write tests covering cooldown, climax density, long drought**

```python
from src.domain.enums import Phase
from src.rules.option_trigger import should_trigger_option


def test_hard_cooldown_blocks():
    r = should_trigger_option(
        turns_since_last_option=1,
        tension=10,
        phase=Phase.CLIMAX,
        wants_option=True,
        decision_pressure=True,
    )
    assert r["should_trigger"] is False


def test_climax_high_tension_triggers():
    r = should_trigger_option(
        turns_since_last_option=3,
        tension=9,
        phase=Phase.CLIMAX,
        wants_option=True,
        decision_pressure=True,
    )
    assert r["should_trigger"] is True


def test_setup_low_tension_usually_no():
    r = should_trigger_option(
        turns_since_last_option=3,
        tension=3,
        phase=Phase.SETUP,
        wants_option=False,
        decision_pressure=False,
    )
    assert r["should_trigger"] is False


def test_long_drought_boost():
    r = should_trigger_option(
        turns_since_last_option=8,
        tension=4,
        phase=Phase.RISING,
        wants_option=False,
        decision_pressure=False,
    )
    assert r["score"] >= 50 or r["should_trigger"] is True
```

- [ ] **Step 2: Implement scoring per spec §4.3**

```python
# backend/src/rules/option_trigger.py
from __future__ import annotations
from typing import Any, Dict, List
from src.domain.enums import Phase


def should_trigger_option(
    *,
    turns_since_last_option: int,
    tension: int,
    phase: Phase,
    wants_option: bool,
    decision_pressure: bool,
    threshold: int = 50,
    min_cooldown: int = 2,
) -> Dict[str, Any]:
    reasons: List[str] = []
    if turns_since_last_option < min_cooldown:
        return {
            "should_trigger": False,
            "score": -999,
            "reasons": ["hard_cooldown"],
        }

    score = 0
    if turns_since_last_option <= 3:
        score += 0
    elif turns_since_last_option <= 5:
        score += 15
        reasons.append("turns_mid")
    else:
        score += 25
        reasons.append("turns_long")

    if tension >= 9:
        score += 35
    elif tension >= 7:
        score += 25
    elif tension >= 5:
        score += 10

    phase_pts = {
        Phase.SETUP: -10,
        Phase.RISING: 0,
        Phase.CLIMAX: 20,
        Phase.FALLING: 10,
    }[phase]
    score += phase_pts
    reasons.append(f"phase:{phase.value}:{phase_pts}")

    if wants_option:
        score += 15
        reasons.append("director_wants")
    if decision_pressure:
        score += 20
        reasons.append("decision_pressure")
    if turns_since_last_option >= 7:
        score += 20
        reasons.append("drought_boost")

    return {
        "should_trigger": score >= threshold,
        "score": score,
        "reasons": reasons,
    }
```

- [ ] **Step 3: PASS + commit** `feat: option trigger scoring rules`

---

### Task 6: Option validator + fallback templates

**Files:**
- Create: `backend/src/rules/option_validator.py`
- Create: `backend/tests/test_option_validator.py`

**Interfaces:**
- Produces:
  - `consequence_fingerprint(opt: ChoiceOption) -> str`
  - `validate_options(options: list[ChoiceOption], *, valid_character_ids, valid_goal_ids, recent_choice_tags) -> ValidationResult`
  - `fallback_options() -> list[ChoiceOption]`
- `ValidationResult`: `valid: bool`, `issues: list[str]`, `options: list[ChoiceOption]` (filtered/assigned ids)

- [ ] **Step 1: Tests**

```python
from src.domain.options import ChoiceOption, PredictedConsequences, GoalEffect
from src.rules.option_validator import validate_options, fallback_options


def _opt(text, flags=None, rel=None, goal=None):
    return ChoiceOption(
        text=text,
        predicted_consequences=PredictedConsequences(
            flag_changes=flags or {},
            relationship_deltas=rel or {},
            goal_effects=[GoalEffect(goal_id=goal, delta_progress=0.1)] if goal else [],
        ),
    )


def test_rejects_too_few():
    r = validate_options(
        [_opt("a", flags={"x": True})],
        valid_character_ids={"alice"},
        valid_goal_ids={"g1"},
        recent_choice_tags=[],
    )
    assert r.valid is False


def test_rejects_identical_consequences():
    a = _opt("one", flags={"x": True})
    b = _opt("two", flags={"x": True})
    r = validate_options(
        [a, b],
        valid_character_ids=set(),
        valid_goal_ids=set(),
        recent_choice_tags=[],
    )
    assert r.valid is False
    assert any("假选择" in i or "fingerprint" in i.lower() or "差分" in i for i in r.issues)


def test_accepts_distinct():
    a = _opt("相信她", flags={"trust_alice": True}, rel={"alice": {"trust": 10}})
    b = _opt("保持警惕", flags={"wary": True}, rel={"alice": {"trust": -5}})
    r = validate_options(
        [a, b],
        valid_character_ids={"alice"},
        valid_goal_ids=set(),
        recent_choice_tags=[],
    )
    assert r.valid is True
    assert 2 <= len(r.options) <= 4


def test_fallback_has_consequences():
    fb = fallback_options()
    assert len(fb) >= 2
    assert all(
        o.predicted_consequences.flag_changes
        or o.predicted_consequences.relationship_deltas
        or o.predicted_consequences.goal_effects
        for o in fb
    )
```

- [ ] **Step 2: Implement validator**

Key logic:
- n in 2..4
- each option has non-empty consequences
- fingerprints unique (json dumps sorted of flags + deltas + goal ids)
- text length 2..50
- relationship character ids ⊆ valid set; goal ids ⊆ valid set
- assign `id` as `opt_0`…
- on failure set `valid=False` with issues; caller retries then uses `fallback_options()`

```python
def fallback_options() -> list[ChoiceOption]:
    return [
        ChoiceOption(
            id="fb_0",
            text="继续追问细节",
            stance="bold",
            predicted_consequences=PredictedConsequences(
                flag_changes={"asked_more": True},
                tags=["ask"],
            ),
            narrative_preview="你想知道更多",
        ),
        ChoiceOption(
            id="fb_1",
            text="暂时观望",
            stance="cautious",
            predicted_consequences=PredictedConsequences(
                flag_changes={"watched": True},
                tension_delta=-1,
                tags=["wait"],
            ),
            narrative_preview="你先不表态",
        ),
        ChoiceOption(
            id="fb_2",
            text="转移话题",
            stance="withdraw",
            predicted_consequences=PredictedConsequences(
                flag_changes={"changed_subject": True},
                tags=["deflect"],
            ),
            narrative_preview="气氛稍缓",
        ),
    ]
```

- [ ] **Step 3: PASS + commit** `feat: option validator and fallback templates`

---

### Task 7: Goal tracker + ending evaluator

**Files:**
- Create: `backend/src/rules/goal_tracker.py`
- Create: `backend/src/rules/ending_evaluator.py`
- Create: `backend/tests/test_goal_tracker.py`
- Create: `backend/tests/test_ending_evaluator.py`

**Interfaces:**
- `apply_goal_effects(state: WorldState, pack: SettingPack, effects: list[GoalEffect]) -> WorldState` (returns updated copy or mutates carefully—prefer return new model via `model_copy`)
- `apply_consequences(state, pack, consequences: PredictedConsequences) -> WorldState`
- `evaluate_endings(pack, state) -> EndingDef | None` (highest priority true condition)
- Safe expression eval supporting: `and`/`or`/`&&`/`||`, comparisons, `goals.<id>.completed`, `{char}_trust`, flags as bare names, `steps`

- [ ] **Step 1: Goal tracker tests**

```python
from src.domain.world_state import initial_world_state
from src.domain.options import PredictedConsequences, GoalEffect
from src.rules.goal_tracker import apply_consequences
from src.content.setting_pack_loader import load_setting_pack
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_apply_relationship_and_goal_progress():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    new_state = apply_consequences(
        state,
        pack,
        PredictedConsequences(
            flag_changes={"met_alice": True},
            relationship_deltas={"alice": {"trust": 20}},
            goal_effects=[GoalEffect(goal_id="ally_alice", delta_progress=0.5)],
        ),
    )
    assert new_state.flags["met_alice"] is True
    assert new_state.relationships["alice"].trust == 70
    assert new_state.goal_progress["ally_alice"].progress == 0.5
```

Also test `force_complete=True` sets status completed and lowers conflict goal weight effect: when A completes, B's effective note—V1: if A completed, set conflicting goals' progress uncapped but status remains active; optional: multiply is director-side. Spec: 降权 only in director ranking—tracker may set `progress = min(1.0, p)` and `status=COMPLETED` at `progress >= 1.0` or force_complete.

- [ ] **Step 2: Ending evaluator tests**

```python
def test_ending_priority_and_goal_completed():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    state.relationships["alice"].trust = 75
    state.goal_progress["ally_alice"].status = GoalStatus.COMPLETED
    state.goal_progress["ally_alice"].progress = 1.0
    from src.rules.ending_evaluator import evaluate_endings
    ending = evaluate_endings(pack, state)
    assert ending is not None
    assert ending.id == "alice_route"


def test_fallback_on_max_steps():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    state.steps = 24
    ending = evaluate_endings(pack, state)
    assert ending is not None
    assert ending.id == "timeout_fallback"
```

- [ ] **Step 3: Implement safe condition evaluation**

Reuse spirit of `core/ending_evaluator.py` but:
- Normalize `&&` → `and`, `||` → `or`
- Build context: flags, `alice_trust`, `goals_ally_alice_completed` OR nested access via replacing `goals.X.completed` with boolean before eval
- Prefer `ast` boolean eval or restricted `eval` with empty builtins

```python
def _build_context(state: WorldState) -> dict:
    ctx = dict(state.flags)
    ctx["steps"] = state.steps
    ctx["phase"] = state.phase.value
    for cid, rel in state.relationships.items():
        ctx[f"{cid}_trust"] = rel.trust
        ctx[f"{cid}_romance"] = rel.romance
    for gid, gr in state.goal_progress.items():
        ctx[f"goals_{gid}_completed"] = gr.status == GoalStatus.COMPLETED
    return ctx


def _normalize(condition: str) -> str:
    c = condition.replace("&&", " and ").replace("||", " or ")
    # goals.ally_alice.completed -> goals_ally_alice_completed
    import re
    c = re.sub(r"goals\.([a-zA-Z0-9_]+)\.completed", r"goals_\1_completed", c)
    return c
```

- [ ] **Step 4: PASS + commit** `feat: goal progress apply and multi-ending evaluation`

---

### Task 8: Kernel ports + stubs

**Files:**
- Create: `backend/src/kernel/__init__.py`
- Create: `backend/src/kernel/ports.py`
- Create: `backend/src/kernel/stubs.py`
- Create: `backend/tests/test_kernel_stub.py` (partial—full loop in Task 9)

**Interfaces:**
- Protocols (typing.Protocol):
  - `DirectorPort.generate_scene(state, pack, memories: list[str]) -> SceneIntent` (async)
  - `CharacterPort.generate_dialogue(char_id, directive, state, pack, memories) -> str` (async)
  - `ChoicePort.generate_options(state, pack, scene: SceneIntent, memories) -> list[ChoiceOption]` (async)
  - `MemoryPort.recall(state, pack, events: EventDatabase, k: int = 5) -> list[str]` (async)

- `StubDirector`, `StubCharacter`, `StubChoice`, `StubMemory` deterministic

- [ ] **Step 1: Write port + stub unit test**

```python
import pytest
from src.kernel.stubs import StubDirector, StubChoice, StubMemory, StubCharacter
from src.content.setting_pack_loader import load_setting_pack
from src.domain.world_state import initial_world_state
from src.domain.events import EventDatabase
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.asyncio
async def test_stubs_return_structured():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    mem = await StubMemory().recall(state, pack, EventDatabase(), k=3)
    scene = await StubDirector().generate_scene(state, pack, mem)
    assert scene.narration
    opts = await StubChoice().generate_options(state, pack, scene, mem)
    assert len(opts) >= 2
```

- [ ] **Step 2: Implement ports and stubs**

StubDirector: first step uses `opening_seed`; later alternates focus goals; sets `wants_option` when `turns_since_last_option >= 3` or tension high.

StubChoice: always returns 3 distinct options affecting alice/bob trust and flags `chose_alice` / `chose_bob` / `stayed_neutral` with different fingerprints.

StubMemory: returns last k event summaries as strings.

StubCharacter: returns `f'{name}: ……'` short line from directive.

- [ ] **Step 3: PASS + commit** `feat: kernel agent ports and deterministic stubs`

---

### Task 9: GameKernel turn loop (stub mode)

**Files:**
- Create: `backend/src/kernel/game_kernel.py`
- Modify: `backend/tests/test_kernel_stub.py`

**Interfaces:**
- `class GameKernel`:
  - `__init__(self, pack, state, events, director, character, choice, memory)`
  - `async def start(self) -> list[dict]`  # outbound messages for opening
  - `async def advance_reading(self) -> list[dict]`  # one reading turn; may include options message
  - `async def apply_player_choice(self, option_index: int) -> list[dict]`
  - Message dicts match frontend types: `narration`, `dialogue`, `options`, `state_update`, `ending`, `error`

**Behavior:**
1. Memory.recall  
2. Director.generate_scene  
3. Emit narration; for each speaking char, Character dialogue  
4. Append events; update tension; maybe advance phase; steps += 1; turns_since_last_option += 1  
5. Trigger check; if yes, Choice → validate (retry once) → fallback → set pending_options → emit options (text + preview only)  
6. On choice: apply_consequences; reset turns_since_last_option=0; clear pending; emit state_update; evaluate endings; if none and steps>=max_steps force fallback ending eval  

- [ ] **Step 1: Integration test without API**

```python
@pytest.mark.asyncio
async def test_kernel_reaches_ending_with_stubs():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    events = EventDatabase()
    kernel = GameKernel(
        pack, state, events,
        StubDirector(), StubCharacter(), StubChoice(), StubMemory(),
    )
    msgs = await kernel.start()
    assert any(m["type"] == "game_start" for m in msgs)

    # Drive until ending or safety cap
    ended = False
    for _ in range(40):
        if kernel.state.ended:
            ended = True
            break
        if kernel.state.pending_options:
            out = await kernel.apply_player_choice(0)
        else:
            out = await kernel.advance_reading()
        if any(m["type"] == "ending" for m in out):
            ended = True
            break
    assert ended
    assert kernel.state.ended
```

Also test: after options emitted, `advance_reading` should error or no-op until choice (prefer error message type).

- [ ] **Step 2: Implement GameKernel**

Keep methods small; pure rule calls from Tasks 4–7; never call OpenAI in this task.

Options message shape:
```python
{
  "type": "options",
  "options": [
    {"id": o.id, "text": o.text, "preview": o.narrative_preview}
  ]
}
```

- [ ] **Step 3: PASS + commit** `feat: GameKernel reading/choice loop with stubs`

---

### Task 10: Persist WorldState + wire FastAPI/WebSocket

**Files:**
- Modify: `backend/src/core/state_manager.py` (add WorldState JSON save/load methods or new `WorldStateStore`)
- Prefer Create: `backend/src/core/world_store.py` to avoid breaking old GameState if still imported
- Modify: `backend/src/main.py`
- Create: `backend/tests/test_world_store.py`
- Modify: frontend only if field names break (`pack_id` vs `chapter_id`)

**Interfaces:**
- `WorldStore.create(session_id, pack_id) -> WorldState`
- `WorldStore.save(state, events)`
- `WorldStore.load(session_id) -> tuple[WorldState, EventDatabase] | None`
- API: `CreateSessionRequest.pack_id: str = "chapter_01"` (accept alias `chapter_id` for compat)
- WebSocket: instantiate Kernel with stubs if `GAL_USE_STUBS=1` or no `OPENAI_API_KEY`, else real agents (agents may still be stubs until Tasks 11–14)

- [ ] **Step 1: Test world store roundtrip**

```python
def test_world_store_roundtrip(tmp_path):
    from src.core.world_store import WorldStore
    from src.content.setting_pack_loader import load_setting_pack
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    store = WorldStore(tmp_path)
    state = store.create_session("sid", pack)
    state.steps = 2
    events = EventDatabase()
    store.save("sid", state, events)
    loaded_state, loaded_events = store.load("sid")
    assert loaded_state.steps == 2
    assert loaded_state.pack_id == pack.pack_id
```

- [ ] **Step 2: Implement WorldStore** saving `state.model_dump()` + `events.model_dump()` to `data/{session_id}.json`

- [ ] **Step 3: Rewrite main.py game websocket**

```python
# Pseudocode structure for main.py WS handler
@app.websocket("/ws/game/{session_id}")
async def websocket_game(websocket: WebSocket, session_id: str):
    await websocket.accept()
    state, events = world_store.load(session_id)
    pack = load_setting_pack(SCRIPTS_DIR, state.pack_id)
    kernel = build_kernel(pack, state, events)  # stubs or agents
    for msg in await kernel.start():
        await websocket.send_json(msg)
    # auto-play reading until options or ending
    while not kernel.state.ended:
        if kernel.state.pending_options:
            data = await websocket.receive_json()
            if data.get("type") != "player_choice":
                await websocket.send_json({"type": "error", "message": "only player_choice allowed"})
                continue
            outs = await kernel.apply_player_choice(int(data["option_index"]))
        else:
            outs = await kernel.advance_reading()
        for m in outs:
            await websocket.send_json(m)
        world_store.save(session_id, kernel.state, kernel.events)
        if kernel.state.ended:
            break
```

- [ ] **Step 4: Manual smoke** (optional): `GAL_USE_STUBS=1 uvicorn ...` + frontend start game

- [ ] **Step 5: Commit** `feat: persist world state and wire kernel to websocket`

---

### Task 11: Director Agent (SDK) + factory switch

**Files:**
- Rewrite: `backend/src/agents/director.py` to implement `DirectorPort` returning `SceneIntent`
- Create: `backend/src/kernel/agent_factory.py` choosing stubs vs real
- Modify: `backend/src/agents/__init__.py`

**Interfaces:**
- `SdkDirector.generate_scene(...) -> SceneIntent`
- Use Agents SDK `Agent` + `Runner.run` with instructions from pack premise, goals, phase, memories
- Parse JSON to `SceneIntent`; on failure raise or return safe default narration

- [ ] **Step 1: Unit test with mocked Runner** (if hard, test instruction builder pure function)

```python
from src.agents.director import build_director_prompt
from src.domain.enums import Phase

def test_director_prompt_includes_goals_and_phase():
    text = build_director_prompt(
        premise="p",
        phase=Phase.RISING,
        tension=6,
        goals_summary="ally_alice:0.2",
        memories=["m1"],
        opening_seed="seed",
        steps=0,
    )
    assert "ally_alice" in text
    assert "rising" in text
```

- [ ] **Step 2: Implement SdkDirector**

Instructions must say: invent scenes within world; serve focus goals; do not write player options; output JSON matching SceneIntent fields; `suggested_tension_delta` in [-2,2].

- [ ] **Step 3: `build_ports(use_stubs: bool) -> Ports`**

- [ ] **Step 4: Commit** `feat: SDK Director agent for scene intent`

---

### Task 12: Character Agent (SDK)

**Files:**
- Modify: `backend/src/agents/character.py` to implement `CharacterPort`
- Keep per-character agent or single agent with character card in prompt

- [ ] **Step 1: Test prompt includes personality and trust**

```python
from src.agents.character import build_character_prompt

def test_character_prompt():
    p = build_character_prompt(
        name="艾丽丝",
        personality="冲动",
        trust=55,
        directive="试探玩家",
        memories=["刚坐下"],
    )
    assert "艾丽丝" in p and "冲动" in p
```

- [ ] **Step 2: Implement async generate_dialogue**

- [ ] **Step 3: Commit** `feat: character agent port for dialogue`

---

### Task 13: Choice Agent (SDK) + validator retry in kernel

**Files:**
- Create: `backend/src/agents/choice.py`
- Modify: `backend/src/kernel/game_kernel.py` ensure retry loop uses ChoicePort twice then fallback

- [ ] **Step 1: Test parse helper**

```python
from src.agents.choice import parse_choice_output

def test_parse_choice_json():
    raw = '''{"options":[{"text":"相信她","stance":"bold","predicted_consequences":{"flag_changes":{"a":true},"relationship_deltas":{"alice":{"trust":10}},"goal_effects":[],"tension_delta":1,"tags":["trust"]},"narrative_preview":"她微笑"}]}'''
    opts = parse_choice_output(raw)
    assert opts[0].text == "相信她"
```

- [ ] **Step 2: Implement SdkChoice with strict JSON schema in instructions**

- [ ] **Step 3: Kernel already retries validate—confirm integration test with a FakeChoice that fails once**

```python
class FlakyChoice:
    def __init__(self):
        self.n = 0
    async def generate_options(self, state, pack, scene, memories):
        self.n += 1
        if self.n == 1:
            return [ChoiceOption(text="x", predicted_consequences=PredictedConsequences())]  # invalid
        return await StubChoice().generate_options(state, pack, scene, memories)
```

- [ ] **Step 4: Commit** `feat: choice agent with validation retry`

---

### Task 14: Memory Agent (light)

**Files:**
- Create: `backend/src/agents/memory.py`
- Default V1: rule recall of last K events + optional LLM summarize every N steps into `state.summary`

- [ ] **Step 1: Test rule recall**

```python
@pytest.mark.asyncio
async def test_memory_rule_recall():
    from src.agents.memory import RuleMemory
    db = EventDatabase()
    db.append(GameEvent(id="1", step=0, type=EventType.NARRATION, payload={"content": "A"}))
    db.append(GameEvent(id="2", step=1, type=EventType.DIALOGUE, payload={"content": "B", "character": "alice"}))
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    mems = await RuleMemory().recall(state, pack, db, k=2)
    assert len(mems) == 2
```

- [ ] **Step 2: Implement RuleMemory as MemoryPort; optional SdkMemorySummarizer later—V1 RuleMemory is enough if factory uses it always for recall**

- [ ] **Step 3: Commit** `feat: memory recall from event database`

---

### Task 15: Frontend alignment + README

**Files:**
- Modify: `frontend/src/types.ts` — add optional `phase`/`tension` on state_update; `preview` already exists
- Modify: `frontend/src/api.ts` — send `pack_id` (keep `chapter_id` alias if backend accepts both)
- Modify: `README.md` — document setting pack, `GAL_USE_STUBS`, architecture pointer to spec
- Modify: `docs/superpowers/specs/2026-08-10-dynamic-gal-agent-design.md` footer — link plan path

- [ ] **Step 1: Ensure options render `preview` under text** in `Game.tsx` if not already

- [ ] **Step 2: Update README quick start for kernel mode**

- [ ] **Step 3: Manual checklist**
  - Stub mode full play to fallback or route ending
  - With API key: one short session (optional)

- [ ] **Step 4: Commit** `docs: align frontend and README with dynamic kernel`

---

## Spec Coverage Checklist

| Spec section | Task(s) |
|--------------|---------|
| Kernel + dual data layer | 2, 8, 9 |
| Setting pack | 2, 3, 15 |
| Multi goals + multi endings | 7, 3 |
| Phase / tension / trigger | 4, 5 |
| Choice + validator + preview | 6, 13 |
| Director / Character / Memory | 11, 12, 14 |
| No free input / no backtrack | 9, 10 |
| max_steps fallback | 3, 7, 9 |
| API/WebSocket | 10 |
| Stub tests without API | 8, 9 |
| Example pack chapter_01 | 3 |

## Self-Review Notes

- No TBD placeholders in task steps.
- Types consistent: `WorldState`, `ChoiceOption`, `SceneIntent`, `PredictedConsequences`, `GoalEffect`.
- Old `game_loop.py` not deleted in V1; superseded at API wire (Task 10).
- Commit steps assume git may need `git init` at repo root first if still missing.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-10-dynamic-gal-kernel.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks  
2. **Inline Execution** — Execute tasks in this session with executing-plans checkpoints  

Which approach?
