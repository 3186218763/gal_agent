"""Tests for the streaming advance method on RuntimeService."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.story.api import AppDependencies, ScriptPackRegistry
from src.story.runtime.contracts import (
    ActionResolution,
    EndingDraft,
)
from src.story.runtime.service import RuntimeService
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import NarrativeBlock, SessionState, initial_session_state
from src.story.storage import StoryEventStore
from tests.story_factories import minimal_script_pack_dict


class FakeStreamingGenerator:
    """Fake StreamingGeneratorPort that yields predetermined blocks."""

    def __init__(
        self,
        blocks: list[dict[str, Any]] | None = None,
        complete: dict[str, Any] | None = None,
    ) -> None:
        if blocks is None:
            self._blocks = [
                {"kind": "narration", "text": "The cafe hums with quiet energy."},
                {"kind": "dialogue", "character_id": "alice", "text": "You came back."},
            ]
        else:
            self._blocks = blocks
        self._complete = complete or {
            "scene_id": "scene_stream_1",
            "terminal": "decision",
            "decision_id": "dec_1",
            "choices": [
                {
                    "option_id": "ask",
                    "action_id": "ask",
                    "label": "Ask about the notebook",
                    "intent": "direct question",
                },
                {
                    "option_id": "observe",
                    "action_id": "observe",
                    "label": "Watch quietly",
                    "intent": "patient observation",
                },
            ],
        }

    async def generate_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        for block in self._blocks:
            yield ("block", block)
        yield ("complete", self._complete)


class FakeWriter:
    async def write_scene(self, pack, state, plan):
        pass  # not used in streaming path

    async def write_ending(self, pack, state, ending):
        return EndingDraft(
            ending_id=ending.id,
            title=ending.title,
            blocks=(NarrativeBlock(kind="narration", text=f"Ending: {ending.title}"),),
        )


def write_test_pack(root: Path) -> Path:
    packs_root = root / "script_packs"
    pack_dir = packs_root / "test_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump(minimal_script_pack_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return packs_root


def build_streaming_deps(
    tmp_path: Path,
    generator: FakeStreamingGenerator | None = None,
) -> AppDependencies:
    packs_root = write_test_pack(tmp_path)
    store = StoryEventStore(tmp_path / "story.db")
    registry = ScriptPackRegistry(packs_root)
    runtime = RuntimeService(
        store,
        planner=_NoOpPlanner(),
        writer=FakeWriter(),
        generator=generator or FakeStreamingGenerator(),
    )
    return AppDependencies(store=store, registry=registry, runtime=runtime)


class _NoOpPlanner:
    """Planner stub -- streaming path does not call the planner."""

    async def plan_scene(self, pack, state):
        raise AssertionError("planner should not be called in streaming path")

    async def resolve_action(self, pack, state, choice):
        return ActionResolution(action_id=choice.action_id, outcome="success")


@pytest.fixture
def streaming_env(tmp_path: Path):
    """Create a session and return (store, pack, session_id, runtime, deps)."""
    deps = build_streaming_deps(tmp_path)
    pack = deps.registry.get("test_pack")
    state = initial_session_state(pack, "stream_session", session_seed=1)
    deps.store.create_session(state)
    return deps, pack


def test_advance_streamed_yields_blocks_then_choices_then_done(streaming_env):
    import asyncio

    deps, pack = streaming_env

    async def run():
        results = []
        async for evt, data in deps.runtime.advance_streamed(pack, "stream_session", 0, "key-001"):
            results.append((evt, data))
        return results

    results = asyncio.run(run())

    event_types = [r[0] for r in results]
    assert event_types == ["block", "block", "choices", "done"]

    blocks = [r[1] for r in results if r[0] == "block"]
    assert len(blocks) == 2
    assert "text" in blocks[0]

    choices_payload = [r[1] for r in results if r[0] == "choices"]
    assert len(choices_payload[0]) == 2

    done_payload = next(r[1] for r in results if r[0] == "done")
    assert done_payload["session_id"] == "stream_session"
    assert done_payload["revision"] == 1


def test_advance_streamed_replays_on_idempotent_replay(streaming_env):
    import asyncio

    deps, pack = streaming_env

    async def collect():
        return [
            (evt, data)
            async for evt, data in deps.runtime.advance_streamed(
                pack, "stream_session", 0, "key-replay"
            )
        ]

    first = asyncio.run(collect())
    second = asyncio.run(collect())

    assert [r[0] for r in first] == ["block", "block", "choices", "done"]
    # Replay should yield the same sequence of event types
    assert [r[0] for r in second] == ["block", "block", "choices", "done"]
    # Done revision must match
    first_done = next(r[1] for r in first if r[0] == "done")
    second_done = next(r[1] for r in second if r[0] == "done")
    assert first_done["revision"] == second_done["revision"]


def test_advance_streamed_raises_without_generator(tmp_path: Path):
    packs_root = write_test_pack(tmp_path)
    store = StoryEventStore(tmp_path / "story.db")
    registry = ScriptPackRegistry(packs_root)
    runtime = RuntimeService(store, _NoOpPlanner(), FakeWriter(), generator=None)
    pack = registry.get("test_pack")
    state = initial_session_state(pack, "s1", session_seed=1)
    store.create_session(state)

    import asyncio

    async def run():
        async for _ in runtime.advance_streamed(pack, "s1", 0, "k"):
            pass

    with pytest.raises(RuntimeError, match="streaming generator is not configured"):
        asyncio.run(run())


def test_advance_streamed_empty_blocks_raises(tmp_path: Path):
    gen = FakeStreamingGenerator(blocks=[])
    deps = build_streaming_deps(tmp_path, generator=gen)
    pack = deps.registry.get("test_pack")
    state = initial_session_state(pack, "s_empty", session_seed=1)
    deps.store.create_session(state)

    import asyncio

    async def run():
        async for _ in deps.runtime.advance_streamed(pack, "s_empty", 0, "k-empty"):
            pass

    from src.story.runtime.contracts import RuntimeGenerationUnavailable

    with pytest.raises(RuntimeGenerationUnavailable):
        asyncio.run(run())


def test_advance_streamed_continue_terminal_skips_choices(tmp_path: Path):
    """When terminal='continue', no choices should be emitted."""
    gen = FakeStreamingGenerator(
        blocks=[{"kind": "narration", "text": "Time passes gently."}],
        complete={
            "scene_id": "scene_c1",
            "terminal": "continue",
            "choices": [],
        },
    )
    deps = build_streaming_deps(tmp_path, generator=gen)
    pack = deps.registry.get("test_pack")
    state = initial_session_state(pack, "s_cont", session_seed=1)
    deps.store.create_session(state)

    import asyncio

    async def collect():
        return [
            (evt, data)
            async for evt, data in deps.runtime.advance_streamed(pack, "s_cont", 0, "k-cont")
        ]

    results = asyncio.run(collect())
    event_types = [r[0] for r in results]
    assert event_types == ["block", "done"]
    assert "choices" not in event_types


def test_advance_streamed_ending_path_yields_ending_metadata(tmp_path: Path):
    """When scene_count >= max_scenes, the fallback ending triggers."""
    gen = FakeStreamingGenerator()
    deps = build_streaming_deps(tmp_path, generator=gen)
    pack = deps.registry.get("test_pack")
    state = initial_session_state(pack, "s_end", session_seed=1)
    # Force ending by pushing scene_count to max
    world = state.world.model_copy(update={"scene_count": state.world.max_scenes})
    deps.store.create_session(state.model_copy(update={"world": world}))

    import asyncio

    async def collect():
        return [
            (evt, data)
            async for evt, data in deps.runtime.advance_streamed(pack, "s_end", 0, "k-ending")
        ]

    results = asyncio.run(collect())
    event_types = [r[0] for r in results]
    assert event_types == ["block", "done"]

    done = next(r[1] for r in results if r[0] == "done")
    assert done["ending_id"] == "fallback_ending"
    assert "ending_title" in done


# ---------------------------------------------------------------------------
# SSE HTTP endpoint tests (Task 4)
# ---------------------------------------------------------------------------


def _parse_sse_lines(response) -> list[tuple[str, dict]]:
    """Parse SSE frames from a Starlette TestClient streaming response."""
    import json

    events: list[tuple[str, dict]] = []
    current_event = "message"
    current_data = ""
    for line in response.iter_lines():
        line = line.strip()
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            current_data = line[6:]
        elif line == "":
            if current_data:
                events.append((current_event, json.loads(current_data)))
            current_event = "message"
            current_data = ""
    return events


def test_sse_advance_streams_blocks_and_choices(tmp_path: Path):
    """POST /advance returns text/event-stream with block, choices, done events."""
    from fastapi.testclient import TestClient

    from src.story.api import create_app

    deps = build_streaming_deps(tmp_path)
    client = TestClient(create_app(deps))

    created = client.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 1})
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    with client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "sse-1"},
    ) as resp:
        assert resp.status_code == 200
        events = _parse_sse_lines(resp)

    event_types = [e[0] for e in events]
    assert "block" in event_types
    assert "choices" in event_types
    assert event_types[-1] == "done"

    blocks = [e[1] for e in events if e[0] == "block"]
    assert len(blocks) == 2
    assert "text" in blocks[0]

    choices = next(e[1] for e in events if e[0] == "choices")
    assert len(choices) == 2

    done = next(e[1] for e in events if e[0] == "done")
    assert done["session_id"] == session_id
    assert done["revision"] == 1


def test_sse_advance_idempotent_replay(tmp_path: Path):
    """Replaying the same idempotency key yields the same blocks via SSE."""
    from fastapi.testclient import TestClient

    from src.story.api import create_app

    deps = build_streaming_deps(tmp_path)
    client = TestClient(create_app(deps))

    created = client.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 2})
    session_id = created.json()["session_id"]

    with client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "sse-replay"},
    ) as r1:
        events1 = _parse_sse_lines(r1)

    with client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "sse-replay"},
    ) as r2:
        events2 = _parse_sse_lines(r2)

    blocks1 = [e[1] for e in events1 if e[0] == "block"]
    blocks2 = [e[1] for e in events2 if e[0] == "block"]
    assert len(blocks1) == len(blocks2)
    assert blocks1[0]["text"] == blocks2[0]["text"]


def test_sse_advance_error_sends_error_event(tmp_path: Path):
    """When generation fails, an error SSE event is emitted instead of a crash."""
    from fastapi.testclient import TestClient

    from src.story.api import create_app
    from src.story.runtime.contracts import ModelContractError

    class FailingGenerator:
        async def generate_scene(self, pack, state):
            raise ModelContractError("simulated failure")
            yield  # type: ignore[unreachable]

    packs_root = write_test_pack(tmp_path)
    store = StoryEventStore(tmp_path / "story.db")
    registry = ScriptPackRegistry(packs_root)
    runtime = RuntimeService(store, _NoOpPlanner(), FakeWriter(), generator=FailingGenerator())
    deps = AppDependencies(store=store, registry=registry, runtime=runtime)
    client = TestClient(create_app(deps))

    created = client.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 3})
    session_id = created.json()["session_id"]

    with client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "sse-err"},
    ) as resp:
        assert resp.status_code == 200
        events = _parse_sse_lines(resp)

    error_events = [e for e in events if e[0] == "error"]
    assert len(error_events) == 1
    assert error_events[0][1]["code"] == "generation_unavailable"
