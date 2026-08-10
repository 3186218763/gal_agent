from __future__ import annotations

from pathlib import Path

import pytest

from src.story.cli import _parser, autoplay
from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    EndingDraft,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.service import RuntimeService
from src.story.script_pack import compile_source
from src.story.state import NarrativeBlock, SessionStatus
from src.story.storage import StoryEventStore
from tests.story_factories import minimal_script_pack_dict


def live_test_pack():
    return compile_source(minimal_script_pack_dict())


class FakePlanner:
    async def plan_scene(self, pack, state):
        scene_id = f"scene_{state.world.scene_count + 1:02d}"
        return ScenePlan(
            scene_id=scene_id,
            summary="Alice waits for the protagonist to choose.",
            location_id="cafe",
            present_character_ids=("alice",),
            terminal="decision",
            decision_id=f"decision_{state.world.scene_count + 1:02d}",
            choices=(
                ChoicePlan(option_id="ask", action_id="ask", intent="ask directly"),
                ChoicePlan(option_id="observe", action_id="observe", intent="watch carefully"),
            ),
        )

    async def resolve_action(self, pack, state, choice):
        return ActionResolution(action_id=choice.action_id, outcome="success")


class FakeWriter:
    async def write_scene(self, pack, state, plan):
        return SceneDraft(
            scene_id=plan.scene_id,
            blocks=(NarrativeBlock(kind="narration", text="The cafe hums quietly."),),
            choices=tuple(
                WrittenChoice(option_id=item.option_id, label=item.intent[:80])
                for item in plan.choices
            ),
        )

    async def write_ending(self, pack, state, ending):
        return EndingDraft(
            ending_id=ending.id,
            title=ending.title,
            blocks=(NarrativeBlock(kind="narration", text=f"Ending: {ending.title}"),),
        )


def fake_ending_runtime(tmp_path: Path) -> RuntimeService:
    store = StoryEventStore(tmp_path / "story.db")
    return RuntimeService(store, FakePlanner(), FakeWriter())


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
    result = await autoplay(
        pack=live_test_pack(),
        store=StoryEventStore(tmp_path / "story.db"),
        runtime=fake_ending_runtime(tmp_path),
        session_id="auto-01",
        seed=17,
        choice_strategy="first",
        max_commands=50,
    )
    assert result.status == SessionStatus.ENDED
    assert result.ending is not None
