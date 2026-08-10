import pytest
from src.kernel.stubs import StubDirector, StubChoice, StubMemory, StubCharacter
from src.kernel.game_kernel import GameKernel
from src.content.setting_pack_loader import load_setting_pack
from src.domain.world_state import initial_world_state
from src.domain.events import EventDatabase
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


async def _drive_until_ending(kernel: GameKernel, choice_index: int = 0, cap: int = 40):
    """Always-pick ``choice_index`` until ending or safety cap."""
    ending_id = None
    for _ in range(cap):
        if kernel.state.ended:
            ending_id = kernel.state.ending_id
            break
        if kernel.state.pending_options:
            out = await kernel.apply_player_choice(choice_index)
        else:
            out = await kernel.advance_reading()
        for m in out:
            if m.get("type") == "ending":
                ending_id = m.get("ending_id") or kernel.state.ending_id
                break
        if kernel.state.ended:
            break
    return ending_id


@pytest.mark.asyncio
async def test_stubs_return_structured():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    mem = await StubMemory().recall(state, pack, EventDatabase(), k=3)
    scene = await StubDirector().generate_scene(state, pack, mem)
    assert scene.narration
    opts = await StubChoice().generate_options(state, pack, scene, mem)
    assert len(opts) >= 2
    # Stub choices must carry goal progress so multi-endings are reachable.
    assert any(o.predicted_consequences.goal_effects for o in opts)


@pytest.mark.asyncio
async def test_stub_director_does_not_reemit_opening_seed():
    """GameKernel.start() emits opening_seed once; first scene must differ."""
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    assert state.steps == 0
    seed = (pack.opening_seed or "").strip()
    assert seed

    start_msgs = await GameKernel(
        pack,
        state,
        EventDatabase(),
        StubDirector(),
        StubCharacter(),
        StubChoice(),
        StubMemory(),
    ).start()
    seed_msgs = [
        m for m in start_msgs if m.get("type") == "narration" and m.get("content") == seed
    ]
    assert len(seed_msgs) == 1

    scene = await StubDirector().generate_scene(state, pack, [])
    assert scene.narration
    assert scene.narration.strip() != seed
    assert seed not in scene.narration


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

    ending_id = await _drive_until_ending(kernel, choice_index=0)
    assert kernel.state.ended
    assert ending_id is not None


@pytest.mark.asyncio
async def test_always_pick_opt0_reaches_alice_route():
    """StubChoice opt0 stacks ally_alice + trust; three picks → alice_route."""
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    kernel = GameKernel(
        pack, state, EventDatabase(),
        StubDirector(), StubCharacter(), StubChoice(), StubMemory(),
    )
    await kernel.start()
    ending_id = await _drive_until_ending(kernel, choice_index=0)
    assert kernel.state.ended
    assert ending_id == "alice_route"
    assert kernel.state.ending_id == "alice_route"
    assert kernel.state.flags.get("met_alice") is True


@pytest.mark.asyncio
async def test_always_pick_opt1_reaches_bob_route():
    """StubChoice opt1 stacks ally_bob + bob trust → bob_route before fallback."""
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    kernel = GameKernel(
        pack, state, EventDatabase(),
        StubDirector(), StubCharacter(), StubChoice(), StubMemory(),
    )
    await kernel.start()
    ending_id = await _drive_until_ending(kernel, choice_index=1)
    assert kernel.state.ended
    assert ending_id == "bob_route"
    assert kernel.state.ending_id == "bob_route"


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


@pytest.mark.asyncio
async def test_ending_turn_does_not_emit_options():
    """When steps hit max_steps on a reading turn, emit ending without options."""
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    # One step shy of max so the next advance_reading ends the game.
    state.steps = pack.max_steps - 1
    state.turns_since_last_option = 10  # would otherwise favor options
    state.tension = 9
    events = EventDatabase()
    kernel = GameKernel(
        pack, state, events,
        StubDirector(), StubCharacter(), StubChoice(), StubMemory(),
    )
    out = await kernel.advance_reading()
    types = [m["type"] for m in out]
    assert "ending" in types
    assert "options" not in types
    assert kernel.state.ended
    assert not kernel.state.pending_options
    assert kernel.state.steps >= pack.max_steps
