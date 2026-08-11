"""Shared OpenAI Responses model bundle and contract-retry helper."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, TypeVar

from agents import Runner, set_tracing_disabled
from agents.agent_output import AgentOutputSchema
from agents.exceptions import ModelBehaviorError
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from .config import OpenCodeGoSettings
from .contracts import ModelContractError

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ModelBundle:
    client: AsyncOpenAI
    model: OpenAIResponsesModel


def build_model_bundle(settings: OpenCodeGoSettings) -> ModelBundle:
    set_tracing_disabled(True)
    client = AsyncOpenAI(
        api_key=settings.api_key.get_secret_value(),
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )
    model = OpenAIResponsesModel(model=settings.model, openai_client=client)
    return ModelBundle(client=client, model=model)


def _inline_anyof_refs(schema: Any, defs: dict[str, Any], seen: set[str]) -> Any:
    """Inline `$ref` branches inside `anyOf` so every branch carries a `type`.

    The OpenAI Agents SDK strict conversion leaves bare `$ref` branches in `anyOf`
    (strict_schema.py only unravels refs that sit next to other keys), which the
    Console Go provider rejects with "anyOf: missing field type".
    """
    if isinstance(schema, dict):
        any_of = schema.get("anyOf")
        if isinstance(any_of, list):
            schema["anyOf"] = [
                _resolve_anyof_branch(branch, defs, seen) for branch in any_of
            ]
        for value in schema.values():
            _inline_anyof_refs(value, defs, seen)
    elif isinstance(schema, list):
        for item in schema:
            _inline_anyof_refs(item, defs, seen)
    return schema


def _resolve_anyof_branch(branch: Any, defs: dict[str, Any], seen: set[str]) -> Any:
    if not isinstance(branch, dict):
        return branch
    ref = branch.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/$defs/") or ref in seen:
        return branch
    name = ref.rsplit("/", 1)[-1]
    resolved = defs.get(name)
    if not isinstance(resolved, dict):
        return branch
    seen.add(ref)
    try:
        inlined = _inline_anyof_refs(copy.deepcopy(resolved), defs, seen)
    finally:
        seen.discard(ref)
    return inlined


class ProviderStrictOutputSchema(AgentOutputSchema):
    """Strict AgentOutputSchema accepted by the Console Go provider.

    Inlines `$ref` branches inside `anyOf` (optional model fields) after the SDK
    strict conversion so every `anyOf` branch carries a concrete `type`.
    """

    def __init__(self, output_type: type[Any], strict_json_schema: bool = True) -> None:
        super().__init__(output_type, strict_json_schema=strict_json_schema)
        defs = self._output_schema.get("$defs")
        defs = defs if isinstance(defs, dict) else {}
        self._output_schema = _inline_anyof_refs(self._output_schema, defs, set())


async def run_with_contract_retry(agent: Any, prompt: str, expected_type: type[T]) -> T:
    try:
        result = await Runner.run(agent, input=prompt)
        return expected_type.model_validate(result.final_output)
    except (ModelBehaviorError, ValidationError) as first_error:
        repair = json.dumps(
            {
                "operation": "repair_contract",
                "validation_error": str(first_error)[:1000],
                "original_input": json.loads(prompt),
            },
            ensure_ascii=False,
        )
        try:
            result = await Runner.run(agent, input=repair)
            return expected_type.model_validate(result.final_output)
        except (ModelBehaviorError, ValidationError) as second_error:
            raise ModelContractError(
                f"structured output failed after repair: {str(second_error)[:1000]}"
            ) from second_error
