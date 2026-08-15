"""LLM-backed Segment Writer Agent."""

from __future__ import annotations

from src.story.runtime.model import LLMClient
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import PresentedChoice, SessionState

from .contracts import (
    ModelContractError,
    SegmentDraft,
    SegmentPlan,
    SegmentWriterOutput,
)
from .segment_context import build_segment_writer_context

SEGMENT_WRITER_INSTRUCTIONS = """You are the Segment Writer for a constrained visual novel.
Render ONLY the approved SegmentPlan as narration and dialogue blocks. You cannot add a fact,
effect, character, location, choice ID, thread, or ending obligation that is not in the plan.

HARD RULES — the output is machine-validated and any violation is rejected:

0. PLAYER CHOICE (when the context has a "player_choice" section):
   This segment DIRECTLY follows that choice. The chosen action must visibly
   happen in the prose, and its intent, stance, accepted risk, and possible
   obligation must shape how characters react. Never undo, ignore, or
   contradict it. The section appears for this segment only — when absent,
   do not invent or resurrect an earlier choice.

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

7. RECENT PROSE (when the context has a "recent_prose" section):
   These are the literal final prose blocks the player just read. Continue
   seamlessly from the last block — never repeat or re-narrate them — and
   match their quotation marks, punctuation, and formatting exactly so the
   seam is invisible.
- Return only the requested structured contract."""


class LLMSegmentWriter:
    """Segment Writer backed by the structured-output LLM client."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def write_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        *,
        pending_choice: PresentedChoice | None = None,
    ) -> SegmentDraft:
        output = await self.client.complete_structured(
            instructions=SEGMENT_WRITER_INSTRUCTIONS,
            payload={
                "operation": "write_segment",
                "context": build_segment_writer_context(
                    pack, state, plan, pending_choice=pending_choice
                ),
            },
            output_type=SegmentWriterOutput,
        )
        draft = output.segment_draft
        if draft.segment_id != plan.segment_id:
            raise ModelContractError(
                f"writer changed segment_id: expected {plan.segment_id}, got {draft.segment_id}"
            )
        return draft
