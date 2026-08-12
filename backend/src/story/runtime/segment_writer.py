"""SDK-backed Segment Writer Agent using OpenAI Responses."""

from __future__ import annotations

import json

from agents import Agent
from agents.models.openai_responses import OpenAIResponsesModel

from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState

from .contracts import (
    ModelContractError,
    SegmentDraft,
    SegmentPlan,
    SegmentWriterOutput,
)
from .model import ProviderStrictOutputSchema, run_with_contract_retry
from .segment_context import build_segment_writer_context

SEGMENT_WRITER_INSTRUCTIONS = """You are the Segment Writer for a constrained visual novel.
Render ONLY the approved SegmentPlan as narration and dialogue blocks. You cannot add a fact,
effect, character, location, choice ID, thread, or ending obligation that is not in the plan.

HARD RULES — the output is machine-validated and any violation is rejected:

1. KNOWLEDGE SCOPING (most important):
   A character's dialogue may reference ONLY facts listed in that character's own
   "known_facts" section in the context. A character must never state, hint at, or
   reference a fact they have not learned — never by name, content, or implication.
   The context gives each character ONLY their own knowledge; if a fact is not in the
   speaker's known_facts, the speaker cannot know it. Never infer another character's
   secrets from the context.

2. NO INTERNAL IDS IN PROSE:
   Never write a fact ID, thread ID, character ID, location ID, or option ID inside
   narration or dialogue text. For example "alice_hidden_motive", "notebook_holder",
   "bob_has_org_history" must NEVER appear inside a block's text. The output is
   machine-checked: any snake_case identifier in a block's text is automatically
   rejected. Refer to facts only in natural language — and only when the speaker knows
   them. Never quote, cite, or parenthesize a fact's ID in prose.

3. DECISION CHOICES ARE MANDATORY:
   When the plan's terminal is "decision", the draft MUST contain every planned choice
   from the plan's last scene with the EXACT same option_id as the plan, and 2-4 unique
   natural-language labels. Never return zero choices, never omit a planned option_id,
   and never invent a new option_id.

4. STRUCTURE:
   - Each scene_id in the draft must match the plan's scene_id exactly.
   - Narration blocks have no character_id; dialogue blocks have a character_id.
   - For an ending terminal, generate the dynamic title and final ending blocks from the
     ending_proposal.

5. Keep each character's dialogue within that character's supplied knowledge, beliefs,
   voice, and boundaries in the context. Do NOT share one character's secrets with
   another character's dialogue.

6. Write in the script pack language and prose style.
- Return only the requested structured contract."""


class SdkSegmentWriter:
    """Segment Writer Agent backed by the OpenAI Agents SDK."""

    def __init__(self, model: OpenAIResponsesModel) -> None:
        self.agent = Agent(
            name="Segment Writer",
            instructions=SEGMENT_WRITER_INSTRUCTIONS,
            model=model,
            output_type=ProviderStrictOutputSchema(SegmentWriterOutput),
        )

    async def write_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
    ) -> SegmentDraft:
        prompt = json.dumps(
            {
                "operation": "write_segment",
                "context": build_segment_writer_context(pack, state, plan),
            },
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, SegmentWriterOutput)
        draft = output.segment_draft
        if draft.segment_id != plan.segment_id:
            raise ModelContractError(
                f"writer changed segment_id: expected {plan.segment_id}, got {draft.segment_id}"
            )
        return draft
