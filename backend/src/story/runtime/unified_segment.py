"""Unified Segment Agent: merges Director + Writer into a single LLM call.

Instead of two round-trips (Director plans → Writer renders), the unified agent
produces both the structural plan and the prose draft in one call.  This halves
latency and significantly improves reliability on flash-tier models.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from agents import Agent
from agents.models.openai_responses import OpenAIResponsesModel
from pydantic import model_validator

from src.story.runtime.contracts import (
    ModelContractError,
    RuntimeModel,
    SegmentDraft,
    SegmentPlan,
    WrittenChoice,
)
from src.story.runtime.model import ProviderStrictOutputSchema, run_with_contract_retry
from src.story.runtime.segment_context import (
    _character_known_facts,
    _completion_requirement_views,
    _event_trace_digest,
    _fact_summary_views,
    _get_forbidden_content,
    _get_immutable_rules,
    _get_world_setting,
    _thread_views,
)
from src.story.runtime.segment_contracts import PacingEnvelope
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState

# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


class UnifiedSegmentOutput(RuntimeModel):
    """Combined plan + draft from a single LLM call."""

    segment_plan: SegmentPlan
    segment_draft: SegmentDraft

    @model_validator(mode="after")
    def validate_consistency(self) -> UnifiedSegmentOutput:
        if self.segment_plan.segment_id != self.segment_draft.segment_id:
            raise ValueError(
                f"segment_id mismatch: plan={self.segment_plan.segment_id}, "
                f"draft={self.segment_draft.segment_id}"
            )
        plan_scenes = [s.scene_id for s in self.segment_plan.scenes]
        draft_scenes = [s.scene_id for s in self.segment_draft.scene_drafts]
        if plan_scenes != draft_scenes:
            raise ValueError(f"scene mismatch: plan={plan_scenes}, draft={draft_scenes}")
        return self


# ---------------------------------------------------------------------------
# Port protocol
# ---------------------------------------------------------------------------


class UnifiedSegmentPort(Protocol):
    async def generate(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        pacing: PacingEnvelope,
    ) -> UnifiedSegmentOutput: ...


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def build_unified_context(
    pack: CompiledScriptPack,
    state: SessionState,
    pacing: PacingEnvelope,
) -> dict[str, Any]:
    """Combine Director and Writer contexts into a single prompt context.

    "planning" section has the Director's world view (pacing, goals, threads).
    "rendering" section has per-character scoped knowledge (voice, known facts).
    """
    source = pack.source
    world_setting = _get_world_setting(source)

    # ── Planning context (what the Director sees) ──
    planning_chars = []
    for character in source.characters:
        runtime = state.characters[character.id]
        planning_chars.append(
            {
                "id": character.id,
                "name": character.name,
                "public_profile": character.public_profile,
                "personality": character.personality.model_dump(mode="json"),
                "drives": character.drives,
                "boundaries": character.boundaries.model_dump(mode="json"),
                "relationship": dict(state.world.relationships.get(character.id, {})),
                "emotional_state": dict(runtime.emotional_state),
                "known_fact_ids": sorted(runtime.knowledge),
            }
        )

    # ── Rendering context (per-character scoped, what the Writer sees) ──
    sources = {item.id: item for item in source.characters}
    rendering_chars = []
    for character in source.characters:
        char_source = sources[character.id]
        runtime = state.characters[character.id]
        rendering_chars.append(
            {
                "id": character.id,
                "name": char_source.name,
                "public_profile": char_source.public_profile,
                "personality": char_source.personality.model_dump(mode="json"),
                "voice": char_source.voice.model_dump(mode="json"),
                "drives": char_source.drives,
                "boundaries": char_source.boundaries.model_dump(mode="json"),
                "relationship": dict(state.world.relationships.get(character.id, {})),
                "emotional_state": dict(runtime.emotional_state),
                "known_facts": _character_known_facts(pack, state, character.id),
                "beliefs": {k: v.model_dump(mode="json") for k, v in runtime.beliefs.items()},
            }
        )

    return {
        # Planning section
        "pack": {
            "id": source.identity.id,
            "language": source.identity.language,
            "premise": world_setting.premise,
            "immutable_rules": _get_immutable_rules(source),
            "forbidden_content": _get_forbidden_content(source),
            "protagonist_id": source.protagonist.id,
            "protagonist_capabilities": list(source.protagonist.capabilities),
            "experience": {
                "viewpoint": source.experience.viewpoint,
                "prose_style": source.experience.prose_style,
                "tone": source.experience.tone,
            },
        },
        "world_truth": {
            "location_id": state.world.location_id,
            "phase": state.world.phase.value,
            "scene_count": state.world.scene_count,
            "pressure": state.world.pressure,
            "present_character_ids": list(state.world.present_character_ids),
        },
        "facts": _fact_summary_views(pack, state),
        "goals": _completion_requirement_views(pack, state),
        "completion_requirements": _completion_requirement_views(pack, state),
        "open_threads": _thread_views(state),
        "pacing": pacing.model_dump(mode="json"),
        "available_action_ids": sorted(pack.action_ids & set(source.protagonist.capabilities)),
        "event_trace": _event_trace_digest(state),
        # Rendering section (per-character scoped knowledge)
        "characters": rendering_chars,
        # Planning-only characters (for reference — do NOT use for dialogue)
        "planning_characters": planning_chars,
        # Location reference
        "locations": [
            {"id": loc.id, "name": loc.name, "tags": list(loc.tags)}
            for loc in world_setting.locations
        ],
    }


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

UNIFIED_INSTRUCTIONS = """You are the Unified Segment Agent for a constrained visual novel.
You do TWO things in one pass:

  STEP 1 — PLAN: Decide the scene structure, locations, characters, and story direction.
  STEP 2 — WRITE: Render each planned scene as narration and dialogue blocks.

