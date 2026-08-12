"""Deterministic fallback segment generator.

When the LLM pipeline fails (timeout, contract error, etc.), this module
produces a minimal valid segment that keeps the game playable.  The fallback
generates a brief narration pause + standard choices so the player can retry
without hitting a dead-end error screen.
"""

from __future__ import annotations

import uuid

from src.story.runtime.contracts import (
    ChoicePlan,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.segment_contracts import (
    SegmentDraft,
    SegmentPlan,
)
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import NarrativeBlock, SessionState


def _fallback_segment_id(state: SessionState) -> str:
    return f"fallback_{state.world.scene_count + 1}_{uuid.uuid4().hex[:6]}"


def generate_fallback_segment(
    pack: CompiledScriptPack,
    state: SessionState,
) -> tuple[SegmentPlan, SegmentDraft]:
    """Produce a minimal valid (plan, draft) pair with no LLM calls.

    The fallback is a "pause" scene: a short narration block followed by
    2-3 standard choices derived from the protagonist's available actions.
    """
    source = pack.source
    seg_id = _fallback_segment_id(state)
    scene_id = f"{seg_id}_s1"

    # Pick 2-3 standard actions for choices
    available_actions = sorted(
        pack.action_ids & set(source.protagonist.capabilities)
    )
    # Prioritize observe/ask/support for a natural "think" moment
    preferred = ["observe", "ask", "support", "challenge", "wait"]
    chosen: list[str] = []
    for action in preferred:
        if action in available_actions and len(chosen) < 3:
            chosen.append(action)
    # Fill remaining slots from available actions
    for action in available_actions:
        if action not in chosen and len(chosen) < 3:
            chosen.append(action)
    # Ensure at least 2 choices
    if len(chosen) < 2:
        chosen = ["observe", "ask"]

    # Narration text (in the pack language)
    lang = source.identity.language
    if lang.startswith("zh"):
        narration = "沉默在空气中蔓延。你整理了一下思绪，思考接下来该怎么做。"
        choice_labels = {
            "observe": "观察周围的情况",
            "ask": "主动开口询问",
            "support": "表达关心",
            "challenge": "提出质疑",
            "wait": "暂时按兵不动",
        }
    else:
        narration = "A pause stretches through the air. You collect your thoughts and consider your next move."
        choice_labels = {
            "observe": "Observe the situation",
            "ask": "Ask a question",
            "support": "Show support",
            "challenge": "Raise a challenge",
            "wait": "Wait and see",
        }

    # Build choices
    choices_plan = tuple(
        ChoicePlan(
            option_id=f"fb_{action}",
            action_id=action,
            intent=choice_labels.get(action, action),
        )
        for action in chosen
    )
    choices_written = tuple(
        WrittenChoice(
            option_id=f"fb_{action}",
            label=choice_labels.get(action, action),
            preview=None,
        )
        for action in chosen
    )

    plan = SegmentPlan(
        segment_id=seg_id,
        scenes=(
            ScenePlan(
                scene_id=scene_id,
                summary="Fallback pause scene — protagonist considers options.",
                location_id=state.world.location_id,
                present_character_ids=tuple(state.world.present_character_ids),
                terminal="decision",
                decision_id=f"fb_decision_{state.world.scene_count + 1}",
                choices=choices_plan,
            ),
        ),
        terminal="decision",
    )

    draft = SegmentDraft(
        segment_id=seg_id,
        scene_drafts=(
            SceneDraft(
                scene_id=scene_id,
                blocks=(
                    NarrativeBlock(kind="narration", text=narration),
                ),
                choices=choices_written,
            ),
        ),
        choices=choices_written,
    )

    return plan, draft
