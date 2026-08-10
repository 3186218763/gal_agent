import pytest
from pathlib import Path

from src.agents.memory import RuleMemory
from src.content.setting_pack_loader import load_setting_pack
from src.domain.enums import EventType
from src.domain.events import EventDatabase, GameEvent
from src.domain.world_state import initial_world_state

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.asyncio
async def test_memory_rule_recall():
    db = EventDatabase()
    db.append(
        GameEvent(
            id="1",
            step=0,
            type=EventType.NARRATION,
            payload={"content": "A"},
        )
    )
    db.append(
        GameEvent(
            id="2",
            step=1,
            type=EventType.DIALOGUE,
            payload={"content": "B", "character": "alice"},
        )
    )
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    mems = await RuleMemory().recall(state, pack, db, k=2)
    assert len(mems) == 2
    assert "A" in mems[0]
    assert "B" in mems[1]
    assert "alice" in mems[1]


@pytest.mark.asyncio
async def test_memory_includes_state_summary():
    db = EventDatabase()
    db.append(
        GameEvent(
            id="1",
            step=0,
            type=EventType.NARRATION,
            payload={"content": "rain"},
        )
    )
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    state.summary = "Alice met Bob at the station"
    mems = await RuleMemory().recall(state, pack, db, k=1)
    assert any("Alice met Bob" in m for m in mems)
    assert any("rain" in m for m in mems)


@pytest.mark.asyncio
async def test_memory_recent_k_limits():
    db = EventDatabase()
    for i in range(5):
        db.append(
            GameEvent(
                id=str(i),
                step=i,
                type=EventType.NARRATION,
                payload={"content": f"e{i}"},
            )
        )
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    mems = await RuleMemory().recall(state, pack, db, k=2)
    assert len(mems) == 2
    assert "e3" in mems[0]
    assert "e4" in mems[1]
