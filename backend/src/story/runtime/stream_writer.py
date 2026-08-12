"""Streaming segment writer adapter using raw OpenAI Responses API.

Consumes an approved SegmentPlan and streams provisional blocks.
Does NOT invent facts, choices, terminal states, or effects.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError

from src.story.runtime.contracts import ModelContractError, SegmentPlan, SegmentWriterOutput
from src.story.runtime.segment_context import build_segment_writer_context
from src.story.runtime.stream_parser import BlockStreamParser
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState

STREAMING_WRITER_INSTRUCTIONS = """\
You are the streaming segment writer for a visual novel.
Render ONLY the approved SegmentPlan as narration and dialogue blocks.

Output ONLY valid JSON in this exact structure:
{"segment_draft":{"segment_id":"...","scene_drafts":[{"scene_id":"...","blocks":[{"kind":"narration","text":"..."},{"kind":"dialogue","character_id":"...","text":"..."}],"choices":[{"option_id":"...","label":"..."}]}],"choices":[{"option_id":"...","label":"..."}],"ending":{"title":"...","blocks":[{"kind":"narration","text":"..."}]}}

Rules:
- Each scene_id must match the plan exactly.
- Use "narration" for descriptive text and inner monologue (no character_id).
- Use "dialogue" with the speaking character's "character_id" from the plan's present characters.
- For a decision terminal, include choices matching the plan's option_ids exactly.
- For an ending terminal, include the ending title and final blocks from the ending_proposal.
- Keep each character's dialogue within that character's knowledge and voice.
- Do NOT output anything outside the JSON.
"""


def _build_segment_prompt(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
) -> str:
    """Build the user-input JSON from the approved plan."""
    context = build_segment_writer_context(pack, state, plan)
    return json.dumps(
        {
            "operation": "write_segment",
            "context": context,
        },
        ensure_ascii=False,
    )


class StreamingSceneGenerator:
    """Streaming adapter that consumes an approved SegmentPlan.

    Yields ``("block", block_dict)`` for each completed NarrativeBlock,
    then yields ``("complete", full_result_dict)`` with the parsed full
    SegmentWriterOutput.
    """

    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def generate_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        prompt = _build_segment_prompt(pack, state, plan)
        parser = BlockStreamParser()

        stream = await self._client.responses.create(
            model=self._model,
            input=prompt,
            instructions=STREAMING_WRITER_INSTRUCTIONS,
            stream=True,
        )

        async for event in stream:
            event_type = getattr(event, "type", "")
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                blocks = parser.feed(delta)
                for block_dict in blocks:
                    yield ("block", block_dict)

        final = parser.finalize()
        if final is None:
            raise ModelContractError("streaming output could not be parsed as JSON")
        # Validate as SegmentWriterOutput.  A ValidationError would embed the
        # raw model output in its message, so it is wrapped in a
        # ModelContractError (spec section 10: no raw model output escapes).
        try:
            validated = SegmentWriterOutput.model_validate(final)
        except ValidationError as exc:
            raise ModelContractError(
                "streaming output could not be validated as SegmentWriterOutput"
            ) from exc
        yield ("complete", validated.model_dump(mode="json"))

    async def generate_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        """Deprecated: use generate_segment with an approved plan."""
        raise RuntimeError(
            "generate_scene is deprecated; use generate_segment with an approved SegmentPlan"
        )
