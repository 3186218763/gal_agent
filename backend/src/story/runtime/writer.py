"""SDK-backed scene writer using OpenAI Responses only."""

from __future__ import annotations

import json

from agents import Agent
from agents.models.openai_responses import OpenAIResponsesModel

from src.story.script_pack.models import CompiledScriptPack, EndingSource
from src.story.state import SessionState

from .context import build_ending_context, build_writer_context
from .contracts import EndingDraft, ModelContractError, SceneDraft, ScenePlan, WriterOutput
from .model import run_with_contract_retry

WRITER_INSTRUCTIONS = """You are the prose writer for a constrained visual novel.
Render only the approved semantic plan. Never add, remove, or change a fact, effect, action, choice
ID, ending ID, or ending obligation. Keep each character's dialogue within that character's supplied
knowledge, beliefs, voice, and boundaries. Write in the script pack language and return only the
requested structured contract."""


class SdkWriter:
    def __init__(self, model: OpenAIResponsesModel) -> None:
        self.agent = Agent(
            name="V2 Scene Writer",
            instructions=WRITER_INSTRUCTIONS,
            model=model,
            output_type=WriterOutput,
        )

    async def write_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: ScenePlan,
    ) -> SceneDraft:
        prompt = json.dumps(
            {
                "operation": "write_scene",
                "approved_plan": plan.model_dump(mode="json"),
                "context": build_writer_context(
                    pack, state, plan.present_character_ids, plan
                ),
            },
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, WriterOutput)
        if output.kind != "scene" or output.scene is None:
            raise ModelContractError("writer returned non-scene output")
        return output.scene

    async def write_ending(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        ending: EndingSource,
    ) -> EndingDraft:
        prompt = json.dumps(
            {
                "operation": "write_ending",
                "context": build_ending_context(pack, state, ending),
            },
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, WriterOutput)
        if output.kind != "ending" or output.ending is None:
            raise ModelContractError("writer returned non-ending output")
        if output.ending.ending_id != ending.id:
            raise ModelContractError("writer changed ending id")
        return output.ending
