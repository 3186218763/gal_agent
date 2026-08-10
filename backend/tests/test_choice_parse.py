"""Tests for Choice agent pure parse helper + kernel validation retry."""
from __future__ import annotations

import pytest

from src.agents.choice import build_choice_prompt, parse_choice_output
from src.content.setting_pack_loader import load_setting_pack
from src.domain.events import EventDatabase
from src.domain.options import ChoiceOption, PredictedConsequences
from src.domain.world_state import initial_world_state
from src.kernel.game_kernel import GameKernel
from src.kernel.stubs import StubCharacter, StubChoice, StubDirector, StubMemory
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_parse_choice_json():
    raw = (
        '{"options":[{"text":"相信她","stance":"bold",'
        '"predicted_consequences":{"flag_changes":{"a":true},'
        '"relationship_deltas":{"alice":{"trust":10}},'
        '"goal_effects":[],"tension_delta":1,"tags":["trust"]},'
        '"narrative_preview":"她微笑"}]}'
    )
    opts = parse_choice_output(raw)
    assert opts[0].text == "相信她"
    assert opts[0].stance == "bold"
    assert opts[0].predicted_consequences.flag_changes == {"a": True}
    assert opts[0].predicted_consequences.relationship_deltas["alice"]["trust"] == 10
    assert opts[0].predicted_consequences.tension_delta == 1
    assert opts[0].predicted_consequences.tags == ["trust"]
    assert opts[0].narrative_preview == "她微笑"


def test_parse_choice_strips_fences():
    raw = """```json
{"options":[
  {"text":"继续","stance":"bold",
   "predicted_consequences":{"flag_changes":{"go":true},"tags":["go"]},
   "narrative_preview":"前行"},
  {"text":"停下","stance":"cautious",
   "predicted_consequences":{"flag_changes":{"stop":true},"tags":["stop"]},
   "narrative_preview":"观望"}
]}
```"""
    opts = parse_choice_output(raw)
    assert len(opts) == 2
    assert opts[0].text == "继续"
    assert opts[1].text == "停下"


def test_parse_choice_bare_array():
    raw = '[{"text":"甲","predicted_consequences":{"flag_changes":{"a":1}}},{"text":"乙","predicted_consequences":{"flag_changes":{"b":1}}}]'
    opts = parse_choice_output(raw)
    assert len(opts) == 2
    assert opts[0].text == "甲"


def test_parse_choice_empty_and_garbage():
    assert parse_choice_output("") == []
    assert parse_choice_output("not json at all") == []
    assert parse_choice_output('{"options":"bad"}') == []
    assert parse_choice_output('{"options":[]}') == []


def test_parse_choice_skips_invalid_items():
    raw = (
        '{"options":['
        '{"text":"","predicted_consequences":{"flag_changes":{"x":true}}},'
        '{"text":"ok","predicted_consequences":{"flag_changes":{"y":true}}},'
        'null,'
        '"string",'
        '{"text":"also","predicted_consequences":{"flag_changes":{"z":true}}}'
        "]}"
    )
    opts = parse_choice_output(raw)
    assert [o.text for o in opts] == ["ok", "also"]


def test_parse_choice_embedded_json():
    raw = 'Here you go:\n{"options":[{"text":"嵌入","stance":"neutral","predicted_consequences":{"flag_changes":{"e":true}},"narrative_preview":"ok"}]}\nthanks'
    opts = parse_choice_output(raw)
    assert len(opts) == 1
    assert opts[0].text == "嵌入"


def test_build_choice_prompt_includes_context():
    text = build_choice_prompt(
        narration="雨夜巷口",
        mood="tense",
        phase="rising",
        tension=6,
        character_ids=["alice", "bob"],
        goal_ids=["ally_alice"],
        memories=["上次争吵"],
        focus_goal_ids=["ally_alice"],
    )
    assert "雨夜巷口" in text
    assert "alice" in text
    assert "ally_alice" in text
    assert "上次争吵" in text
    assert "rising" in text


class FlakyChoice:
    """Fails validation once, then returns StubChoice options."""

    def __init__(self) -> None:
        self.n = 0

    async def generate_options(self, state, pack, scene, memories):
        self.n += 1
        if self.n == 1:
            # Invalid: single option, empty consequences → fails validate
            return [
                ChoiceOption(
                    text="x",
                    predicted_consequences=PredictedConsequences(),
                )
            ]
        return await StubChoice().generate_options(state, pack, scene, memories)


@pytest.mark.asyncio
async def test_kernel_retries_choice_then_accepts():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    state = initial_world_state(pack, "s")
    flaky = FlakyChoice()
    kernel = GameKernel(
        pack,
        state,
        EventDatabase(),
        StubDirector(),
        StubCharacter(),
        flaky,
        StubMemory(),
    )
    # Force option generation path via public helper
    from src.domain.scene import SceneIntent

    scene = SceneIntent(narration="test", wants_option=True)
    opts = await kernel._generate_validated_options(scene, [])
    assert flaky.n == 2  # first invalid, second accepted
    assert len(opts) >= 2
    assert all(o.predicted_consequences.flag_changes or o.predicted_consequences.relationship_deltas for o in opts)
