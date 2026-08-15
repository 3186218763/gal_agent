"""LLM-backed narrative planner."""

from __future__ import annotations

from typing import Any

from src.story.runtime.model import LLMClient
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import PresentedChoice, SessionState

from .context import build_planner_context
from .contracts import ActionResolution, ModelContractError, PlannerOutput, ScenePlan

PLANNER_INSTRUCTIONS = """You are the semantic planner for a constrained visual novel.
Return only the requested structured contract. Propose events and action outcomes; never claim
that state has changed. Use only IDs, locations, characters, goals, facts, candidate values, and
actions supplied in the input. Never choose a latent fact value outside its candidates. Do not
write narration or dialogue. The validator and reducer are the only state authority."""


class LLMPlanner:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def plan_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> ScenePlan:
        output = await self.client.complete_structured(
            instructions=PLANNER_INSTRUCTIONS,
            payload={
                "operation": "plan_scene",
                "context": build_planner_context(pack, state),
            },
            output_type=PlannerOutput,
        )
        if output.kind != "scene" or output.scene is None:
            raise ModelContractError("planner returned non-scene output")
        return output.scene

    async def resolve_action(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        choice: PresentedChoice,
        rejection_notes: tuple[str, ...] = (),
    ) -> ActionResolution:
        payload: dict[str, Any] = {
            "operation": "resolve_action",
            "choice": choice.model_dump(mode="json"),
            "context": build_planner_context(pack, state),
        }
        if rejection_notes:
            payload["rejection_notes"] = list(rejection_notes)
        output = await self.client.complete_structured(
            instructions=PLANNER_INSTRUCTIONS,
            payload=payload,
            output_type=PlannerOutput,
        )
        if output.kind != "resolution" or output.resolution is None:
            raise ModelContractError("planner returned non-resolution output")
        return output.resolution
