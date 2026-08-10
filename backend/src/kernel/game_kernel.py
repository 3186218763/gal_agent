# backend/src/kernel/game_kernel.py
"""Deterministic game turn loop. Agents are injected via ports (stubs or real)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from src.domain.enums import EndingType, EventType, Phase
from src.domain.events import EventDatabase, GameEvent
from src.domain.options import ChoiceOption
from src.domain.scene import SceneIntent
from src.domain.setting_pack import EndingDef, SettingPack
from src.domain.world_state import WorldState
from src.rules.ending_evaluator import evaluate_endings
from src.rules.goal_tracker import apply_consequences
from src.rules.option_trigger import should_trigger_option
from src.rules.option_validator import fallback_options, validate_options
from src.rules.phase_tension import clamp_phase_hint, maybe_advance_phase, update_tension


class GameKernel:
    """Owns the reading/choice turn loop and all WorldState writes.

    ``self.state`` is the sole live WorldState after every turn. Callers must
    not keep a separate reference from construction: ``apply_consequences``
    rebinds ``self.state`` to a deep-copied model, so only ``kernel.state`` is
    authoritative for subsequent reads/writes.
    """

    def __init__(
        self,
        pack: SettingPack,
        state: WorldState,
        events: EventDatabase,
        director,
        character,
        choice,
        memory,
    ) -> None:
        self.pack = pack
        # Sole live WorldState — reassigned by apply_consequences; do not alias.
        self.state = state
        self.events = events
        self.director = director
        self.character = character
        self.choice = choice
        self.memory = memory
        self._last_scene: Optional[SceneIntent] = None
        # Parked Task 4 fix: default tension=5 would skip SETUP immediately
        # (maybe_advance_phase SETUP→RISING when tension >= 5).
        self._fix_initial_tension()

    def _fix_initial_tension(self) -> None:
        if (
            self.state.tension == 5
            and self.state.phase == Phase.SETUP
            and self.state.steps == 0
        ):
            self.state.tension = 3

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> list[dict]:
        """Emit game_start; tension fix already applied in __init__."""
        self._fix_initial_tension()
        msgs: list[dict] = [
            {
                "type": "game_start",
                "chapter": self.pack.title,
                "session_id": self.state.session_id,
            }
        ]
        # Optional opening narration from pack seed (does not consume a turn).
        seed = (self.pack.opening_seed or "").strip()
        if seed:
            msgs.append(
                {
                    "type": "narration",
                    "content": seed,
                    "mood": "opening",
                }
            )
        return msgs

    async def advance_reading(self) -> list[dict]:
        """One reading turn; may append an options message."""
        if self.state.ended:
            return [
                {
                    "type": "error",
                    "message": "Game has already ended",
                }
            ]
        if self.state.pending_options:
            return [
                {
                    "type": "error",
                    "message": "Pending options require a player choice before advancing",
                }
            ]

        msgs: list[dict] = []

        # 1. Memory.recall
        memories = await self.memory.recall(
            self.state, self.pack, self.events, k=5
        )

        # 2. Director.generate_scene
        scene: SceneIntent = await self.director.generate_scene(
            self.state, self.pack, memories
        )
        self._last_scene = scene

        # 3. Emit narration + dialogue
        msgs.append(
            {
                "type": "narration",
                "content": scene.narration,
                "mood": scene.mood,
            }
        )
        self._append_event(
            EventType.NARRATION,
            {
                "narration": scene.narration,
                "mood": scene.mood,
                "summary": scene.narration[:80],
            },
            tags=list(scene.event_tags),
        )

        for char_id in scene.speaking_character_ids:
            directive = scene.dialogue_directives.get(char_id, "")
            line = await self.character.generate_dialogue(
                char_id, directive, self.state, self.pack, memories
            )
            display_name = self._char_name(char_id)
            msgs.append(
                {
                    "type": "dialogue",
                    "character": display_name,
                    "content": line,
                    "mood": scene.mood,
                }
            )
            self._append_event(
                EventType.DIALOGUE,
                {
                    "character_id": char_id,
                    "character": display_name,
                    "dialogue": line,
                    "text": line,
                    "summary": line[:80],
                },
                tags=list(scene.event_tags),
            )

        # 4. Tension / phase / counters
        self.state.tension = update_tension(
            self.state.tension,
            scene.suggested_tension_delta,
            list(scene.event_tags),
            self.state.phase,
        )
        rule_phase = maybe_advance_phase(self.state, self.pack)
        if rule_phase != self.state.phase:
            self.state.phase = rule_phase
        elif scene.phase_hint:
            self.state.phase = clamp_phase_hint(self.state.phase, scene.phase_hint)

        self.state.steps += 1
        self.state.turns_since_last_option += 1

        # 5. Ending check first — never emit options + ending in the same turn
        ending_msg = self._try_end()
        if ending_msg:
            self.state.pending_options = []
            msgs.append(ending_msg)
            return msgs

        # 6. Option trigger (only if game continues)
        trigger = should_trigger_option(
            turns_since_last_option=self.state.turns_since_last_option,
            tension=self.state.tension,
            phase=self.state.phase,
            wants_option=scene.wants_option,
            decision_pressure=scene.decision_pressure,
        )
        if trigger.get("should_trigger"):
            options = await self._generate_validated_options(scene, memories)
            self.state.pending_options = list(options)
            msgs.append(
                {
                    "type": "options",
                    "options": [
                        {
                            "id": o.id,
                            "text": o.text,
                            "preview": o.narrative_preview,
                        }
                        for o in options
                    ],
                }
            )

        return msgs

    async def apply_player_choice(self, option_index: int) -> list[dict]:
        """Apply a pending option by index; may end the game."""
        if self.state.ended:
            return [{"type": "error", "message": "Game has already ended"}]

        if not self.state.pending_options:
            return [
                {
                    "type": "error",
                    "message": "No pending options to choose from",
                }
            ]

        if option_index < 0 or option_index >= len(self.state.pending_options):
            return [
                {
                    "type": "error",
                    "message": f"Invalid option_index: {option_index}",
                }
            ]

        option = self.state.pending_options[option_index]
        consequences = option.predicted_consequences

        # Apply structured consequences
        self.state = apply_consequences(self.state, self.pack, consequences)
        self.state.turns_since_last_option = 0
        self.state.pending_options = []

        # Major choice may advance phase (e.g. climax → falling)
        self.state.phase = maybe_advance_phase(
            self.state, self.pack, major_choice=True
        )

        self._append_event(
            EventType.PLAYER_CHOICE,
            {
                "option_id": option.id,
                "text": option.text,
                "summary": option.text,
                "flag_changes": dict(consequences.flag_changes),
                "tags": list(consequences.tags),
            },
            tags=list(consequences.tags),
        )

        msgs: list[dict] = [
            {
                "type": "state_update",
                "changes": {
                    "flags": dict(self.state.flags),
                    "relationships": {
                        cid: {"trust": rel.trust, "romance": rel.romance}
                        for cid, rel in self.state.relationships.items()
                    },
                    "phase": self.state.phase.value,
                    "tension": self.state.tension,
                    "steps": self.state.steps,
                },
            }
        ]

        ending_msg = self._try_end()
        if ending_msg:
            msgs.append(ending_msg)

        return msgs

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _generate_validated_options(
        self,
        scene: SceneIntent,
        memories: list[str],
    ) -> list[ChoiceOption]:
        valid_char_ids = [c.id for c in self.pack.characters]
        valid_goal_ids = [g.id for g in self.pack.goals]
        recent_tags = self._recent_choice_tags()

        options = await self.choice.generate_options(
            self.state, self.pack, scene, memories
        )
        result = validate_options(
            options,
            valid_character_ids=valid_char_ids,
            valid_goal_ids=valid_goal_ids,
            recent_choice_tags=recent_tags,
        )
        if not result.valid:
            # Retry once
            options = await self.choice.generate_options(
                self.state, self.pack, scene, memories
            )
            result = validate_options(
                options,
                valid_character_ids=valid_char_ids,
                valid_goal_ids=valid_goal_ids,
                recent_choice_tags=recent_tags,
            )
        if not result.valid:
            return fallback_options()
        return result.options

    def _try_end(self) -> Optional[dict]:
        """Evaluate endings; force fallback path when steps >= max_steps."""
        if self.state.ended:
            return None

        ending = evaluate_endings(self.pack, self.state)
        if ending is None and self.state.steps >= self.pack.max_steps:
            ending = self._force_fallback_ending()

        if ending is None:
            return None

        self.state.ended = True
        self.state.ending_id = ending.id
        self.state.pending_options = []
        return {
            "type": "ending",
            "ending_id": ending.id,
            "title": ending.title,
            "content": ending.content,
            "ending_type": ending.type.value,
        }

    def _force_fallback_ending(self) -> Optional[EndingDef]:
        """Pick an ending when steps >= max_steps and evaluate_endings missed.

        Order (always returns an ending if the pack has any):
        1. Re-run evaluate_endings (conditions may already match after max_steps).
        2. Highest-priority ending with type=fallback.
        3. Highest-priority ending whose condition mentions ``steps``.
        4. Lowest-priority ending as last resort.
        """
        ending = evaluate_endings(self.pack, self.state)
        if ending is not None:
            return ending

        fallbacks = [e for e in self.pack.endings if e.type == EndingType.FALLBACK]
        if fallbacks:
            return max(fallbacks, key=lambda e: e.priority)

        step_endings = [e for e in self.pack.endings if "steps" in e.condition]
        if step_endings:
            return max(step_endings, key=lambda e: e.priority)

        if self.pack.endings:
            return min(self.pack.endings, key=lambda e: e.priority)
        return None

    def _append_event(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        tags: Optional[List[str]] = None,
    ) -> GameEvent:
        ev = GameEvent(
            id=str(uuid.uuid4()),
            step=self.state.steps,
            type=event_type,
            payload=payload,
            phase=self.state.phase,
            tension=self.state.tension,
            tags=list(tags or []),
        )
        self.events.append(ev)
        return ev

    def _char_name(self, char_id: str) -> str:
        for c in self.pack.characters:
            if c.id == char_id:
                return c.name
        return char_id

    def _recent_choice_tags(self) -> list[str]:
        tags: list[str] = []
        for ev in reversed(self.events.list()):
            if ev.type == EventType.PLAYER_CHOICE:
                t = ev.payload.get("tags") or ev.tags or []
                if t:
                    return list(t)
        return tags
