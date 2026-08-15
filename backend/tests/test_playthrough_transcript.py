"""Seam tests for issue 07: incremental playthrough transcripts + CLI export."""

from __future__ import annotations

from pathlib import Path

from src.story.cli import main as cli_main
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.transcript import TranscriptWriter, render_events
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.script_pack.compiler import compile_source
from src.story.state import initial_session_state
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)
from tests.test_turn_orchestrator import _collect_events


def _orchestrator(store, transcript_writer):
    return TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        transcript_writer=transcript_writer,
    )


def _ready(events):
    return next(data for t, data in events if t == "segment_ready")


def test_options_come_from_decision_presented_not_scene_choices(tmp_path: Path):
    """The segment engine commits SceneCommitted without choices; option
    text at a decision point is joined from the DecisionPresented event."""
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "join.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=1))
    root = tmp_path / "playthroughs"
    orchestrator = _orchestrator(store, TranscriptWriter(root))

    _collect_events(orchestrator.execute_turn(pack, "s1", 0, "t-open", None))

    committed = store.load_events("s1")
    scene_events = [e.event for e in committed if e.event.type == "scene_committed"]
    decision_events = [e.event for e in committed if e.event.type == "decision_presented"]
    assert scene_events and all(not e.choices for e in scene_events)
    assert decision_events

    text = (root / "s1.md").read_text(encoding="utf-8")
    for choice in decision_events[0].choices:
        assert choice.label in text


def _play_to_ending(pack, store, orchestrator, session_id: str) -> None:
    """Opening → keep taking the first offered choice until the ending."""
    events = _collect_events(orchestrator.execute_turn(pack, session_id, 0, "t-open", None))
    ready = _ready(events)
    turn = 0
    while ready["terminal"] != "ending":
        revision = ready["revision"]
        choice_id = ready["choices"][0]["id"]
        events = _collect_events(
            orchestrator.execute_turn(pack, session_id, revision, f"t-{turn}", choice_id)
        )
        ready = _ready(events)
        turn += 1


def test_incremental_append_matches_full_rebuild(tmp_path: Path):
    """Every segment commit appends in stream order; rebuilding from the
    store afterwards yields byte-identical markdown."""
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "transcript.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=7))
    writer = TranscriptWriter(tmp_path / "playthroughs")
    orchestrator = _orchestrator(store, writer)

    _play_to_ending(pack, store, orchestrator, "s1")

    incremental = (tmp_path / "playthroughs" / "s1.md").read_text(encoding="utf-8")
    # The incremental file already reads as a complete story.
    assert "# Playthrough · s1" in incremental
    assert "### 抉择 ·" in incremental
    assert "> 已选：" in incremental
    assert "## 终章 ·" in incremental
    assert "—— 完（" in incremental

    # Rebuild from the store (the sole source of truth) into another path.
    rebuilt_path = writer.rebuild(
        "s1", store.load_events("s1"), path=tmp_path / "rebuilt.md"
    )
    assert rebuilt_path.read_text(encoding="utf-8") == incremental


def test_midplaythrough_exit_preserves_committed_part(tmp_path: Path):
    """Quitting after the first turn keeps the opening prose + options."""
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "partial.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=3))
    writer = TranscriptWriter(tmp_path / "playthroughs")
    orchestrator = _orchestrator(store, writer)

    _collect_events(orchestrator.execute_turn(pack, "s1", 0, "t-open", None))

    text = (tmp_path / "playthroughs" / "s1.md").read_text(encoding="utf-8")
    assert text.startswith("# Playthrough · s1")
    # Blocks and the first decision's options are already on disk.
    assert "The story continues in" in text
    assert "### 抉择 ·" in text


def test_transcript_failure_never_breaks_the_turn(tmp_path: Path):
    class BrokenWriter:
        def append_events(self, session_id, events):
            raise OSError("disk full")

    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / "broken.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=1))
    orchestrator = _orchestrator(store, BrokenWriter())

    events = _collect_events(orchestrator.execute_turn(pack, "s1", 0, "t-open", None))

    assert any(t == "segment_ready" for t, _data in events)
    assert store.load_session("s1").revision > 0


def test_cli_export_rebuilds_from_the_store(tmp_path: Path):
    pack = compile_source(budget_test_pack_dict())
    database = tmp_path / "export.db"
    store = StoryEventStore(database)
    store.create_session(initial_session_state(pack, "s1", session_seed=5))
    orchestrator = _orchestrator(store, TranscriptWriter(tmp_path / "playthroughs"))
    _play_to_ending(pack, store, orchestrator, "s1")

    out = tmp_path / "recovered.md"
    assert (
        cli_main(
            [
                "export-transcript",
                "s1",
                "--database",
                str(database),
                "--root",
                str(tmp_path / "fresh-root"),
                "--out",
                str(out),
            ]
        )
        == 0
    )

    assert out.read_text(encoding="utf-8") == (
        tmp_path / "playthroughs" / "s1.md"
    ).read_text(encoding="utf-8")
    # Default root rebuild also lands beside the incremental file.
    assert (
        cli_main(
            ["export-transcript", "s1", "--database", str(database), "--root", str(tmp_path)]
        )
        == 0
    )
    assert (tmp_path / "s1.md").exists()


def test_render_events_is_deterministic_and_skips_bookkeeping():
    from src.story.state import (
        DecisionPresented,
        FactCommitted,
        PresentedChoice,
        SceneCommitted,
    )
    from src.story.state.models import NarrativeBlock

    scene = SceneCommitted(
        scene_id="scene_1",
        location_id="cafe",
        present_character_ids=("alice",),
        blocks=(
            NarrativeBlock(kind="narration", text="The cafe hums."),
            NarrativeBlock(kind="dialogue", character_id="alice", text="You came back."),
        ),
        summary="A quiet return.",
    )
    decision = DecisionPresented(
        decision_id="dec_1",
        choices=(
            PresentedChoice(
                id="ask", action_id="ask", label="Ask about the notebook", intent="ask"
            ),
            PresentedChoice(
                id="wait",
                action_id="observe",
                label="Watch quietly",
                intent="observe",
                preview="Say nothing for now",
            ),
        ),
    )
    events = (FactCommitted(fact_id="f", value="v"), scene, decision)

    first = render_events(events)
    second = render_events(events)
    assert first == second
    assert "## scene_1" in first
    assert "*A quiet return.*" in first
    assert "**alice**：You came back." in first
    assert "1. Ask about the notebook" in first
    assert "2. Watch quietly —— *Say nothing for now*" in first
    assert "fact" not in first.split("## scene_1", 1)[1]  # bookkeeping skipped


def test_selection_line_renders_intent_with_option_id():
    from src.story.state import PlayerActionSelected

    text = render_events(
        (
            PlayerActionSelected(
                decision_id="dec_1",
                option_id="ask",
                action_id="ask",
                intent="ask directly",
                idempotency_key="k",
            ),
        )
    )
    assert text == "> 已选：`ask` —— ask directly\n\n"


def test_empty_replay_render_is_empty():
    assert render_events(()) == ""
