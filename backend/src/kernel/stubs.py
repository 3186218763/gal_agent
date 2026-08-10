# backend/src/kernel/stubs.py
from __future__ import annotations

from src.domain.events import EventDatabase
from src.domain.options import ChoiceOption, GoalEffect, PredictedConsequences
from src.domain.scene import SceneIntent
from src.domain.setting_pack import SettingPack
from src.domain.world_state import WorldState

# Tension >= this is treated as "high" (matches climax threshold in phase_tension).
_HIGH_TENSION = 8


class StubDirector:
    """Deterministic director: first-scene hook, then alternating goal focus.

    Does **not** re-emit ``pack.opening_seed`` — ``GameKernel.start()`` already
    sends that once as the opening narration.
    """

    async def generate_scene(
        self,
        state: WorldState,
        pack: SettingPack,
        memories: list[str],
    ) -> SceneIntent:
        char_ids = [c.id for c in pack.characters]
        goal_ids = [g.id for g in pack.goals]

        # Alternate focus goals by step index (including step 0).
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

        if state.steps == 0:
            # First reading scene — distinct from opening_seed (emitted by start()).
            narration = (
                "门铃轻响，一位陌生女孩在门口张望，似乎在找人。"
                + (f" 焦点：{focus_title}。" if focus_title else "")
            )
            mood = "calm"
            event_tags = ["setup"]
            speaking = char_ids[:1] if char_ids else []
            directives = {
                cid: f"向玩家打招呼，呼应开场气氛（目标：{focus[0] if focus else 'none'}）"
                for cid in speaking
            }
        else:
            narration = (
                f"第 {state.steps + 1} 步。气氛在咖啡馆中继续推进。"
                + (f" 焦点：{focus_title}。" if focus_title else "")
            )
            if memories:
                narration += f" （回忆：{memories[-1][:40]}）"
            mood = "tense" if state.tension >= _HIGH_TENSION else "neutral"
            event_tags = ["advance"]
            if char_ids:
                speaking = [char_ids[state.steps % len(char_ids)]]
            else:
                speaking = []
            directives = {
                cid: f"围绕焦点「{focus_title or '当前局势'}」说一句简短台词"
                for cid in speaking
            }

        location_id = pack.world.locations[0].id if pack.world.locations else None
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
            event_tags=event_tags,
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
    """Three distinct options; repeated picks can complete ally routes.

    Strategy (chapter_01): always-pick index 0 → alice_route before max_steps
    (3× trust+25 + ally_alice δ0.4). Always-pick index 1 → bob_route.
    """

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
                    flag_changes={
                        "met_alice": True,
                        "talked_to_alice": True,
                        "chose_alice": True,
                    },
                    relationship_deltas={"alice": {"trust": 25, "romance": 2}},
                    goal_effects=[
                        GoalEffect(goal_id="ally_alice", delta_progress=0.4),
                    ],
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
                    flag_changes={
                        "chose_bob": True,
                        "questioned_alice": True,
                    },
                    relationship_deltas={"bob": {"trust": 25}},
                    goal_effects=[
                        GoalEffect(goal_id="ally_bob", delta_progress=0.4),
                    ],
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
                    goal_effects=[
                        GoalEffect(goal_id="learn_org_truth", delta_progress=0.15),
                    ],
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
