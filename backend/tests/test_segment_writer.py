from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from agents import Runner
from agents.agent_output import AgentOutputSchema
from agents.exceptions import ModelBehaviorError
from agents.models.interface import Model

from src.story.runtime.contracts import (
    ChoicePlan,
    EndingDraft,
    EndingProposal,
    SceneDraft,
    ScenePlan,
    SegmentDraft,
    SegmentPlan,
    SegmentWriterOutput,
    WrittenChoice,
)
from src.story.runtime.segment_writer import SEGMENT_WRITER_INSTRUCTIONS, SdkSegmentWriter
from src.story.script_pack import compile_source
from src.story.state import NarrativeBlock, initial_session_state
from tests.story_factories import minimal_script_pack_dict


class SharedFakeModel(Model):
    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no network in offline tests")

    async def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no network in offline tests")
        if False:  # pragma: no cover
            yield None


@pytest.fixture
def pack():
    return compile_source(minimal_script_pack_dict())


@pytest.fixture
def state(pack):
    return initial_session_state(pack, "session_01", session_seed=42)


@pytest.fixture
def shared_model():
    return SharedFakeModel()


def decision_segment_plan() -> SegmentPlan:
    return SegmentPlan(
        segment_id="seg_01",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="Alice waits at the cafe.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="continue",
            ),
            ScenePlan(
                scene_id="scene_02",
                summary="The protagonist faces a decision.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id="dec_01",
                choices=(
                    ChoicePlan(option_id="opt_ask", action_id="ask", intent="ask directly"),
                    ChoicePlan(option_id="opt_observe", action_id="observe", intent="watch carefully"),
                ),
            ),
        ),
        terminal="decision",
    )


def ending_segment_plan() -> SegmentPlan:
    return SegmentPlan(
        segment_id="seg_end",
        scenes=(
            ScenePlan(
                scene_id="scene_final",
                summary="The story concludes.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="ending",
            ),
        ),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Farewell, Cafe",
            tone="bittersweet",
            terminal_state_summary="They part ways.",
        ),
    )


def valid_decision_draft() -> SegmentDraft:
    return SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="The cafe hummed quietly."),),
            ),
            SceneDraft(
                scene_id="scene_02",
                blocks=(
                    NarrativeBlock(kind="narration", text="Alice looked up."),
                    NarrativeBlock(kind="dialogue", character_id="alice", text="So what will you do?"),
                ),
                choices=(
                    WrittenChoice(option_id="opt_ask", label="Ask her directly"),
                    WrittenChoice(option_id="opt_observe", label="Watch carefully"),
                ),
            ),
        ),
        choices=(
            WrittenChoice(option_id="opt_ask", label="Ask her directly"),
            WrittenChoice(option_id="opt_observe", label="Watch carefully"),
        ),
    )


def valid_ending_draft() -> SegmentDraft:
    return SegmentDraft(
        segment_id="seg_end",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_final",
                blocks=(
                    NarrativeBlock(kind="narration", text="They said goodbye at the cafe door."),
                ),
            ),
        ),
        ending=EndingDraft(
            ending_id="ending_01",
            title="Farewell, Cafe",
            blocks=(NarrativeBlock(kind="narration", text="The story ends here."),),
        ),
    )


def test_segment_writer_output_strict_schema():
    assert AgentOutputSchema(SegmentWriterOutput).is_strict_json_schema() is True


def test_writer_instructions_forbid_adding_facts():
    assert "cannot add" in SEGMENT_WRITER_INSTRUCTIONS.lower() or "must not add" in SEGMENT_WRITER_INSTRUCTIONS.lower()
    assert "fact" in SEGMENT_WRITER_INSTRUCTIONS.lower()


@pytest.mark.asyncio
async def test_writer_returns_decision_draft(monkeypatch, shared_model, pack, state):
    plan = decision_segment_plan()

    async def fake_run(agent, input):
        payload = json.loads(input)
        assert payload["operation"] == "write_segment"
        assert payload["context"]["approved_plan"]["segment_id"] == "seg_01"
        return SimpleNamespace(final_output=SegmentWriterOutput(segment_draft=valid_decision_draft()))

    monkeypatch.setattr(Runner, "run", fake_run)
    writer = SdkSegmentWriter(shared_model)
    draft = await writer.write_segment(pack, state, plan)
    assert draft.segment_id == "seg_01"
    assert len(draft.scene_drafts) == 2
    assert len(draft.choices) == 2
    assert draft.ending is None
    assert writer.agent.model is shared_model


@pytest.mark.asyncio
async def test_writer_returns_ending_draft(monkeypatch, shared_model, pack, state):
    plan = ending_segment_plan()

    async def fake_run(agent, input):
        payload = json.loads(input)
        assert payload["context"]["ending_proposal"]["title"] == "Farewell, Cafe"
        return SimpleNamespace(final_output=SegmentWriterOutput(segment_draft=valid_ending_draft()))

    monkeypatch.setattr(Runner, "run", fake_run)
    writer = SdkSegmentWriter(shared_model)
    draft = await writer.write_segment(pack, state, plan)
    assert draft.ending is not None
    assert draft.ending.title == "Farewell, Cafe"
    assert len(draft.ending.blocks) >= 1


@pytest.mark.asyncio
async def test_writer_contract_error_retries_once(monkeypatch, shared_model, pack, state):
    plan = decision_segment_plan()
    calls = 0

    async def fake_run(agent, input):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelBehaviorError("bad output")
        return SimpleNamespace(final_output=SegmentWriterOutput(segment_draft=valid_decision_draft()))

    monkeypatch.setattr(Runner, "run", fake_run)
    writer = SdkSegmentWriter(shared_model)
    draft = await writer.write_segment(pack, state, plan)
    assert draft.segment_id == "seg_01"
    assert calls == 2


@pytest.mark.asyncio
async def test_writer_per_character_context_scoping(monkeypatch, shared_model, pack, state):
    """Verify that the writer context does not leak secrets across characters."""
    plan = decision_segment_plan()
    captured_context = None

    async def fake_run(agent, input):
        nonlocal captured_context
        payload = json.loads(input)
        captured_context = payload["context"]
        return SimpleNamespace(final_output=SegmentWriterOutput(segment_draft=valid_decision_draft()))

    monkeypatch.setattr(Runner, "run", fake_run)
    writer = SdkSegmentWriter(shared_model)
    await writer.write_segment(pack, state, plan)
    # Each character should only see their own known_facts
    for char in captured_context["characters"]:
        for known in char["known_facts"]:
            # The character should only know facts they are authorized to know
            char_runtime = state.characters[char["id"]]
            assert known["id"] in char_runtime.knowledge
