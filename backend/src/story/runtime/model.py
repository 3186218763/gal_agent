"""Shared OpenAI Responses model bundle and contract-retry helper."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypeVar

from agents import Runner, set_tracing_disabled
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
