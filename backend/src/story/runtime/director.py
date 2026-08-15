"""LLM-backed Segment Director Agent."""

from __future__ import annotations

from src.story.runtime.model import LLMClient
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState

from .contracts import (
    DirectorOutput,
    PacingEnvelope,
    SegmentPlan,
)
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
- new_facts may ONLY contain fact IDs whose "kind" is "latent" in the facts list.
  Never commit a fact with "kind": "fixed" (for example alice_lost_notebook) — fixed facts
  are already true in the world and can never be committed via new_facts.
- All proposals are checked by the deterministic kernel — never assume state has changed.
- Use only IDs, locations, characters, goals, facts, and action IDs from the input.
- Never choose a latent fact value outside its listed candidates.
- Do not invent new character IDs, location IDs, or action IDs.
- present_character_ids must contain ONLY character IDs from the provided "characters"
  list. The protagonist is the player character, not part of that list — never put
  "protagonist" in present_character_ids.
- Write all summaries in the script pack language.
- Return only the requested structured contract."""


class LLMDirector:
    """Segment Director backed by the structured-output LLM client."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def plan_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        pacing: PacingEnvelope,
    ) -> SegmentPlan:
        output = await self.client.complete_structured(
            instructions=DIRECTOR_INSTRUCTIONS,
            payload={
                "operation": "plan_segment",
                "context": build_director_context(pack, state, pacing),
            },
            output_type=DirectorOutput,
        )
        return output.segment_plan
