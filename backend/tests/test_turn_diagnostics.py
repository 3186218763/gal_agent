"""Seam tests: bounded model-call timeout and per-turn diagnostics persistence."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

import src.story.runtime.model as model_module
from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.config import LLMSettings
from src.story.runtime.contracts import (
    ModelTimeoutError,
    RuntimeGenerationUnavailable,
)
from src.story.runtime.semantic_judge import JudgeFinding, JudgeFindings
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.script_pack.compiler import compile_source
from src.story.state import initial_session_state
from src.story.storage import SessionNotFound, StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeSegmentWriter,
    budget_test_pack_dict,
)
from tests.test_turn_orchestrator import _collect_events, _valid_unified_output


class ToyOutput(BaseModel):
    answer: str


def _fast_settings(timeout_seconds: float) -> LLMSettings:
    return LLMSettings(
        provider="everygpt",
        api_key=SecretStr("test-secret"),
        base_url="https://api.everygpt.site/v1",
        model="gemini-3.7-flash",
        api="chat_completions",
        timeout_seconds=timeout_seconds,
        max_retries=1,
    )


async def test_hung_model_call_fails_fast_with_named_timeout(monkeypatch):
    """A provider that never answers fails at the deadline, not after retries."""
    client = model_module.LLMClient(_fast_settings(0.2))

    async def hung(self, instructions: str, user: str, schema: dict) -> str:
        await asyncio.sleep(30)
        return "{}"

    monkeypatch.setattr(model_module.LLMClient, "_ask_once", hung)
    with pytest.raises(ModelTimeoutError) as excinfo:
        await client.complete_structured(
            instructions="reply",
            payload={"prompt": "anything"},
            output_type=ToyOutput,
        )
    assert "deadline" in str(excinfo.value)
    assert isinstance(excinfo.value, RuntimeGenerationUnavailable)


async def test_none_deadline_disables_the_wrapper(monkeypatch):
    client = model_module.LLMClient(_fast_settings(45))
    client._deadline_seconds = None

    async def slow_but_finite(self, instructions: str, user: str, schema: dict) -> str:
        await asyncio.sleep(0.05)
        return json.dumps({"answer": "ok"})

    monkeypatch.setattr(model_module.LLMClient, "_ask_once", slow_but_finite)
    result = await client.complete_structured(
        instructions="reply",
        payload={"prompt": "anything"},
        output_type=ToyOutput,
    )
    assert result.answer == "ok"


class TimeoutUnifiedAgent:
    async def generate(self, pack, state, pacing, *, rejection_notes=(), pending_choice=None):
        raise ModelTimeoutError("model call exceeded the 180s deadline")


def _orchestrator_with(store, unified_agent=None, semantic_judge=None, planner=None):
    return TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=FakeSegmentWriter(),
        guard=FakeGuard(),
        completion_judge=CompletionJudge(),
        planner=planner if planner is not None else FakePlanner(),
        unified_agent=unified_agent,
        semantic_judge=semantic_judge,
    )


def _new_session(tmp_path: Path, name: str):
    pack = compile_source(budget_test_pack_dict())
    store = StoryEventStore(tmp_path / f"{name}.db")
    store.create_session(initial_session_state(pack, "s1", session_seed=42))
    return pack, store


def test_timeout_during_generation_records_failed_diagnostics(tmp_path: Path):
    pack, store = _new_session(tmp_path, "diag_timeout")
    orch = _orchestrator_with(store, unified_agent=TimeoutUnifiedAgent())

    with pytest.raises(ModelTimeoutError) as excinfo:
        _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-hang", None))
    # the timeout message survives — it is not wrapped into a generic failure
    assert "deadline" in str(excinfo.value)

    records = store.load_turn_diagnostics("s1")
    assert len(records) == 1
    record = records[0]
    assert record["command_kind"] == "generate_opening"
    assert record["outcome"] == "failed"
    assert "ModelTimeoutError" in record["error"]
    assert "deadline" in record["error"]
    stages = {stage["name"]: stage for stage in record["stages"]}
    assert stages["generating"]["attempts"] == 1


def test_committed_turn_persists_stage_timings(tmp_path: Path):
    pack, store = _new_session(tmp_path, "diag_ok")
    orch = _orchestrator_with(store)

    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-ok", None))
    assert any(t == "segment_ready" for t, _data in events)

    records = store.load_turn_diagnostics("s1")
    assert len(records) == 1
    record = records[0]
    assert record["outcome"] == "committed"
    assert record["error"] is None
    assert record["regenerations"] == 0
    assert record["judge_findings"] == []
    assert record["guard_violations"] == []
    assert [stage["name"] for stage in record["stages"]] == [
        "generating",
        "validating",
        "committing",
    ]
    assert all(stage["duration_ms"] >= 0 for stage in record["stages"])
    assert all(stage["attempts"] == 1 for stage in record["stages"])

    # a fresh store handle sees the same records — they survive the turn
    reopened = StoryEventStore(tmp_path / "diag_ok.db")
    assert reopened.load_turn_diagnostics("s1") == records


def test_judge_rejection_persists_findings_and_regeneration(tmp_path: Path):
    pack, store = _new_session(tmp_path, "diag_retry")

    class RejectOnceJudge:
        async def judge_segment(self, pack, state, plan, draft, pending_choice=None):
            if RejectOnceJudge.calls == 0:
                RejectOnceJudge.calls += 1
                return JudgeFindings(
                    findings=(
                        JudgeFinding(
                            kind="choice_reversal",
                            severity="blocking",
                            detail="the segment ignores the player's committed stance",
                        ),
                    )
                )
            return JudgeFindings()

    RejectOnceJudge.calls = 0
    orch = _orchestrator_with(
        store, unified_agent=_RecordingAgent(), semantic_judge=RejectOnceJudge()
    )

    events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-retry", None))
    assert any(t == "segment_ready" for t, _data in events)

    record = store.load_turn_diagnostics("s1")[0]
    assert record["outcome"] == "committed"
    assert record["regenerations"] == 1
    assert record["judge_findings"] == [
        {
            "kind": "choice_reversal",
            "detail": "the segment ignores the player's committed stance",
            "block_index": None,
        }
    ]
    stages = {stage["name"]: stage for stage in record["stages"]}
    assert stages["generating"]["attempts"] == 2
    assert stages["validating"]["attempts"] == 2


class _RecordingAgent:
    """Unified agent producing a validator-clean proposal every time."""

    async def generate(self, pack, state, pacing, *, rejection_notes=(), pending_choice=None):
        return await _valid_unified_output(pack, state, pacing)


def test_planner_timeout_keeps_timeout_message(tmp_path: Path):
    pack, store = _new_session(tmp_path, "diag_planner_timeout")

    class TimeoutPlanner(FakePlanner):
        async def resolve_action(self, pack, state, choice, rejection_notes=()):
            raise ModelTimeoutError("model call exceeded the 180s deadline")

    orch = _orchestrator_with(store, planner=TimeoutPlanner())
    opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
    ready = next(data for t, data in opening if t == "segment_ready")
    choice_id = ready["choices"][0]["id"]

    with pytest.raises(ModelTimeoutError) as excinfo:
        _collect_events(
            orch.execute_turn(pack, "s1", ready["revision"], f"cmd-choice-{choice_id}", choice_id)
        )
    assert "deadline" in str(excinfo.value)

    record = store.load_turn_diagnostics("s1")[-1]
    assert record["command_kind"] == "resolve_consequence"
    assert record["outcome"] == "failed"
    assert "ModelTimeoutError" in record["error"]
    stages = {stage["name"]: stage for stage in record["stages"]}
    assert stages["planning"]["attempts"] == 1


def test_diagnostics_never_touch_the_event_stream(tmp_path: Path):
    """Diagnostics live beside the stream: no extra events, no revision shift."""
    pack, store = _new_session(tmp_path, "diag_stream")
    orch = _orchestrator_with(store, unified_agent=TimeoutUnifiedAgent())

    with pytest.raises(ModelTimeoutError):
        _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-hang", None))
    assert store.load_turn_diagnostics("s1"), "diagnostics must be persisted"

    state = store.load_session("s1")
    envelopes = store.load_events("s1")
    assert len(envelopes) == state.revision
    assert not any("diagnostics" in envelope.model_dump_json() for envelope in envelopes)


def test_append_turn_diagnostics_requires_existing_session(tmp_path: Path):
    store = StoryEventStore(tmp_path / "diag_missing.db")
    with pytest.raises(SessionNotFound):
        store.append_turn_diagnostics(
            "ghost",
            {"command_id": "x", "command_kind": "generate_opening", "outcome": "failed"},
        )


def test_diagnostics_command_dumps_records(tmp_path: Path, capsys):
    from src.story.cli import main

    _pack, store = _new_session(tmp_path, "diag_cli")
    store.append_turn_diagnostics(
        "s1",
        {
            "command_id": "cmd-1",
            "command_kind": "generate_opening",
            "outcome": "committed",
            "stages": [{"name": "generating", "duration_ms": 5, "attempts": 1}],
        },
    )

    assert main(["diagnostics", "s1", "--database", str(tmp_path / "diag_cli.db")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["count"] == 1
    assert output["records"][0]["command_id"] == "cmd-1"
    assert output["records"][0]["stages"][0]["name"] == "generating"
