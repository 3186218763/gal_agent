"""Seam tests for issue 02: pending choice intent reaching the writer context."""

from __future__ import annotations

from pathlib import Path

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.contracts import (
    ChoicePlan,
    PacingEnvelope,
    ScenePlan,
    SegmentPlan,
)
from src.story.runtime.segment_context import (
    build_segment_writer_context,
    player_choice_view,
)
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.unified_segment import build_unified_context
from src.story.script_pack.compiler import compile_source
from src.story.state import PresentedChoice, StoryPhase, initial_session_state
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)
from tests.story_factories import minimal_script_pack_dict
from tests.test_turn_orchestrator import _collect_events, _valid_unified_output


def _pacing() -> PacingEnvelope:
    return PacingEnvelope(
        phase=StoryPhase.EXPLORATION,
        scene_count=5,
        min_scenes=8,
        max_scenes=20,
        reserved_resolution_scenes=3,
        remaining_budget=15,
        can_end=False,
        must_end=False,
        in_convergence=False,
        max_new_threads=2,
        quiet_scene_allowance=1,
        target_block_range=(8, 25),
    )


def _pending_choice() -> PresentedChoice:
    return PresentedChoice(
        id="opt_a",
        action_id="ask",
        label="Ask directly",
        intent="confront Alice about the notebook",
        target_character_id="alice",
        stance_axis="trust",
        stance_value="wary",
        accepted_risk="Alice may shut the conversation down",
        potential_obligation_kind="promise_kept",
    )


def _plan() -> SegmentPlan:
    return SegmentPlan(
        segment_id="seg_01",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="Alice reacts to the question.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id="dec_01",
                choices=(
                    ChoicePlan(option_id="opt_a", action_id="ask", intent="ask directly"),
                    ChoicePlan(option_id="opt_b", action_id="observe", intent="watch carefully"),
                ),
            ),
        ),
        terminal="decision",
    )


def test_unified_context_carries_confirmation_and_structured_fields():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)

    ctx = build_unified_context(pack, state, _pacing(), pending_choice=_pending_choice())

    view = ctx["player_choice"]
    # text layer: the confirmation sentence names the label and the intent
    assert "Ask directly" in view["confirmation"]
    assert "confront Alice about the notebook" in view["confirmation"]
    assert "wary" in view["confirmation"]
    # structured layer: the full Choice Meaning fields travel alongside
    assert view["structured"]["action_id"] == "ask"
    assert view["structured"]["stance_axis"] == "trust"
    assert view["structured"]["accepted_risk"] == "Alice may shut the conversation down"
    assert view["structured"]["potential_obligation_kind"] == "promise_kept"


def test_unified_context_without_pending_choice_has_no_player_choice_section():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)

    ctx = build_unified_context(pack, state, _pacing())

    assert "player_choice" not in ctx


def test_writer_context_injects_and_retires_player_choice():
    pack = compile_source(minimal_script_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    plan = _plan()

    injected = build_segment_writer_context(pack, state, plan, pending_choice=_pending_choice())
    assert injected["player_choice"]["confirmation"].startswith("The player just chose")

    retired = build_segment_writer_context(pack, state, plan)
    assert "player_choice" not in retired


def test_confirmation_sentence_follows_pack_language():
    raw = minimal_script_pack_dict()
    raw["identity"]["language"] = "zh-CN"
    pack = compile_source(raw)

    view = player_choice_view(_pending_choice(), pack)

    assert view["confirmation"].startswith("玩家刚刚选择了「")
    assert "「Ask directly」" in view["confirmation"]
    assert "玩家已接受的风险" in view["confirmation"]


def test_pipeline_delivers_choice_to_immediate_segment_only(tmp_path: Path):
    """The fake writer receives the choice for the segment right after the
    selection, and nothing stale for the segment after that."""
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "intent_pipeline.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))

    seen: list[PresentedChoice | None] = []

    class RecordingWriter(FakeSegmentWriter):
        async def write_segment(self, pack, state, plan, *, pending_choice=None):
            seen.append(pending_choice)
            return await super().write_segment(pack, state, plan, pending_choice=pending_choice)

    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=RecordingWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
    )

    # opening: no choice pending
    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
    ready = next(data for t, data in opening if t == "segment_ready")
    assert seen[-1] is None

    # the segment directly following the selection sees the full choice
    choice_id = ready["choices"][0]["id"]
    _collect_events(orch.execute_turn(pack, "s1", ready["revision"], f"cmd-c1-{choice_id}", choice_id))
    delivered = seen[-1]
    assert delivered is not None
    assert delivered.action_id
    assert delivered.intent
    assert delivered.id == choice_id

    # the next decision's own segment carries only ITS choice — never the
    # previous one (anti choice-reversal: stale instructions must retire)
    state = store.load_session("s1")
    next_choice = state.pending_decision.choices[-1].id if state.pending_decision else None
    assert next_choice is not None, "the consequence segment must end in a decision"
    _collect_events(
        orch.execute_turn(pack, "s1", state.revision, f"cmd-c2-{next_choice}", next_choice)
    )
    assert seen[-1] is not None
    assert seen[-1].id == next_choice
    assert seen[-1].id != choice_id


def test_unified_agent_receives_pending_choice(tmp_path: Path):
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "intent_unified.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))

    seen: list[PresentedChoice | None] = []

    class RecordingAgent:
        async def generate(self, pack, state, pacing, *, rejection_notes=(), pending_choice=None):
            seen.append(pending_choice)
            return await _valid_unified_output(pack, state, pacing)

    orch = TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        unified_agent=RecordingAgent(),
    )

    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
    ready = next(data for t, data in opening if t == "segment_ready")
    assert seen == [None]

    choice_id = ready["choices"][0]["id"]
    _collect_events(orch.execute_turn(pack, "s1", ready["revision"], f"cmd-c-{choice_id}", choice_id))
    assert seen[-1] is not None and seen[-1].id == choice_id
