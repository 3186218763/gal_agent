import pytest
from src.kernel.stubs import StubDirector, StubChoice, StubMemory, StubCharacter
from src.kernel.game_kernel import GameKernel
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
    # Parked Task 4 fix: SETUP must not skip immediately
    assert kernel.state.tension == 3
    assert kernel.state.phase.value == "setup"

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


@pytest.mark.asyncio
async def test_pending_options_blocks_advance():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    events = EventDatabase()
    kernel = GameKernel(
        pack, state, events,
        StubDirector(), StubCharacter(), StubChoice(), StubMemory(),
    )
    await kernel.start()

    # Drive until options appear (or safety cap)
    saw_options = False
    for _ in range(20):
        if kernel.state.pending_options:
            saw_options = True
            break
        out = await kernel.advance_reading()
        if any(m["type"] == "options" for m in out):
            saw_options = True
            break
        if kernel.state.ended:
            break

    assert saw_options, "expected options within 20 reading turns"
    assert kernel.state.pending_options

    blocked = await kernel.advance_reading()
    assert any(m["type"] == "error" for m in blocked)
    assert kernel.state.pending_options  # still pending
    assert not kernel.state.ended
