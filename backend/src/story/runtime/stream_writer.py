"""Streaming scene generator using raw OpenAI Responses API."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from src.story.runtime.contracts import ModelContractError
from src.story.runtime.stream_parser import BlockStreamParser
from src.story.script_pack.models import CompiledScriptPack, ScriptPackSourceV2
from src.story.state import SessionState

STREAMING_WRITER_INSTRUCTIONS = """\
You are the narrator and dialogue writer for a visual novel game.
Generate immersive narration and character dialogue.

Output ONLY valid JSON in this exact structure:
{"blocks":[{"kind":"narration","text":"..."},{"kind":"dialogue","character_id":"...","text":"..."}],"terminal":"decision","decision_id":"d_N","choices":[{"option_id":"opt_N","action_id":"...","label":"...","intent":"..."}]}

Rules:
- Generate 5-15 blocks alternating between narration and dialogue.
- Use "narration" for descriptive text and inner monologue (no character_id).
- Use "dialogue" with the speaking character's "character_id".
- Present a decision (terminal="decision") roughly every 2-3 scenes with 2-4 choices.
- Between decisions, use terminal="continue" with an empty choices array.
- Each choice must use an action_id from the provided available_actions.
- Write in the specified language and prose style.
- Keep each character's dialogue matching their personality and voice.
- Do NOT output anything outside the JSON.
"""


def _build_scene_prompt(pack: CompiledScriptPack, state: SessionState) -> str:
    """Build the user-input JSON for the streaming model call."""
    source = pack.source
    locations = (
        source.world_setting.locations
        if isinstance(source, ScriptPackSourceV2)
        else source.world.locations
    )
    location_name = next(
        (loc.name for loc in locations if loc.id == state.world.location_id),
        state.world.location_id,
    )
    characters = []
    for char in source.characters:
        if char.id in state.world.present_character_ids:
            characters.append({
                "id": char.id,
                "name": char.name,
                "public_profile": char.public_profile,
                "personality": char.personality.model_dump(mode="json"),
                "voice": char.voice.model_dump(mode="json"),
                "drives": char.drives,
            })

    recent_blocks = []
    if state.pending_scene is not None:
        recent_blocks = [b.model_dump(mode="json") for b in state.pending_scene.blocks]

    available_actions = sorted(
        pack.action_ids & set(source.protagonist.capabilities)
    )

    return json.dumps({
        "scene_number": state.world.scene_count + 1,
        "phase": state.world.phase.value,
        "location": {"id": state.world.location_id, "name": location_name},
        "premise": (
            source.world_setting.premise
            if isinstance(source, ScriptPackSourceV2)
            else source.world.premise
        ),
        "prose_style": source.experience.prose_style,
        "tone": source.experience.tone,
        "forbidden_content": source.experience.forbidden_content,
        "language": source.identity.language,
        "characters": characters,
        "recent_blocks": recent_blocks,
        "available_actions": list(available_actions),
    }, ensure_ascii=False)


class StreamingSceneGenerator:
    """Generates scene content via a single streaming model call.

    Yields ``("block", block_dict)`` for each completed NarrativeBlock,
    then yields ``("complete", full_result_dict)`` with the parsed full
    output including choices.
    """

    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def generate_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        prompt = _build_scene_prompt(pack, state)
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
        yield ("complete", final)
