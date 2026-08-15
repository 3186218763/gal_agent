"""Seam tests for issue 05: verbatim tail window + per-layer token budgets."""

from __future__ import annotations

from pathlib import Path

from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.segment_context import (
    ContextBudgets,
    build_segment_writer_context,
    estimate_tokens,
    recent_prose_window,
)
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.unified_segment import build_unified_context
from src.story.script_pack.compiler import compile_source
from src.story.state import (
    RECENT_PROSE_BLOCK_CAP,
    ProseBlockRecord,
    SceneSummaryRecord,
    initial_session_state,
)
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)
from tests.test_turn_orchestrator import _collect_events


def _state_with_ring(count: int, text_template: str = "Block {} unfolds slowly."):
    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    records = tuple(
        ProseBlockRecord(
            scene_id=f"scene_{index}",
            kind="narration",
            text=text_template.format(index),
        )
        for index in range(count)
    )
    return pack, state.model_copy(update={"recent_prose_blocks": records})


def test_window_fills_newest_to_oldest_until_budget_spent():
    _pack, state = _state_with_ring(40)
    budgets = ContextBudgets(recent_prose_token_budget=60)

    window = recent_prose_window(state, budgets)

    texts = [block["text"] for block in window["blocks"]]
    assert texts[-1] == "Block 39 unfolds slowly.", "the newest block anchors the seam"
    assert texts == sorted(texts, key=lambda t: int(t.split()[1])), "reading order kept"
    assert window["blocks_omitted"] == 40 - len(texts)
    spent = sum(estimate_tokens(text) for text in texts)
    assert spent <= 60 + estimate_tokens(texts[-1]), "budget respected up to the anchor block"


def test_window_returns_none_without_committed_prose():
    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    assert recent_prose_window(state) is None


def test_ring_is_capped_by_the_reducer(tmp_path: Path):
    """A long playthrough cannot grow the verbatim ring without bound."""
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "ring.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=2))
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=None,
        planner=FakePlanner(),
    )

    events = _collect_events(orchestrator.execute_turn(pack, "s1", 0, "ring-open", None))
    ready = next(data for t, data in events if t == "segment_ready")
    turn = 0
    while turn < 6:
        revision = ready["revision"]
        choice_id = ready["choices"][0]["id"]
        events = _collect_events(
            orchestrator.execute_turn(pack, "s1", revision, f"ring-{turn}", choice_id)
        )
        ready = next(data for t, data in events if t == "segment_ready")
        turn += 1

    state = store.load_session("s1")
    assert len(state.recent_prose_blocks) <= RECENT_PROSE_BLOCK_CAP
    # The ring holds the newest committed blocks, verbatim.
    last_committed_scene = [
        e.event for e in store.load_events("s1") if e.event.type == "scene_committed"
    ][-1]
    assert state.recent_prose_blocks[-1].scene_id == last_committed_scene.scene_id
    assert state.recent_prose_blocks[-1].text == last_committed_scene.blocks[-1].text


def test_digest_layer_caps_scene_summaries_independently():
    """A huge summaries history caps at its own quota while the verbatim
    window layer keeps spending its own (independent) budget."""
    pack, state = _state_with_ring(40)
    summaries = tuple(
        SceneSummaryRecord(scene_id=f"scene_{i}", summary=f"Scene {i} summary.")
        for i in range(40)
    )
    state = state.model_copy(update={"scene_summaries": summaries})
    budgets = ContextBudgets(scene_summary_max=24, recent_prose_token_budget=100000)

    ctx = build_unified_context(pack, state, compute_pacing_envelope(state, pack),
                                budgets=budgets)

    digest = ctx["event_trace"]
    assert len(digest["scene_summaries"]) == 24
    assert digest["scene_summaries"][-1]["scene_id"] == "scene_39", "newest summaries kept"
    assert digest["scene_summaries_omitted"] == 16
    # The summaries cap never squeezes the window: all 40 blocks still fit
    # under the generous window budget.
    assert len(ctx["recent_prose"]["blocks"]) == 40
    assert ctx["recent_prose"]["blocks_omitted"] == 0