Return segment_plan (structure) and segment_draft (prose). They must be consistent:
same segment_id, same scene_ids in the same order.

═══ PLANNING RULES ═══

1. The plan must contain 1 or more scenes. Only the last scene may be terminal.
2. If pacing.must_end is true or the story has a defensible conclusion, set terminal="ending"
   and provide an ending_proposal with title, tone, and terminal_state_summary.
3. Otherwise set terminal="decision" and provide 2-4 choices on the last scene.
4. Middle scenes must always be terminal="continue".
5. You may propose thread_ops (open/advance/close), new_facts (fact commits), and phase_after.
6. new_facts may ONLY contain fact IDs whose "kind" is "latent" in the facts list.
   Never commit a fact with "kind": "fixed".
7. All proposals are checked by the deterministic kernel — never assume state has changed.
8. Use only IDs, locations, characters, goals, facts, and action IDs from the input.
9. Do not invent new character IDs, location IDs, or action IDs.
10. present_character_ids must contain ONLY character IDs from the provided "characters"
    list. The protagonist is the player character, not part of that list — never put
    "protagonist" in present_character_ids.
11. Write all summaries in the script pack language.

═══ WRITING RULES ═══

1. KNOWLEDGE SCOPING (most important):
   A character's dialogue may reference ONLY facts listed in that character's own
   "known_facts" section in the "characters" array. A character must never state, hint at,
   or reference a fact they have not learned — never by name, content, or implication.
   If a fact is not in the speaker's known_facts, the speaker cannot know it.

2. NO INTERNAL IDS IN PROSE:
   Never write a fact ID, thread ID, character ID, location ID, or option ID inside
   narration or dialogue text. Any snake_case identifier in a block's text is rejected.

3. DECISION CHOICES ARE MANDATORY:
   When terminal is "decision", the draft MUST contain every planned choice from the
   plan's last scene with the EXACT same option_id, and 2-4 unique natural-language labels.

4. STRUCTURE:
   - Each scene_id in the draft must match the plan's scene_id exactly.
   - Narration blocks have no character_id; dialogue blocks have a character_id.
   - For an ending terminal, generate the dynamic title and final ending blocks.

5. Keep each character's dialogue within that character's supplied knowledge, beliefs,
   voice, and boundaries. Do NOT share one character's secrets with another character's
   dialogue.

6. Write in the script pack language and prose style.

7. SEGMENT LENGTH: Generate sufficiently long continuous Galgame performance.
   Aim for at least 8 blocks of narration and dialogue between choices.
   Do not rush toward the decision point — let the player linger in each scene.

8. Return only the requested structured contract."""


OPENING_INSTRUCTIONS = """You are the Unified Segment Agent for a constrained visual novel.
This is the game OPENING — a long, atmospheric prologue that must immerse the player
in the world before the first decision.

You do TWO things in one pass:

  STEP 1 — PLAN: Decide the scene structure for a long opening sequence.
  STEP 2 — WRITE: Render each planned scene as narration and dialogue blocks.

Return segment_plan (structure) and segment_draft (prose). They must be consistent:
same segment_id, same scene_ids in the same order.

═══ OPENING-SPECIFIC RULES ═══

