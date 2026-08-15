"""Direct structured-output LLM client over OpenAI-compatible APIs.

No agent framework: every call is one round trip — prompt in, validated
contract out.  Providers that do not enforce ``response_format.json_schema``
get the exact schema embedded in the prompt (see ``complete_structured``);
a violation is repaired once with the validation error re-sent.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Awaitable
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from .config import LLMSettings
from .contracts import ModelContractError, ModelTimeoutError

T = TypeVar("T", bound=BaseModel)

SCHEMA_RULE = (
    "Return ONLY a JSON object that validates against required_output_schema. "
    "Use exactly its field names and types; every array item must be an object "
    "with the schema's item fields (never a shorthand string); add no extra fields."
)

REPAIR_INSTRUCTION = (
    "Your previous reply violated the contract. Return ONLY JSON that "
    "validates against required_output_schema: use exactly the field "
    "names it lists, include every required field, and add no extra "
    "fields. Values must match the schema types."
)


def _inline_refs(schema: Any, defs: dict[str, Any], seen: set[str]) -> Any:
    """Inline every ``$ref`` so the schema is fully self-contained.

    Some providers cannot resolve ``$defs``/``$ref``: OpenCode Go rejects bare
    ``$ref`` branches in ``anyOf`` ("anyOf: missing field type"), and Gemini's
    OpenAI-compatible schema conversion drops unresolved refs entirely — the
    model then never sees the nested structure and returns empty objects.
    """
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            resolved = defs.get(name)
            if isinstance(resolved, dict) and ref not in seen:
                seen.add(ref)
                try:
                    schema = _inline_refs(copy.deepcopy(resolved), defs, seen)
                finally:
                    seen.discard(ref)
        any_of = schema.get("anyOf")
        if isinstance(any_of, list):
            schema["anyOf"] = [_inline_refs(branch, defs, seen) for branch in any_of]
        for key, value in list(schema.items()):
            schema[key] = _inline_refs(value, defs, seen)
    elif isinstance(schema, list):
        schema = [_inline_refs(item, defs, seen) for item in schema]
    return schema


def _strictify(schema: Any) -> Any:
    """Add ``additionalProperties: false`` to every object schema."""
    if isinstance(schema, dict):
        if schema.get("type") == "object" or "properties" in schema:
            schema.setdefault("additionalProperties", False)
        for value in schema.values():
            _strictify(value)
    elif isinstance(schema, list):
        for item in schema:
            _strictify(item)
    return schema


def build_output_schema(output_type: type[BaseModel]) -> dict[str, Any]:
    """Strict, self-contained JSON schema for a contract model."""
    schema = output_type.model_json_schema()
    defs = schema.pop("$defs", {})
    if not isinstance(defs, dict):
        defs = {}
    inlined = _inline_refs(schema, defs, set())
    if not isinstance(inlined, dict):
        raise TypeError(f"schema for {output_type} is not an object schema")
    return _strictify(inlined)


def _strip_code_fences(text: str) -> str:
    """Strip a ```json ... ``` fence some models wrap around JSON output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def parse_model_json(text: str) -> Any:
    """Parse raw model text as JSON, stripping code fences."""
    cleaned = _strip_code_fences(text)
    if not cleaned:
        raise ValueError("model returned an empty output")
    return json.loads(cleaned)


class LLMClient:
    """One structured-output client per provider configuration."""

    def __init__(self, settings: LLMSettings) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
        self._api = settings.api
        self.model = settings.model
        # Hard per-call deadline.  The client timeout bounds one HTTP attempt
        # (and provider retries can stack several attempts), so the client
        # enforces its own wall-clock bound per model call: a hung provider
        # fails the turn fast instead of stalling the player forever.
        self._deadline_seconds = settings.timeout_seconds

    @property
    def api(self) -> str:
        return self._api

    @property
    def raw(self) -> AsyncOpenAI:
        """The underlying OpenAI client (streaming writer path)."""
        return self._client

    async def complete_structured(
        self,
        *,
        instructions: str,
        payload: dict[str, Any],
        output_type: type[T],
    ) -> T:
        """Ask once, validate, repair once, then fail closed."""
        schema = build_output_schema(output_type)
        request: dict[str, Any] = {
            **payload,
            "required_output_schema": schema,
            "schema_rule": SCHEMA_RULE,
        }
        first = await self._ask(instructions, json.dumps(request, ensure_ascii=False), schema)
        try:
            return output_type.model_validate(parse_model_json(first))
        except (ValueError, ValidationError) as first_error:
            repair: dict[str, Any] = {
                "operation": "repair_contract",
                "validation_error": str(first_error)[:1000],
                "original_input": request,
                "instruction": REPAIR_INSTRUCTION,
                "required_output_schema": schema,
                "schema_rule": SCHEMA_RULE,
            }
            second = await self._ask(instructions, json.dumps(repair, ensure_ascii=False), schema)
            try:
                return output_type.model_validate(parse_model_json(second))
            except (ValueError, ValidationError) as second_error:
                raise ModelContractError(
                    f"structured output failed after repair: {str(second_error)[:1000]}"
                ) from second_error

    async def stream_text(self, *, system: str, user: str) -> AsyncIterator[str]:
        """Yield streamed text deltas for a plain (non-structured) prompt."""
        if self._api == "chat_completions":
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta
            return
        stream = await self._client.responses.create(
            model=self.model,
            input=user,
            instructions=system,
            stream=True,
        )
        async for event in stream:
            if getattr(event, "type", "") == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                if delta:
                    yield delta

    async def _ask(self, instructions: str, user: str, schema: dict[str, Any]) -> str:
        """One non-streaming request in the configured API flavor.

        Each request runs under the hard per-call deadline; exceeding it
        raises ModelTimeoutError (a RuntimeGenerationUnavailable) so the
        turn fails fast with a message that names the timeout.
        """
        return await self._ask_with_deadline(self._ask_once(instructions, user, schema))

    async def _ask_with_deadline(self, awaitable: Awaitable[str]) -> str:
        if self._deadline_seconds is None:
            return await awaitable
        try:
            return await asyncio.wait_for(awaitable, self._deadline_seconds)
        except TimeoutError as exc:
            raise ModelTimeoutError(
                f"model call exceeded the {self._deadline_seconds:.0f}s deadline"
            ) from exc

    async def _ask_once(self, instructions: str, user: str, schema: dict[str, Any]) -> str:
        if self._api == "chat_completions":
            # response_format is deliberately omitted: this project's providers
            # treat json_schema as guidance at best, and some proxies corrupt
            # nested arrays of objects — the in-prompt schema does the work.
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user},
                ],
            )
            return response.choices[0].message.content or ""
        # Responses API: the same in-prompt schema doubles as the server-side
        # text.format contract so both flavors see an identical shape.
        response = await self._client.responses.create(
            model=self.model,
            input=user,
            instructions=instructions,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "final_output",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        return response.output_text
