"""Yokai pack v3 walkthrough: the authored Beat Map drives three routes to
three different endings without a single improvisation turn."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.script_pack.compiler import compile_script_pack
from src.story.state import initial_session_state
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeScenePerformer,
)
from tests.test_turn_orchestrator import _collect_events

YOKAI_PACK_DIR = Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school"


def _orchestrator(store: StoryEventStore) -> TurnOrchestrator:
    return TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=None,
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=FakePlanner(),
        scene_performer=FakeScenePerformer(),
    )


def _walk(pack, tmp_path, route_prefs: list[str], session_id: str) -> dict:
    """Drive the orchestrator turn by turn, picking authored route options."""
    store = StoryEventStore(tmp_path / f"{session_id}.db")
    store.create_session(initial_session_state(pack, session_id, session_seed=7))
    orch = _orchestrator(store)

    revision = 0
    events = _collect_events(orch.execute_turn(pack, session_id, revision, "cmd-open", None))
    ready = next(data for t, data in events if t == "segment_ready")
    for _turn in range(12):
        if ready["ending"] is not None:
            break
        options = [choice["id"] for choice in (ready["choices"] or [])]
        assert options, "decision segment surfaced no authored choices"
        picked = next(opt for opt in route_prefs if opt in options)
        revision = ready["revision"]
        events = _collect_events(
            orch.execute_turn(pack, session_id, revision, f"cmd-c-{picked}", picked)
        )
        ready = next(data for t, data in events if t == "segment_ready")
    return {
        "ready": ready,
        "events": [env.event for env in store.load_events(session_id)],
    }


def _completed_beats(events) -> list[str]:
    return [event.beat_id for event in events if event.type == "beat_completed"]


def _committed_value(events, fact_id: str) -> str | None:
    for event in events:
        if event.type == "fact_committed" and event.fact_id == fact_id:
            return event.value
    return None


def _is_revealed(events, fact_id: str) -> bool:
    return any(
        event.type == "fact_revealed" and event.fact_id == fact_id for event in events
    )


@pytest.fixture()
def yokai_pack():
    return compile_script_pack(YOKAI_PACK_DIR)


class TestYokaiBeatMapCompiles:
    def test_pack_has_full_structure_and_seeds(self, yokai_pack):
        assert len(yokai_pack.beat_ids) == 14
        assert yokai_pack.ending_seed_ids == frozenset(
            {"seed_lantern_home", "seed_lively_week", "seed_quiet_week"}
        )


class TestYokaiWalkthrough:
    @pytest.mark.parametrize(
        ("route_prefs", "club_future", "commit_beat", "ending_title"),
        [
            (
                ["go_with_hiyori", "back_guide", "rehearse_with_hiyori", "present_together"],
                "multilingual_campus_guide",
                "b_commit_club_guide",
                "普通的下一周",
            ),
            (
                ["go_with_chika", "back_broadcast", "help_chika_edit", "present_together"],
                "after_school_radio_special",
                "b_commit_club_radio",
                "心跳的放学后直播",
            ),
            (
                ["politely_decline", "ask_mio_first", "build_props_with_mio", "present_together"],
                "shrine_and_campus_open_day",
                "b_commit_club_openday",
                "纸签归处",
            ),
        ],
        ids=["hiyori-route", "chika-route", "mio-route"],
    )
    def test_route_reaches_its_ending(
        self, yokai_pack, tmp_path, route_prefs, club_future, commit_beat, ending_title
    ):
        result = _walk(yokai_pack, tmp_path, route_prefs, "s_route")

        ready = result["ready"]
        assert ready["ending"] is not None, "walkthrough never reached an ending"
        assert ready["ending"]["title"] == ending_title
        assert ready["terminal"] == "ending"

        # every mandatory beat fired, in act order, with only the two
        # off-route conditional commits skipped
        assert _completed_beats(result["events"]) == [
            "b_transfer_intro",
            "b_recruit_invitations",
            "b_clubroom_first_visit",
            "b_first_yokai",
            "b_club_direction_debate",
            "b_shrine_afternoon",
            "b_hiyori_ema",
            commit_beat,
            "b_friday_plan",
            "b_paper_fox_truth",
            "b_review_day",
            "b_finale",
        ]

        # the chosen route fixed the canon: club direction + fox truth
        assert _committed_value(result["events"], "club_future") == club_future
        assert (
            _committed_value(result["events"], "paper_fox_sender")
            == "mio_returned_it_by_mistake"
        )
        assert _is_revealed(result["events"], "club_future")
        assert _is_revealed(result["events"], "paper_fox_sender")
