# backend/src/kernel/stubs.py
from __future__ import annotations

from src.domain.events import EventDatabase
from src.domain.options import ChoiceOption, PredictedConsequences
from src.domain.scene import SceneIntent
from src.domain.setting_pack import SettingPack
from src.domain.world_state import WorldState

# Tension >= this is treated as "high" (matches climax threshold in phase_tension).
_HIGH_TENSION = 8


class StubDirector:
    """Deterministic director: opening seed, then alternating goal focus."""

    async def generate_scene(
        self,
        state: WorldState,
        pack: SettingPack,
        memories: list[str],
    ) -> SceneIntent:
        char_ids = [c.id for c in pack.characters]
        goal_ids = [g.id for g in pack.goals]

        if state.steps == 0 and pack.opening_seed.strip():
            narration = pack.opening_seed.strip()
            focus = goal_ids[:1]
            mood = "calm"
            location_id = pack.world.locations[0].id if pack.world.locations else None
            speaking = char_ids[:1] if char_ids else []
            directives = {
                cid: f"向玩家打招呼，呼应开场气氛（目标：{focus[0] if focus else 'none'}）"
                for cid in speaking
            }
        else:
            # Alternate focus goals by step index.
            if goal_ids:
                idx = state.steps % len(goal_ids)
                focus = [goal_ids[idx]]
                # Optionally pair with next goal for mild multi-focus.
                if len(goal_ids) > 1 and state.steps % 2 == 1:
                    focus = [goal_ids[idx], goal_ids[(idx + 1) % len(goal_ids)]]
            else:
                focus = []

            focus_title = ""
            if focus:
                gmap = {g.id: g.title for g in pack.goals}
                focus_title = " / ".join(gmap.get(fid, fid) for fid in focus)

            narration = (
                f"第 {state.steps + 1} 步。气氛在咖啡馆中继续推进。"
                + (f" 焦点：{focus_title}。" if focus_title else "")
            )
            if memories:
                narration += f" （回忆：{memories[-1][:40]}）"

            mood = "tense" if state.tension >= _HIGH_TENSION else "neutral"
            location_id = pack.world.locations[0].id if pack.world.locations else None
            # Alternate who speaks.
            if char_ids:
                speaking = [char_ids[state.steps % len(char_ids)]]
            else:
                speaking = []
            directives = {
                cid: f"围绕焦点「{focus_title or '当前局势'}」说一句简短台词"
                for cid in speaking
            }

        wants_option = state.turns_since_last_option >= 3 or state.tension >= _HIGH_TENSION

        return SceneIntent(
            narration=narration,
            mood=mood,
            location_id=location_id,
            speaking_character_ids=speaking,
            dialogue_directives=directives,
            focus_goal_ids=focus,
            suggested_tension_delta=1 if wants_option else 0,
            wants_option=wants_option,
            decision_pressure=wants_option and state.tension >= _HIGH_TENSION,
            event_tags=["setup"] if state.steps == 0 else ["advance"],
            phase_hint=None,
        )


class StubCharacter:
    """Returns a short deterministic dialogue line."""

    async def generate_dialogue(
        self,
        char_id: str,
        directive: str,
        state: WorldState,
        pack: SettingPack,
        memories: list[str],
    ) -> str:
        name = char_id
        for c in pack.characters:
            if c.id == char_id:
                name = c.name
                break
        brief = (directive or "").strip()
        if brief:
            # Keep line short: name + ellipsis-style reaction.
            return f"{name}: ……（{brief[:24]}）"
        return f"{name}: ……"


class StubChoice:
    """Always three distinct options with different consequence fingerprints."""

    async def generate_options(
        self,
        state: WorldState,
        pack: SettingPack,
        scene: SceneIntent,
        memories: list[str],
    ) -> list[ChoiceOption]:
        return [
            ChoiceOption(
                id="stub_alice",
                text="相信艾丽丝，答应帮她",
                stance="bold",
                player_intent="ally_alice",
                predicted_consequences=PredictedConsequences(
                    flag_changes={"chose_alice": True},
                    relationship_deltas={"alice": {"trust": 10, "romance": 2}},
                    tension_delta=1,
                    tags=["chose_alice"],
                ),
                narrative_preview="你点头答应了艾丽丝",
            ),
            ChoiceOption(
                id="stub_bob",
                text="站在鲍勃一边，保持警惕",
                stance="cautious",
                player_intent="ally_bob",
                predicted_consequences=PredictedConsequences(
                    flag_changes={"chose_bob": True},
                    relationship_deltas={"bob": {"trust": 10}},
                    tension_delta=0,
                    tags=["chose_bob"],
                ),
                narrative_preview="你采纳了鲍勃的警告",
            ),
            ChoiceOption(
                id="stub_neutral",
                text="两边都不站队，继续观察",
                stance="neutral",
                player_intent="stay_neutral",
                predicted_consequences=PredictedConsequences(
                    flag_changes={"stayed_neutral": True},
                    relationship_deltas={
                        "alice": {"trust": -2},
                        "bob": {"trust": -2},
                    },
                    tension_delta=-1,
                    tags=["stayed_neutral"],
                ),
                narrative_preview="你选择暂时观望",
            ),
        ]


class StubMemory:
    """Returns the last k event summaries as plain strings."""

    async def recall(
        self,
        state: WorldState,
        pack: SettingPack,
        events: EventDatabase,
        k: int = 5,
    ) -> list[str]:
        recent = events.recent(k)
        out: list[str] = []
        for ev in recent:
            summary = _event_summary(ev)
            out.append(summary)
        return out


def _event_summary(ev) -> str:
    payload = ev.payload or {}
    if "summary" in payload and payload["summary"]:
        text = str(payload["summary"])
    elif "text" in payload and payload["text"]:
        text = str(payload["text"])
    elif "narration" in payload and payload["narration"]:
        text = str(payload["narration"])
    elif "dialogue" in payload and payload["dialogue"]:
        text = str(payload["dialogue"])
    else:
        text = f"{ev.type.value} step={ev.step}"
    return f"[{ev.step}] {ev.type.value}: {text}"
