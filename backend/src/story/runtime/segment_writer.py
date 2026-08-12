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

Rules:
- Write narration (no character_id) and dialogue (with character_id) for each scene.
- Each scene_id in the draft must match the plan's scene_id exactly.
- For a decision terminal, render exactly the planned choices with unique labels.
- For an ending terminal, generate the dynamic title and final ending blocks from the ending_proposal.
- Keep each character's dialogue within that character's supplied knowledge, beliefs, voice,
  and boundaries in the context. A character must not state or reference facts they have not
  witnessed or learned.
- Do NOT share one character's secrets with another character's dialogue.
- Write in the script pack language and prose style.
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
