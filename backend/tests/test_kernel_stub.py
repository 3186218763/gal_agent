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
