"""SDK-backed narrative planner using OpenAI Responses only."""

from __future__ import annotations

import json

from agents import Agent
from agents.models.openai_responses import OpenAIResponsesModel

from src.story.script_pack.models import CompiledScriptPack
from src.story.state import PresentedChoice, SessionState

from .context import build_planner_context
from .contracts import ActionResolution, ModelContractError, PlannerOutput, ScenePlan
from .model import run_with_contract_retry

PLANNER_INSTRUCTIONS = """You are the semantic planner for a constrained visual novel.
Return only the requested structured contract. Propose events and action outcomes; never claim
that state has changed. Use only IDs, locations, characters, goals, facts, candidate values, and
actions supplied in the input. Never choose a latent fact value outside its candidates. Do not
write narration or dialogue. The validator and reducer are the only state authority."""


class SdkPlanner:
    def __init__(self, model: OpenAIResponsesModel) -> None:
        self.agent = Agent(
            name="V2 Narrative Planner",
            instructions=PLANNER_INSTRUCTIONS,
            model=model,
            output_type=PlannerOutput,
        )

    async def plan_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> ScenePlan:
        prompt = json.dumps(
            {"operation": "plan_scene", "context": build_planner_context(pack, state)},
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, PlannerOutput)
        if output.kind != "scene" or output.scene is None:
            raise ModelContractError("planner returned non-scene output")
        return output.scene

    async def resolve_action(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        choice: PresentedChoice,
    ) -> ActionResolution:
        prompt = json.dumps(
            {
                "operation": "resolve_action",
                "choice": choice.model_dump(mode="json"),
                "context": build_planner_context(pack, state),
            },
            ensure_ascii=False,
        )
        output = await run_with_contract_retry(self.agent, prompt, PlannerOutput)
        if output.kind != "resolution" or output.resolution is None:
            raise ModelContractError("planner returned non-resolution output")
        return output.resolution
