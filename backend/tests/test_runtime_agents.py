from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from agents import Runner
from agents.exceptions import ModelBehaviorError
from agents.models.interface import Model

from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    EndingDraft,
    ModelContractError,
    PlannerOutput,
    SceneDraft,
    ScenePlan,
    WriterOutput,
    WrittenChoice,
)
from src.story.runtime.model import run_with_contract_retry
from src.story.runtime.planner import SdkPlanner
from src.story.runtime.writer import SdkWriter
from src.story.script_pack import compile_source
from src.story.state import NarrativeBlock, PresentedChoice, initial_session_state
from tests.story_factories import minimal_script_pack_dict


class SharedFakeModel(Model):
    """Offline stand-in accepted by Agent model type checks."""

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network model calls are not allowed in offline tests")

    async def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network model calls are not allowed in offline tests")
        if False:  # pragma: no cover - make this an async generator signature-compatible
            yield None


@pytest.fixture
def pack():
    return compile_source(minimal_script_pack_dict())


@pytest.fixture
def state(pack):
    return initial_session_state(pack, "session_01", session_seed=42)


@pytest.fixture
def decision_state(state):
    return state


@pytest.fixture
def offered_choice() -> PresentedChoice:
    return PresentedChoice(
        id="ask_alice",
        action_id="ask",
        label="Ask Alice",
        intent="ask directly",
    )


@pytest.fixture
def shared_model():
    return SharedFakeModel()


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


def valid_writer_scene_output() -> WriterOutput:
    plan = valid_scene_plan()
    return WriterOutput(
        kind="scene",
        scene=SceneDraft(
            scene_id=plan.scene_id,
            blocks=(NarrativeBlock(kind="narration", text="The cafe hums quietly."),),
            choices=tuple(
                WrittenChoice(option_id=item.option_id, label=item.intent[:80])
                for item in plan.choices
            ),
        ),
    )


def valid_writer_ending_output(ending_id: str = "ally_ending") -> WriterOutput:
    return WriterOutput(
        kind="ending",
        ending=EndingDraft(
            ending_id=ending_id,
            title="Together",
            blocks=(NarrativeBlock(kind="narration", text="They leave together."),),
        ),
    )


@pytest.mark.asyncio
async def test_planner_uses_one_agent_for_scene_and_resolution(
    monkeypatch, shared_model, pack, state, decision_state, offered_choice
):
    async def fake_run(agent, input):
        payload = json.loads(input)
        if payload["operation"] == "plan_scene":
            return SimpleNamespace(final_output=valid_planner_output("scene"))
        if payload["operation"] == "resolve_action":
            return SimpleNamespace(
                final_output=PlannerOutput(
                    kind="resolution",
                    resolution=ActionResolution(
                        action_id=payload["choice"]["action_id"],
                        outcome="success",
                    ),
                )
            )
        raise AssertionError(f"unexpected operation: {payload['operation']}")

    monkeypatch.setattr(Runner, "run", fake_run)
    planner = SdkPlanner(shared_model)
    scene = await planner.plan_scene(pack, state)
    resolution = await planner.resolve_action(pack, decision_state, offered_choice)
    assert scene.scene_id == "scene_01"
    assert resolution.action_id == offered_choice.action_id
    assert planner.agent.model is shared_model


@pytest.mark.asyncio
async def test_writer_uses_same_model_instance(monkeypatch, shared_model, pack, state):
    plan = valid_scene_plan()
    ending = next(item for item in pack.source.endings if item.id == "ally_ending")

    async def fake_run(agent, input):
        payload = json.loads(input)
        if payload["operation"] == "write_scene":
            return SimpleNamespace(final_output=valid_writer_scene_output())
        if payload["operation"] == "write_ending":
            return SimpleNamespace(
                final_output=valid_writer_ending_output(ending.id)
            )
        raise AssertionError(f"unexpected operation: {payload['operation']}")

    monkeypatch.setattr(Runner, "run", fake_run)
    writer = SdkWriter(shared_model)
    assert writer.agent.model is shared_model
    draft = await writer.write_scene(pack, state, plan)
    ending_draft = await writer.write_ending(pack, state, ending)
    assert draft.scene_id == plan.scene_id
    assert ending_draft.ending_id == ending.id


@pytest.mark.asyncio
async def test_contract_error_retries_once_without_chat_fallback(
    monkeypatch, shared_model, pack, state
):
    calls = 0

    async def fake_run(agent, input):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelBehaviorError("invalid structured output")
        return SimpleNamespace(final_output=valid_planner_output())

    monkeypatch.setattr(Runner, "run", fake_run)
    planner = SdkPlanner(shared_model)
    await planner.plan_scene(pack, state)
    assert calls == 2


@pytest.mark.asyncio
async def test_second_contract_failure_raises_model_contract_error(monkeypatch, shared_model):
    calls = 0

    async def fake_run(agent, input):
        nonlocal calls
        calls += 1
        raise ModelBehaviorError("still invalid")

    monkeypatch.setattr(Runner, "run", fake_run)
    agent = SimpleNamespace(name="stub")
    with pytest.raises(ModelContractError, match="structured output failed after repair"):
        await run_with_contract_retry(agent, json.dumps({"operation": "plan_scene"}), PlannerOutput)
    assert calls == 2


@pytest.mark.asyncio
async def test_validation_error_is_repaired_once(monkeypatch, shared_model, pack, state):
    calls = 0

    async def fake_run(agent, input):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(final_output={"kind": "scene"})  # missing scene payload
        return SimpleNamespace(final_output=valid_planner_output())

    monkeypatch.setattr(Runner, "run", fake_run)
    planner = SdkPlanner(shared_model)
    scene = await planner.plan_scene(pack, state)
    assert scene.scene_id == "scene_01"
    assert calls == 2