1. Generate 3-5 scenes (target 10-20 narrative blocks total).
2. The opening must world-build: establish the setting, character relationships,
   and the initial conflict or mystery.
3. Do NOT rush to a decision — let the player immerse in the opening atmosphere.
   Use ample narration, environmental description, and character interaction.
4. The LAST scene MUST be terminal="decision" with 2-4 choices.
5. All middle scenes must be terminal="continue".

═══ PLANNING RULES ═══

1. The plan must contain 1 or more scenes. Only the last scene may be terminal.
2. If pacing.must_end is true or the story has a defensible conclusion, set terminal="ending"
   and provide an ending_proposal with title, tone, and terminal_state_summary.
3. Otherwise set terminal="decision" and provide 2-4 choices on the last scene.
4. Middle scenes must always be terminal="continue".
5. You may propose thread_ops (open/advance/close), new_facts (fact commits), and phase_after.
6. new_facts may ONLY contain fact IDs whose "kind" is "latent" in the facts list.
   Never commit a fact with "kind": "fixed".
7. All proposals are checked by the deterministic kernel — never assume state has changed.
8. Use only IDs, locations, characters, goals, facts, and action IDs from the input.
9. Do not invent new character IDs, location IDs, or action IDs.
10. present_character_ids must contain ONLY character IDs from the provided "characters"
    list. The protagonist is the player character, not part of that list — never put
    "protagonist" in present_character_ids.
11. Write all summaries in the script pack language.

═══ WRITING RULES ═══

1. KNOWLEDGE SCOPING (most important):
   A character's dialogue may reference ONLY facts listed in that character's own
   "known_facts" section in the "characters" array. A character must never state, hint at,
   or reference a fact they have not learned — never by name, content, or implication.
   If a fact is not in the speaker's known_facts, the speaker cannot know it.

2. NO INTERNAL IDS IN PROSE:
   Never write a fact ID, thread ID, character ID, location ID, or option ID inside
   narration or dialogue text. Any snake_case identifier in a block's text is rejected.

3. DECISION CHOICES ARE MANDATORY:
   When terminal is "decision", the draft MUST contain every planned choice from the
   plan's last scene with the EXACT same option_id, and 2-4 unique natural-language labels.

4. STRUCTURE:
   - Each scene_id in the draft must match the plan's scene_id exactly.
   - Narration blocks have no character_id; dialogue blocks have a character_id.
   - For an ending terminal, generate the dynamic title and final ending blocks.

5. Keep each character's dialogue within that character's supplied knowledge, beliefs,
   voice, and boundaries. Do NOT share one character's secrets with another character's
   dialogue.

6. Write in the script pack language and prose style.
7. Return only the requested structured contract."""


class SdkUnifiedSegmentAgent:
    """Unified Segment Agent backed by the OpenAI Agents SDK.

    Pass ``instructions=OPENING_INSTRUCTIONS`` to create an agent that
    generates long, atmospheric opening segments.
    """

    def __init__(
        self,
        model: OpenAIResponsesModel,
        instructions: str = UNIFIED_INSTRUCTIONS,
    ) -> None:
        self.agent = Agent(
            name="Unified Segment Agent",
            instructions=instructions,
            model=model,
            output_type=ProviderStrictOutputSchema(UnifiedSegmentOutput),
        )

    async def generate(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        pacing: PacingEnvelope,
    ) -> UnifiedSegmentOutput:
        prompt = json.dumps(
            {
                "operation": "plan_and_write_segment",
                "context": build_unified_context(pack, state, pacing),
            },
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, UnifiedSegmentOutput)
        # Extra validation: draft choices must match plan choices.
        # If the model forgot to put choices in the draft, auto-repair
        # by deriving WrittenChoice entries from the plan's ChoicePlan list.
        if output.segment_plan.terminal == "decision":
            plan_scene = output.segment_plan.scenes[-1]
            plan_choices = plan_scene.choices if plan_scene.choices else ()
            plan_option_ids = {c.option_id for c in plan_choices}
            draft_map = {c.option_id: c for c in output.segment_draft.choices}
            missing_ids = plan_option_ids - set(draft_map)
            if missing_ids:
                repaired = list(output.segment_draft.choices)
                for pc in plan_choices:
                    if pc.option_id in missing_ids:
                        repaired.append(
                            WrittenChoice(
                                option_id=pc.option_id,
                                label=pc.intent[:80],
                            )
                        )
                output = output.model_copy(
                    update={
                        "segment_draft": output.segment_draft.model_copy(
                            update={"choices": tuple(repaired)}
                        )
                    }
                )
        return output
