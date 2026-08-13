"""CLI autoplay tests using the authoritative TurnOrchestrator command flow.

The autoplay loop is exercised offline with deterministic fakes: opening,
choice selection, Pending Consequence recovery, and the ending all go
through the same ``execute_turn`` path as the HTTP ``/turns`` route.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.story.cli import _parser, autoplay
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.contracts import RuntimeGenerationUnavailable
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.script_pack import compile_source
from src.story.state import SessionStatus
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)


def live_test_pack():
    return compile_source(budget_test_pack_dict())


def _build_autoplay_runtime(tmp_path: Path, planner=None) -> TurnOrchestrator:
    store = StoryEventStore(tmp_path / "story.db")
    return (
        store,
        TurnOrchestrator(
            store=store,
            director=FakeDirector(),
            writer=FakeSegmentWriter(),
            guard=FakeGuard(),
            completion_judge=CompletionJudge(),
            planner=planner if planner is not None else FakePlanner(),
        ),
    )


def test_play_live_parser_accepts_required_arguments():
    args = _parser().parse_args(
        [
            "play-live",
            "script_packs/cafe_mystery",
            "--database",
            "data/live.db",
            "--session-id",
            "live-01",
            "--seed",
            "17",
            "--choice-strategy",
            "first",
        ]
    )
    assert args.command == "play-live"
    assert args.pack_path == Path("script_packs/cafe_mystery")
    assert args.database == Path("data/live.db")
    assert args.session_id == "live-01"
    assert args.seed == 17
    assert args.choice_strategy == "first"
    assert args.max_commands == 200


@pytest.mark.asyncio
async def test_autoplay_reaches_ended_state_with_fake_agents(tmp_path):
    store, orchestrator = _build_autoplay_runtime(tmp_path)
    result = await autoplay(
        pack=live_test_pack(),
        store=store,
        orchestrator=orchestrator,
        session_id="auto-01",
        seed=17,
        choice_strategy="first",
        max_commands=50,
    )
    assert result.status == SessionStatus.ENDED
    assert result.ending is not None


@pytest.mark.asyncio
async def test_autoplay_recovers_pending_consequence_without_reoffering_choice(tmp_path):
    """A transient generation failure leaves the committed choice pending;
    the next loop iteration resumes it with choice_id=None and the same
    idempotency key, and never commits the choice a second time."""

    class FailsOncePlanner(FakePlanner):
        def __init__(self):
            self.failed = False

        async def resolve_action(self, pack, state, choice):
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient model failure")
            return await super().resolve_action(pack, state, choice)

    store, orchestrator = _build_autoplay_runtime(tmp_path, planner=FailsOncePlanner())
    result = await autoplay(
        pack=live_test_pack(),
        store=store,
        orchestrator=orchestrator,
        session_id="auto-02",
        seed=17,
        choice_strategy="first",
        max_commands=50,
    )
    assert result.status == SessionStatus.ENDED

    selected = [e for e in store.load_events("auto-02") if e.event.type == "player_action_selected"]
    resolved = [e for e in store.load_events("auto-02") if e.event.type == "action_resolved"]
    assert len(selected) == len(resolved)


@pytest.mark.asyncio
async def test_autoplay_exhausts_attempts_on_persistent_generation_failure(tmp_path):
    class AlwaysFailingPlanner(FakePlanner):
        async def resolve_action(self, pack, state, choice):
            raise RuntimeError("persistent model failure")

    store, orchestrator = _build_autoplay_runtime(tmp_path, planner=AlwaysFailingPlanner())
    with pytest.raises(RuntimeGenerationUnavailable):
        await autoplay(
            pack=live_test_pack(),
            store=store,
            orchestrator=orchestrator,
            session_id="auto-03",
            seed=17,
            choice_strategy="first",
            max_commands=50,
            max_attempts=2,
        )

    # The committed choice is preserved and durable after the loop gives up.
    state = store.load_session("auto-03")
    assert state.pending_consequence is not None
    assert state.pending_decision is None
