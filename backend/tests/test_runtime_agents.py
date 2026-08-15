from __future__ import annotations

import json
from typing import Any

import pytest

from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    ModelContractError,
    PlannerOutput,
    ScenePlan,
    WriterOutput,
)
from src.story.runtime.model import SCHEMA_RULE, build_output_schema
from src.story.runtime.planner import LLMPlanner
from src.story.script_pack import compile_source
from src.story.state import PresentedChoice, initial_session_state
from tests.fakes import StubLLMClient, json_reply
from tests.story_factories import minimal_script_pack_dict


@pytest.fixture
def pack():
    return compile_source(minimal_script_pack_dict())


@pytest.fixture
def state(pack):
    return initial_session_state(pack, "session_01", session_seed=42)


@pytest.fixture
def offered_choice() -> PresentedChoice:
    return PresentedChoice(
        id="ask_alice",
        action_id="ask",
        label="Ask Alice",
        intent="ask directly",
    )


def valid_scene_plan() -> ScenePlan:
    return ScenePlan(
        scene_id="scene_01",
        summary="Alice waits for the protagonist to choose.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="decision_01",
        choices=(
            ChoicePlan(option_id="ask", action_id="ask", intent="ask directly"),
            ChoicePlan(option_id="observe", action_id="observe", intent="watch carefully"),
        ),
    )


def valid_planner_output(kind: str = "scene") -> PlannerOutput:
    if kind == "scene":
        return PlannerOutput(kind="scene", scene=valid_scene_plan())
    return PlannerOutput(
        kind="resolution",
        resolution=ActionResolution(action_id="ask", outcome="success"),
    )


def _missing_type_in_anyof(schema: Any) -> list[list[Any]]:
    bad: list[list[Any]] = []
    if isinstance(schema, dict):
        any_of = schema.get("anyOf")
        if isinstance(any_of, list):
            missing = [branch for branch in any_of if "type" not in branch]
            if missing:
                bad.append(missing)
        for value in schema.values():
            bad.extend(_missing_type_in_anyof(value))
    elif isinstance(schema, list):
        for item in schema:
            bad.extend(_missing_type_in_anyof(item))
    return bad


def test_planner_and_writer_schemas_are_strict_and_self_contained():
    schema = build_output_schema(PlannerOutput)
    assert schema["properties"]["kind"]["enum"] == ["scene", "resolution"]
    # strict: every object forbids extra fields
    assert schema["additionalProperties"] is False
    # self-contained: no $defs and no unresolved refs anywhere
    assert _missing_type_in_anyof(schema) == []
    assert '"$ref"' not in json.dumps(schema)
    writer_schema = build_output_schema(WriterOutput)
    assert writer_schema["additionalProperties"] is False
    assert _missing_type_in_anyof(writer_schema) == []


@pytest.mark.asyncio
async def test_planner_uses_one_client_for_scene_and_resolution(pack, state, offered_choice):
    client = StubLLMClient(
        replies=[
            json_reply(valid_planner_output("scene")),
            json_reply(valid_planner_output("resolution")),
        ]
    )
    planner = LLMPlanner(client)
    scene = await planner.plan_scene(pack, state)
    resolution = await planner.resolve_action(pack, state, offered_choice)
    assert scene.scene_id == "scene_01"
    assert resolution.action_id == offered_choice.action_id
    # both operations went through the same client with the same instructions
    assert [r["payload"]["operation"] for r in client.requests] == [
        "plan_scene",
        "resolve_action",
    ]
    assert len({r["instructions"] for r in client.requests}) == 1


@pytest.mark.asyncio
async def test_planner_passes_rejection_notes_on_resolve(pack, state, offered_choice):
    client = StubLLMClient(replies=[json_reply(valid_planner_output("resolution"))])
    planner = LLMPlanner(client)
    await planner.resolve_action(
        pack, state, offered_choice, rejection_notes=("cannot evidence uncommitted fact",)
    )
    assert client.requests[0]["payload"]["rejection_notes"] == ["cannot evidence uncommitted fact"]


@pytest.mark.asyncio
async def test_invalid_json_is_repaired_once(pack, state):
    client = StubLLMClient(
        replies=[
            "not json at all",
            json_reply(valid_planner_output()),
        ]
    )
    planner = LLMPlanner(client)
    scene = await planner.plan_scene(pack, state)
    assert scene.scene_id == "scene_01"
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_contract_schema_rides_on_first_attempt_and_repair(pack, state):
    """Providers that treat json_schema as guidance get the exact schema in
    the request payload from the first attempt, and again in the repair
    turn, so near-miss field names and shorthand values can be corrected."""
    client = StubLLMClient(
        replies=[
            '{"kind": "scene", "scene": {"bad_field": true}}',
            json_reply(valid_planner_output()),
        ]
    )
    planner = LLMPlanner(client)
    await planner.plan_scene(pack, state)

    first = client.requests[0]["payload"]
    assert first["operation"] == "plan_scene"
    assert first["required_output_schema"]["properties"]["kind"]["enum"] == [
        "scene",
        "resolution",
    ]
    assert "never a shorthand string" in first["schema_rule"]
    assert SCHEMA_RULE == first["schema_rule"]

    repair = client.requests[1]["payload"]
    assert repair["operation"] == "repair_contract"
    assert "validation_error" in repair
    assert repair["original_input"]["operation"] == "plan_scene"
    assert repair["required_output_schema"]["properties"]["kind"]["enum"] == [
        "scene",
        "resolution",
    ]
    assert repair["schema_rule"] == SCHEMA_RULE


@pytest.mark.asyncio
async def test_second_contract_failure_raises_model_contract_error(monkeypatch, pack, state):
    client = StubLLMClient(replies=["still invalid", "also invalid"])
    planner = LLMPlanner(client)
    with pytest.raises(ModelContractError, match="structured output failed after repair"):
        await planner.plan_scene(pack, state)
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_planner_rejects_wrong_output_kind(pack, state, offered_choice):
    client = StubLLMClient(replies=[json_reply(valid_planner_output("scene"))])
    planner = LLMPlanner(client)
    with pytest.raises(ModelContractError, match="non-resolution"):
        await planner.resolve_action(pack, state, offered_choice)
