"""SDK-backed Segment Director Agent using OpenAI Responses."""

from __future__ import annotations

import json

from agents import Agent
from agents.models.openai_responses import OpenAIResponsesModel

from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState

from .contracts import (
    DirectorOutput,
    PacingEnvelope,
    SegmentPlan,
)
from .model import ProviderStrictOutputSchema, run_with_contract_retry
from .segment_context import build_director_context

DIRECTOR_INSTRUCTIONS = """You are the Segment Director for a constrained visual novel.
You receive the post-choice world state, world truth, event context, character knowledge map,
completion requirements, open threads, and a deterministic pacing envelope.

Return ONLY a SegmentPlan — never narration, dialogue, or prose.

Rules:
- The plan must contain 1 or more scenes. Only the last scene may be terminal.
- If pacing.must_end is true or the story has a defensible conclusion, set terminal="ending"
  and provide an ending_proposal with title, tone, and terminal_state_summary.
- Otherwise set terminal="decision" and provide 2-4 choices on the last scene.
- Middle scenes must always be terminal="continue".
- You may propose thread_ops (open/advance/close), new_facts (fact commits), and phase_after.
- All proposals are checked by the deterministic kernel — never assume state has changed.
- Use only IDs, locations, characters, goals, facts, and action IDs from the input.
- Never choose a latent fact value outside its listed candidates.
- Do not invent new character IDs, location IDs, or action IDs.
- present_character_ids must contain ONLY character IDs from the provided "characters"
  list. The protagonist is the player character, not part of that list — never put
  "protagonist" in present_character_ids.
- Write all summaries in the script pack language.
- Return only the requested structured contract."""


class SdkDirector:
    """Segment Director Agent backed by the OpenAI Agents SDK."""

    def __init__(self, model: OpenAIResponsesModel) -> None:
        self.agent = Agent(
            name="Segment Director",
            instructions=DIRECTOR_INSTRUCTIONS,
            model=model,
            output_type=ProviderStrictOutputSchema(DirectorOutput),
        )

    async def plan_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        pacing: PacingEnvelope,
    ) -> SegmentPlan:
        prompt = json.dumps(
            {
                "operation": "plan_segment",
                "context": build_director_context(pack, state, pacing),
            },
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, DirectorOutput)
        return output.segment_plan