def test_window_budget_never_squeezes_the_digest():
    """A tiny window budget trims the verbatim layer only; the digest keeps
    spending its own summary quota untouched."""
    pack, state = _state_with_ring(40)
    summaries = tuple(
        SceneSummaryRecord(scene_id=f"scene_{i}", summary=f"Scene {i}.")
        for i in range(30)
    )
    state = state.model_copy(update={"scene_summaries": summaries})
    budgets = ContextBudgets(recent_prose_token_budget=20)

    ctx = build_unified_context(pack, state, compute_pacing_envelope(state, pack),
                                budgets=budgets)

    assert len(ctx["event_trace"]["scene_summaries"]) == 24  # its own quota
    assert ctx["event_trace"]["scene_summaries_omitted"] == 6
    assert len(ctx["recent_prose"]["blocks"]) <= 3, "window trimmed to its own budget"
    assert ctx["recent_prose"]["blocks_omitted"] == 40 - len(ctx["recent_prose"]["blocks"])


def test_writer_context_carries_the_committed_tail(tmp_path: Path):
    """After a turn, the writer context's recent_prose holds the literal
    tail blocks the player just read — the seam for continuity."""
    import asyncio

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "seam.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=3))
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=None,
        planner=FakePlanner(),
    )

    _collect_events(orchestrator.execute_turn(pack, "s1", 0, "seam-open", None))

    state = store.load_session("s1")
    pacing = compute_pacing_envelope(state, pack)
    plan = asyncio.run(FakeDirector().plan_segment(pack, state, pacing))
    ctx = build_segment_writer_context(pack, state, plan)
    window = ctx["recent_prose"]
    committed_blocks = [
        block for event in store.load_events("s1") if event.event.type == "scene_committed"
        for block in event.event.blocks
    ]
    assert window["blocks"], "the opening prose reaches the writer verbatim"
    assert window["blocks"][-1]["text"] == committed_blocks[-1].text


def test_total_history_layers_stay_bounded_over_a_long_playthrough(tmp_path: Path):
    """Multi-segment play: summaries + digest + verbatim window together stay
    under a fixed token bound regardless of how many segments were played."""
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "bounded.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=4))
    orchestrator = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=None,
        planner=FakePlanner(),
    )

    events = _collect_events(orchestrator.execute_turn(pack, "s1", 0, "b-open", None))
    ready = next(data for t, data in events if t == "segment_ready")
    for turn in range(8):
        if ready["terminal"] == "ending":
            break
        events = _collect_events(
            orchestrator.execute_turn(pack, "s1", ready["revision"], f"b-{turn}",
                                      ready["choices"][0]["id"])
        )
        ready = next(data for t, data in events if t == "segment_ready")

    state = store.load_session("s1")
    ctx = build_unified_context(pack, state, compute_pacing_envelope(state, pack))

    window_tokens = sum(
        estimate_tokens(block["text"]) for block in ctx["recent_prose"]["blocks"]
    )
    summary_tokens = sum(
        estimate_tokens(item["summary"]) for item in ctx["event_trace"]["scene_summaries"]
    )
    # Independent caps: each layer within its own allowance...
    assert window_tokens <= 1000 + 4000, "window within its token budget (plus anchor block)"
    assert len(ctx["event_trace"]["scene_summaries"]) <= 24
    # ...and the total history payload bounded by a constant.
    assert window_tokens + summary_tokens < 12000
    assert len(state.recent_prose_blocks) <= RECENT_PROSE_BLOCK_CAP


def test_token_estimator_is_deterministic_and_cjk_aware():
    assert estimate_tokens("hello world!") == 3  # 12 latin chars / 4
    assert estimate_tokens("你好世界") == 4  # 4 CJK chars ~ 4 tokens
    assert estimate_tokens("你好 world") == 2 + 2  # 2 CJK + ceil(6/4)=2
