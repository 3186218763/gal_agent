# Streaming Galgame Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace batch scene generation with a streaming producer-consumer architecture where the LLM generates text blocks that flow to the player via SSE, played back with a typewriter effect on click/Enter.

**Architecture:** A single streaming model call (Responses API with `stream=True`) replaces the Planner to Writer to Simulator pipeline. An incremental JSON parser extracts `NarrativeBlock` objects from the token stream and pushes them via SSE to the frontend. The frontend buffers blocks and plays them with a galgame-style typewriter (click or Enter to advance). The event store (claim/commit/release) remains unchanged; events commit atomically at stream end.

**Tech Stack:** Python 3.11+, FastAPI StreamingResponse, OpenAI AsyncClient (Responses API streaming), React 18, TypeScript, SSE (text/event-stream)

## Global Constraints

- Game content language: zh-CN (Chinese), driven by the script pack `identity.language`
- Model: `deepseek-v4-flash` via opencode.ai Responses API
- Timeout: `GAL_LLM_TIMEOUT_SECONDS=90`
- Python `>=3.11`, async, Pydantic v2 `FrozenModel` for all data classes
- Frontend: React 18, TypeScript strict mode, Vitest for tests
- Event sourcing: SQLite append-only store, revision-based optimistic concurrency, idempotency keys
- Ruff lint must pass: `uv run ruff check src/story src/main.py tests`
- No secrets in error messages or logs

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `backend/src/story/runtime/stream_parser.py` | Incremental JSON parser that extracts complete block dicts from a token stream |
| `backend/src/story/runtime/stream_writer.py` | Streaming scene generator using raw OpenAI Responses API with streaming |
| `backend/tests/test_stream_parser.py` | Unit tests for the incremental parser |
| `backend/tests/test_streaming_api.py` | Integration tests for SSE endpoints |
| `frontend/src/stream.ts` | SSE consumption utilities (fetch + ReadableStream parsing) |
| `frontend/src/Playback.tsx` | Galgame playback component (typewriter, buffer, click/Enter) |
| `frontend/src/Playback.css` | Styles for scrolling log and typewriter |

**Modified files:**

| File | Changes |
|------|---------|
| `backend/src/story/runtime/contracts.py` | Add `StreamEvent` type alias, `StreamingGeneratorPort` protocol |
| `backend/src/story/runtime/service.py` | Add `advance_streamed()` async generator method |
| `backend/src/story/api.py` | Replace advance endpoint with SSE `StreamingResponse`; simplify choice endpoint |
| `backend/src/main.py` | Wire `StreamingSceneGenerator` into dependencies |
| `backend/tests/test_v2_api.py` | Update advance/choice tests for SSE format |
| `frontend/src/api.ts` | Remove `advanceSession` / `choose`; keep `createSession`, `fetchSession`, `fetchPack` |
| `frontend/src/App.tsx` | Use `Playback` component, wire streaming flow |
| `frontend/src/App.css` | Remove batch-mode styles, add streaming-specific styles |

---

## Task 1: Incremental JSON Block Parser

**Files:**
- Create: `backend/src/story/runtime/stream_parser.py`
- Test: `backend/tests/test_stream_parser.py`

**Interfaces:**
- Produces: `BlockStreamParser` class with `feed(text: str) -> list[dict]` and `finalize() -> dict | None`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_stream_parser.py`:

```python
"""Tests for incremental JSON block stream parser."""

import json

from src.story.runtime.stream_parser import BlockStreamParser


def test_extracts_blocks_one_at_a_time():
    full = json.dumps({
        "blocks": [
            {"kind": "narration", "text": "Hello world."},
            {"kind": "dialogue", "character_id": "alice", "text": "Hi there."},
        ],
        "terminal": "decision",
        "choices": [{"option_id": "a", "label": "Ask"}],
    })
    parser = BlockStreamParser()
    seen = []
    chunk_size = 10
    for i in range(0, len(full), chunk_size):
        seen.extend(parser.feed(full[i : i + chunk_size]))
    assert len(seen) == 2
    assert seen[0]["kind"] == "narration"
    assert seen[0]["text"] == "Hello world."
    assert seen[1]["kind"] == "dialogue"
    assert seen[1]["character_id"] == "alice"


def test_does_not_yield_partial_blocks():
    parser = BlockStreamParser()
    assert parser.feed('{"blocks": [{"kind": "narration", "text": "') == []
    assert parser.feed('partial') == []
    result = parser.feed(' done"}]')
    assert len(result) == 1
    assert result[0]["text"] == "partial done"


def test_handles_braces_inside_strings():
    parser = BlockStreamParser()
    parser.feed('{"blocks": [{"kind": "narration", "text": "has } brace')
    result = parser.feed(' inside"}]}')
    assert len(result) == 1
    assert result[0]["text"] == "has } brace inside"


def test_handles_escaped_quotes_in_strings():
    parser = BlockStreamParser()
    parser.feed('{"blocks": [{"kind": "narration", "text": "say \\"hello\\"')
    result = parser.feed(' to her"}]}')
    assert len(result) == 1
    assert result[0]["text"] == 'say "hello" to her'


def test_finalize_returns_full_json():
    full = {
        "blocks": [{"kind": "narration", "text": "Done."}],
        "terminal": "continue",
        "choices": [],
    }
    raw = json.dumps(full)
    parser = BlockStreamParser()
    for i in range(0, len(raw), 5):
        parser.feed(raw[i : i + 5])
    result = parser.finalize()
    assert result is not None
    assert result["terminal"] == "continue"


def test_finalize_returns_none_on_invalid_json():
    parser = BlockStreamParser()
    parser.feed("not valid json at all")
    assert parser.finalize() is None


def test_empty_feed_returns_empty():
    parser = BlockStreamParser()
    assert parser.feed("") == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_stream_parser.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.story.runtime.stream_parser'`

- [ ] **Step 3: Write the parser implementation**

Create `backend/src/story/runtime/stream_parser.py`:

```python
"""Incremental JSON parser for streaming model output.

Feeds text deltas and yields complete block dicts as they become
available in the ``blocks`` array of the streamed JSON document.
"""

from __future__ import annotations

import json


class BlockStreamParser:
    """Incrementally parses a JSON token stream to extract block objects.

    Call :meth:`feed` with each text delta.  It returns a list of
    newly-completed block dicts (may be empty).  After the stream ends,
    call :meth:`finalize` to parse the full accumulated buffer.
    """

    def __init__(self) -> None:
        self._buffer: str = ""
        self._search_pos: int | None = None  # position to resume scanning
        self._blocks_done: bool = False

    def feed(self, text: str) -> list[dict]:
        if not text:
            return []
        self._buffer += text
        if self._search_pos is None:
            marker = '"blocks"'
            idx = self._buffer.find(marker)
            if idx == -1:
                return []
            bracket = self._buffer.find("[", idx)
            if bracket == -1:
                return []
            self._search_pos = bracket + 1

        if self._blocks_done:
            return []

        results: list[dict] = []
        while True:
            block, next_pos = self._extract_next(self._search_pos)
            if block is None:
                break
            results.append(block)
            self._search_pos = next_pos
        return results

    def _extract_next(self, start: int) -> tuple[dict | None, int]:
        buf = self._buffer
        brace_pos = buf.find("{", start)
        bracket_pos = buf.find("]", start)

        if brace_pos == -1:
            return None, start
        if bracket_pos != -1 and bracket_pos < brace_pos:
            self._blocks_done = True
            return None, start

        depth = 0
        in_string = False
        escaped = False
        for i in range(brace_pos, len(buf)):
            ch = buf[i]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(buf[brace_pos : i + 1]), i + 1
                    except json.JSONDecodeError:
                        return None, i + 1
        return None, start

    def finalize(self) -> dict | None:
        """Attempt to parse the full accumulated buffer as JSON."""
        try:
            return json.loads(self._buffer)
        except json.JSONDecodeError:
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_stream_parser.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/story/runtime/stream_parser.py backend/tests/test_stream_parser.py
git commit -m "feat: add incremental JSON block stream parser"
```

---

## Task 2: Streaming Scene Generator

**Files:**
- Create: `backend/src/story/runtime/stream_writer.py`
- Modify: `backend/src/story/runtime/contracts.py` (add streaming port)
- Test: `backend/tests/test_stream_writer.py`

**Interfaces:**
- Consumes: `AsyncOpenAI` client, model name (from `ModelBundle`)
- Produces: `StreamingSceneGenerator` with method `async def generate_scene(pack, state) -> AsyncGenerator[tuple[str, dict], None]` yielding `("block", block_dict)` and ending with `("complete", full_result_dict)`

- [ ] **Step 1: Add streaming types to contracts**

Add to `backend/src/story/runtime/contracts.py` (after `WriterPort`):

```python
class StreamingGeneratorPort(Protocol):
    async def generate_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ):  # -> AsyncGenerator[tuple[str, dict], None]
        raise NotImplementedError
```

Add `StreamingGeneratorPort` to `__all__` if present, or just leave it accessible via module import.

- [ ] **Step 2: Write failing tests**

Create `backend/tests/test_stream_writer.py`:

```python
"""Tests for the streaming scene generator."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.story.runtime.stream_writer import (
    STREAMING_WRITER_INSTRUCTIONS,
    StreamingSceneGenerator,
)


def _make_streaming_response(deltas: list[str]):
    """Build a mock async iterator that yields delta events."""

    class DeltaEvent:
        type = "response.output_text.delta"

        def __init__(self, delta: str) -> None:
            self.delta = delta

    events = [DeltaEvent(d) for d in deltas]

    class FakeStream:
        def __init__(self) -> None:
            self._events = list(events)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._events:
                raise StopAsyncIteration
            return self._events.pop(0)

    return FakeStream()


def _minimal_pack_and_state():
    """Build a minimal pack and state for testing."""
    from src.story.script_pack.models import CompiledScriptPack, ScriptPackSource
    from src.story.state import SessionState

    # Use the test factory's minimal pack structure
    from tests.story_factories import minimal_script_pack_dict
    import yaml

    raw = minimal_script_pack_dict()
    # This is a dict; we need a CompiledScriptPack. Use compile.
    from src.story.script_pack.compiler import compile_from_dict
    pack = compile_from_dict(raw, pack_dir_name="test_pack")
    state = SessionState(
        session_id="test-session",
        pack_id="test_pack",
        pack_hash=pack.pack_hash,
    )
    return pack, state


@pytest.mark.asyncio
async def test_generate_scene_yields_blocks_then_complete():
    full_output = json.dumps({
        "blocks": [
            {"kind": "narration", "text": "The cafe hums quietly."},
            {"kind": "dialogue", "character_id": "alice", "text": "Hello there."},
        ],
        "terminal": "decision",
        "decision_id": "d_1",
        "choices": [
            {"option_id": "opt_1", "action_id": "ask", "label": "Ask", "intent": "ask"},
        ],
    })
    # Split into small chunks to simulate streaming
    chunk_size = 8
    deltas = [full_output[i : i + chunk_size] for i in range(0, len(full_output), chunk_size)]

    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(return_value=_make_streaming_response(deltas))

    generator = StreamingSceneGenerator(mock_client, "test-model")
    pack, state = _minimal_pack_and_state()

    events = []
    async for event_type, data in generator.generate_scene(pack, state):
        events.append((event_type, data))

    # Should have 2 block events + 1 complete event
    block_events = [e for e in events if e[0] == "block"]
    complete_events = [e for e in events if e[0] == "complete"]
    assert len(block_events) == 2
    assert len(complete_events) == 1
    assert block_events[0][1]["text"] == "The cafe hums quietly."
    assert block_events[1][1]["character_id"] == "alice"
    assert complete_events[0][1]["terminal"] == "decision"


@pytest.mark.asyncio
async def test_generate_scene_raises_on_unparseable_output():
    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(
        return_value=_make_streaming_response(["not json at all"])
    )

    generator = StreamingSceneGenerator(mock_client, "test-model")
    pack, state = _minimal_pack_and_state()

    from src.story.runtime.contracts import ModelContractError

    with pytest.raises(ModelContractError):
        async for _ in generator.generate_scene(pack, state):
            pass
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_stream_writer.py -v
```

Expected: FAIL with import error.

- [ ] **Step 4: Write the streaming generator**

Create `backend/src/story/runtime/stream_writer.py`:

```python
"""Streaming scene generator using raw OpenAI Responses API."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from src.story.runtime.context import build_condition_context
from src.story.runtime.contracts import ModelContractError
from src.story.runtime.stream_parser import BlockStreamParser
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState

STREAMING_WRITER_INSTRUCTIONS = """\
You are the narrator and dialogue writer for a visual novel game.
Generate immersive narration and character dialogue.

Output ONLY valid JSON in this exact structure:
{"blocks":[{"kind":"narration","text":"..."},{"kind":"dialogue","character_id":"...","text":"..."}],"terminal":"decision","decision_id":"d_N","choices":[{"option_id":"opt_N","action_id":"...","label":"...","intent":"..."}]}

Rules:
- Generate 5-15 blocks alternating between narration and dialogue.
- Use "narration" for descriptive text and inner monologue (no character_id).
- Use "dialogue" with the speaking character's "character_id".
- Present a decision (terminal="decision") roughly every 2-3 scenes with 2-4 choices.
- Between decisions, use terminal="continue" with an empty choices array.
- Each choice must use an action_id from the provided available_actions.
- Write in the specified language and prose style.
- Keep each character's dialogue matching their personality and voice.
- Do NOT output anything outside the JSON.
"""


def _build_scene_prompt(pack: CompiledScriptPack, state: SessionState) -> str:
    """Build the user-input JSON for the streaming model call."""
    source = pack.source
    location_name = next(
        (loc.name for loc in source.world.locations if loc.id == state.world.location_id),
        state.world.location_id,
    )
    characters = []
    for char in source.characters:
        if char.id in state.world.present_character_ids:
            characters.append({
                "id": char.id,
                "name": char.name,
                "public_profile": char.public_profile,
                "personality": char.personality.model_dump(mode="json"),
                "voice": char.voice.model_dump(mode="json"),
                "drives": char.drives,
            })

    recent_blocks = []
    if state.pending_scene is not None:
        recent_blocks = [b.model_dump(mode="json") for b in state.pending_scene.blocks]

    available_actions = sorted(
        pack.action_ids & set(source.protagonist.capabilities)
    )

    return json.dumps({
        "scene_number": state.world.scene_count + 1,
        "phase": state.world.phase.value,
        "location": {"id": state.world.location_id, "name": location_name},
        "premise": source.world.premise,
        "prose_style": source.experience.prose_style,
        "tone": source.experience.tone,
        "forbidden_content": source.experience.forbidden_content,
        "language": source.identity.language,
        "characters": characters,
        "recent_blocks": recent_blocks,
        "available_actions": list(available_actions),
    }, ensure_ascii=False)


class StreamingSceneGenerator:
    """Generates scene content via a single streaming model call.

    Yields ``("block", block_dict)`` for each completed NarrativeBlock,
    then yields ``("complete", full_result_dict)`` with the parsed full
    output including choices.
    """

    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def generate_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        prompt = _build_scene_prompt(pack, state)
        parser = BlockStreamParser()

        stream = await self._client.responses.create(
            model=self._model,
            input=prompt,
            instructions=STREAMING_WRITER_INSTRUCTIONS,
            stream=True,
        )

        async for event in stream:
            event_type = getattr(event, "type", "")
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                blocks = parser.feed(delta)
                for block_dict in blocks:
                    yield ("block", block_dict)

        final = parser.finalize()
        if final is None:
            raise ModelContractError("streaming output could not be parsed as JSON")
        yield ("complete", final)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_stream_writer.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/story/runtime/stream_writer.py backend/src/story/runtime/contracts.py backend/tests/test_stream_writer.py
git commit -m "feat: add streaming scene generator with incremental block parsing"
```

---

## Task 3: Streaming Advance Method on RuntimeService

**Files:**
- Modify: `backend/src/story/runtime/service.py` (add `advance_streamed`)
- Modify: `backend/src/story/runtime/contracts.py` (add `StreamingGeneratorPort` import if needed)
- Modify: `backend/src/story/api.py` (wire `StreamingSceneGenerator` into `default_dependencies`)
- Test: `backend/tests/test_streaming_api.py`

**Interfaces:**
- Consumes: `StreamingSceneGenerator` (from Task 2), existing `StoryEventStore`, existing `SdkPlanner`/`SdkWriter` (for endings)
- Produces: `RuntimeService.advance_streamed()` async generator yielding `tuple[str, Any]` where first element is event type (`"block"`, `"choices"`, `"done"`, `"error"`)

- [ ] **Step 1: Add StreamingSceneGenerator to RuntimeService**

Modify `backend/src/story/runtime/service.py`:

Add import at top:
```python
from collections.abc import AsyncGenerator
from typing import Any
```

Add `generator` parameter to `RuntimeService.__init__`:
```python
class RuntimeService:
    def __init__(
        self,
        store: StoryEventStore,
        planner: PlannerPort,
        writer: WriterPort,
        generator: StreamingGeneratorPort | None = None,
    ) -> None:
        self.store = store
        self.planner = planner
        self.writer = writer
        self.generator = generator
```

Add `StreamingGeneratorPort` to imports from contracts:
```python
from src.story.runtime.contracts import (
    ActionResult,
    DecisionRequired,
    InvalidChoice,
    ModelContractError,
    PackMismatch,
    PlannerPort,
    RuntimeGenerationUnavailable,
    RuntimeRevisionConflict,
    RuntimeScene,
    RuntimeSessionEnded,
    StreamingGeneratorPort,
    WriterPort,
)
```

- [ ] **Step 2: Implement advance_streamed method**

Add this method to `RuntimeService` in `service.py`:

```python
async def advance_streamed(
    self,
    pack: CompiledScriptPack,
    session_id: str,
    expected_revision: int,
    idempotency_key: str,
) -> AsyncGenerator[tuple[str, Any], None]:
    """Streaming version of advance: yields ('block', dict), ('choices', list),
    and ('done', {'session_id': ..., 'revision': ...})."""
    if self.generator is None:
        raise RuntimeError("streaming generator is not configured")

    fingerprint = _command_fingerprint("advance", expected_revision)
    claim = self.store.claim_command(session_id, idempotency_key, "advance", fingerprint)
    if claim.replay_json is not None:
        import json as _json
        replay = _json.loads(claim.replay_json)
        for block in replay.get("blocks", []):
            yield ("block", block)
        choices = replay.get("choices", [])
        if choices:
            yield ("choices", choices)
        yield ("done", {"session_id": session_id, "revision": replay["revision"]})
        return

    try:
        initial = self._load_matching(pack, session_id, expected_revision)
        if initial.status == SessionStatus.ENDED:
            raise RuntimeSessionEnded(session_id)
        if initial.pending_decision is not None:
            raise DecisionRequired(initial.pending_decision.decision_id)

        state = initial
        events: list[StoryEvent] = []
        if state.pending_scene is not None:
            ack = SceneAcknowledged(scene_id=state.pending_scene.scene_id)
            synthetic = EventEnvelope(
                event_id=f"synthetic-ack-{state.session_id}-{state.revision + 1}",
                session_id=state.session_id,
                sequence=state.revision + 1,
                event=ack,
            )
            state = apply_events(state, (synthetic,))
            events.append(ack)

        ending = select_ending(pack, state)
        if ending is not None:
            async for evt, data in self._stream_ending(pack, state, ending):
                yield (evt, data)
            return

        # Stream regular scene
        collected_blocks: list = []
        complete_data: dict[str, Any] | None = None
        async for event_type, data in self.generator.generate_scene(pack, state):
            if event_type == "block":
                collected_blocks.append(data)
                yield ("block", data)
            elif event_type == "complete":
                complete_data = data

        if complete_data is None:
            raise RuntimeGenerationUnavailable("stream ended without complete data")

        # Validate blocks lightly
        if not collected_blocks:
            raise RuntimeGenerationUnavailable("model produced no blocks")
        for blk in collected_blocks:
            if not blk.get("text", "").strip():
                raise RuntimeGenerationUnavailable("empty block text")

        terminal = complete_data.get("terminal", "decision")
        raw_choices = complete_data.get("choices", [])
        if terminal == "decision" and not (2 <= len(raw_choices) <= 4):
            terminal = "continue"
            raw_choices = []

        choice_tuple = tuple(
            PresentedChoice(
                id=c.get("option_id", f"opt_{i}"),
                action_id=c.get("action_id", "observe"),
                label=c.get("label", "..."),
                intent=c.get("intent", ""),
                target_character_id=c.get("target_character_id"),
                preview=c.get("preview"),
            )
            for i, c in enumerate(raw_choices)
        )

        phase = next_phase(state)
        if phase is not None:
            events.append(PhaseAdvanced(phase=phase))

        committed = SceneCommitted(
            scene_id=complete_data.get("scene_id", f"scene_{state.revision + 1}"),
            terminal=terminal,
            location_id=state.world.location_id,
            present_character_ids=state.world.present_character_ids,
            blocks=tuple(
                NarrativeBlock(
                    kind=b.get("kind", "narration"),
                    text=b["text"],
                    character_id=b.get("character_id"),
                )
                for b in collected_blocks
            ),
            decision_id=complete_data.get("decision_id") if terminal == "decision" else None,
            choices=choice_tuple,
        )
        events.append(committed)

        def result_factory(updated: SessionState, envelopes) -> str:
            import json as _json
            scene_event = next(
                e for e in envelopes if isinstance(e.event, SceneCommitted)
            )
            return RuntimeScene.from_committed(updated, scene_event.event).model_dump_json()

        updated_state, _, result_json = self.store.commit_command(
            session_id,
            idempotency_key,
            "advance",
            fingerprint,
            expected_revision,
            tuple(events),
            result_factory,
        )

        if choice_tuple:
            yield ("choices", [c.model_dump(mode="json") for c in choice_tuple])
        yield ("done", {"session_id": session_id, "revision": updated_state.revision})

    except Exception:
        self.store.release_command(session_id, idempotency_key, "advance", fingerprint)
        raise
```

Also add the `_stream_ending` helper to `RuntimeService`:

```python
async def _stream_ending(
    self,
    pack: CompiledScriptPack,
    state: SessionState,
    ending: EndingSource,
) -> AsyncGenerator[tuple[str, Any], None]:
    """Stream ending blocks using the batch writer."""
    from src.story.state import (
        EndingEntered,
        EndingRuntime,
        SessionEnded,
    )

    draft = await self.writer.write_ending(pack, state, ending)
    if draft.ending_id != ending.id:
        raise ModelContractError("writer changed ending id")

    for block in draft.blocks:
        yield ("block", block.model_dump(mode="json"))

    ending_runtime = EndingRuntime(
        ending_id=ending.id,
        entered_at_revision=state.revision + 1,
        required_payoffs=ending.required_outcomes,
        final_scene_budget=1,
        title=draft.title,
        blocks=draft.blocks,
    )
    committed = SceneCommitted(
        scene_id=f"ending_{ending.id}_{state.revision + 1}",
        terminal="ending",
        location_id=state.world.location_id,
        present_character_ids=state.world.present_character_ids,
        blocks=draft.blocks,
    )
    events = (
        EndingEntered(ending=ending_runtime),
        committed,
        SessionEnded(ending_id=ending.id),
    )

    fingerprint = _command_fingerprint("advance", state.revision)
    # Use the store directly since we're in the ending path
    import json as _json

    def result_factory(updated: SessionState, envelopes) -> str:
        scene_event = next(
            e for e in envelopes if isinstance(e.event, SceneCommitted)
        )
        return RuntimeScene.from_committed(updated, scene_event.event).model_dump_json()

    updated_state, _, _ = self.store.commit_command(
        state.session_id,
        "",  # idempotency key already claimed by caller
        "advance",
        fingerprint,
        state.revision,
        events,
        result_factory,
    )
    yield ("done", {
        "session_id": state.session_id,
        "revision": updated_state.revision,
        "ending_id": ending.id,
        "ending_title": draft.title,
    })
```

**Important:** Add these imports to the top of `service.py`:
```python
from src.story.state import NarrativeBlock, PresentedChoice
```
These are already exported from `src.story.state.__init__`.

- [ ] **Step 3: Wire StreamingSceneGenerator into default_dependencies**

Modify `backend/src/story/api.py` `default_dependencies()`:

```python
def default_dependencies() -> AppDependencies:
    settings = OpenCodeGoSettings.from_env()
    bundle = build_model_bundle(settings)
    store = StoryEventStore(Path(os.getenv("GAL_DATABASE_PATH", "data/story-v2.db")))
    registry = ScriptPackRegistry(Path(os.getenv("GAL_SCRIPT_PACK_ROOT", "script_packs")))
    from src.story.runtime.stream_writer import StreamingSceneGenerator
    runtime = RuntimeService(
        store,
        SdkPlanner(bundle.model),
        SdkWriter(bundle.model),
        StreamingSceneGenerator(bundle.client, settings.model),
    )
    return AppDependencies(store=store, registry=registry, runtime=runtime)
```

- [ ] **Step 4: Run existing tests to check nothing is broken**

```bash
cd backend && uv run pytest tests/test_v2_api.py -v
```

Expected: Existing tests still pass (they use `FakePlanner` and `FakeWriter`, `generator=None` is fine for old advance method).

**Note:** If existing tests create `RuntimeService` without a `generator` argument, they will still work because `generator` defaults to `None`. The old `advance()` method does not use it.

- [ ] **Step 5: Commit**

```bash
git add backend/src/story/runtime/service.py backend/src/story/api.py
git commit -m "feat: add streaming advance method to RuntimeService"
```

---

## Task 4: SSE API Endpoints

**Files:**
- Modify: `backend/src/story/api.py` (replace advance endpoint with SSE)
- Test: `backend/tests/test_streaming_api.py`

**Interfaces:**
- Produces: `POST /api/v2/sessions/{id}/advance` returns `text/event-stream` with events: `block`, `choices`, `done`, `error`

- [ ] **Step 1: Write failing SSE endpoint tests**

Create `backend/tests/test_streaming_api.py`:

```python
"""Integration tests for SSE streaming endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from src.story.api import AppDependencies, ScriptPackRegistry, create_app
from src.story.runtime.service import RuntimeService
from src.story.storage import StoryEventStore
from tests.story_factories import minimal_script_pack_dict


class FakeStreamingGenerator:
    """Fake generator that yields canned blocks."""

    def __init__(self, blocks: list[dict], complete: dict):
        self._blocks = blocks
        self._complete = complete

    async def generate_scene(self, pack, state):
        for blk in self._blocks:
            yield ("block", blk)
        yield ("complete", self._complete)


def _write_test_pack(root: Path) -> Path:
    packs_root = root / "script_packs"
    pack_dir = packs_root / "test_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump(minimal_script_pack_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return packs_root


def _build_deps(tmp_path: Path, generator=None):
    from tests.test_v2_api import FakePlanner, FakeWriter
    packs_root = _write_test_pack(tmp_path)
    store = StoryEventStore(tmp_path / "story.db")
    registry = ScriptPackRegistry(packs_root)
    runtime = RuntimeService(
        store,
        FakePlanner(),
        FakeWriter(),
        generator or FakeStreamingGenerator(
            blocks=[
                {"kind": "narration", "text": "The cafe hums quietly."},
                {"kind": "dialogue", "character_id": "alice", "text": "Hello there."},
            ],
            complete={
                "terminal": "decision",
                "decision_id": "d_1",
                "choices": [
                    {"option_id": "opt_1", "action_id": "ask", "label": "Ask", "intent": "ask"},
                    {"option_id": "opt_2", "action_id": "observe", "label": "Observe", "intent": "watch"},
                ],
            },
        ),
    )
    return AppDependencies(store=store, registry=registry, runtime=runtime)


def _parse_sse_lines(lines: list[str]) -> list[tuple[str, dict]]:
    """Parse SSE lines into (event_type, data) pairs."""
    events = []
    current_event = "message"
    current_data = ""
    for line in lines:
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


@pytest.fixture
def streaming_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(_build_deps(tmp_path)))


def test_advance_streams_blocks_then_choices(streaming_client: TestClient):
    created = streaming_client.post(
        "/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 1}
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    with streaming_client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "k-1"},
    ) as response:
        assert response.status_code == 200
        lines = [line.strip() for line in response.iter_lines()]
        events = _parse_sse_lines(lines)

    block_events = [e for e in events if e[0] == "block"]
    choice_events = [e for e in events if e[0] == "choices"]
    done_events = [e for e in events if e[0] == "done"]

    assert len(block_events) == 2
    assert block_events[0][1]["text"] == "The cafe hums quietly."
    assert len(choice_events) == 1
    assert len(choice_events[0][1]) == 2
    assert len(done_events) == 1
    assert done_events[0][1]["revision"] > 0


def test_advance_replay_sends_same_blocks(streaming_client: TestClient, tmp_path: Path):
    created = streaming_client.post(
        "/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 2}
    )
    session_id = created.json()["session_id"]

    # First call
    with streaming_client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "replay-k"},
    ) as r1:
        lines1 = [line.strip() for line in r1.iter_lines()]
        events1 = _parse_sse_lines(lines1)

    # Replay with same key
    with streaming_client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "replay-k"},
    ) as r2:
        lines2 = [line.strip() for line in r2.iter_lines()]
        events2 = _parse_sse_lines(lines2)

    block1 = [e for e in events1 if e[0] == "block"]
    block2 = [e for e in events2 if e[0] == "block"]
    assert len(block1) == len(block2)
    assert block1[0][1]["text"] == block2[0][1]["text"]


def test_advance_error_sends_error_event(tmp_path: Path):
    from tests.test_v2_api import FakePlanner, FakeWriter
    from src.story.runtime.contracts import ModelContractError

    class FailingGenerator:
        async def generate_scene(self, pack, state):
            raise ModelContractError("simulated failure")
            yield  # make it a generator

    packs_root = _write_test_pack(tmp_path)
    store = StoryEventStore(tmp_path / "story.db")
    registry = ScriptPackRegistry(packs_root)
    runtime = RuntimeService(store, FakePlanner(), FakeWriter(), FailingGenerator())
    deps = AppDependencies(store=store, registry=registry, runtime=runtime)
    client = TestClient(create_app(deps))

    created = client.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 3})
    session_id = created.json()["session_id"]

    with client.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "err-k"},
    ) as response:
        assert response.status_code == 200
        lines = [line.strip() for line in response.iter_lines()]
        events = _parse_sse_lines(lines)

    error_events = [e for e in events if e[0] == "error"]
    assert len(error_events) == 1
    assert error_events[0][1]["code"] == "generation_unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_streaming_api.py -v
```

Expected: FAIL — the advance endpoint still returns JSON, not SSE.

- [ ] **Step 3: Replace the advance endpoint with SSE**

In `backend/src/story/api.py`, add imports:

```python
import json
from fastapi.responses import StreamingResponse
```

Replace the `advance` route function with:

```python
    @app.post(
        "/api/v2/sessions/{session_id}/advance",
        response_class=StreamingResponse,
    )
    async def advance(session_id: str, command: RevisionRequest):
        pack = deps.registry.get(
            deps.store.load_session(session_id).pack_id
        )

        async def event_stream():
            try:
                async for event_type, data in deps.runtime.advance_streamed(
                    pack,
                    session_id,
                    command.expected_revision,
                    command.idempotency_key,
                ):
                    yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            except (RuntimeRevisionConflict, RevisionConflict):
                yield f"event: error\ndata: {json.dumps({'code': 'revision_conflict'})}\n\n"
            except DecisionRequired:
                yield f"event: error\ndata: {json.dumps({'code': 'decision_required'})}\n\n"
            except RuntimeSessionEnded:
                yield f"event: error\ndata: {json.dumps({'code': 'session_ended'})}\n\n"
            except (OpenAIError, RuntimeGenerationUnavailable) as exc:
                logger.warning("advance stream failed: %s", exc)
                yield f"event: error\ndata: {json.dumps({'code': 'generation_unavailable'})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_streaming_api.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Run full backend test suite**

```bash
cd backend && uv run pytest tests/ -q
```

Some old tests in `test_v2_api.py` that test the advance endpoint will now fail because the endpoint returns SSE instead of JSON. Those will be migrated in Task 5.

- [ ] **Step 6: Commit**

```bash
git add backend/src/story/api.py backend/tests/test_streaming_api.py
git commit -m "feat: SSE streaming endpoint for scene advance"
```

---

## Task 5: Migrate Old Backend Tests

**Files:**
- Modify: `backend/tests/test_v2_api.py`

**Interfaces:**
- No new interfaces; this task updates existing tests to consume SSE.

- [ ] **Step 1: Identify tests that need updating**

The following tests in `test_v2_api.py` call `/advance` and expect JSON:
- `test_create_advance_and_choose_v2_session` — calls advance, checks `payload["choices"]`
- `test_repeated_advance_with_same_key_replays_without_extra_events` — checks replay returns same JSON
- `_decision_bundle` fixture — calls advance and parses JSON response
- `test_generation_contract_failure_is_retryable_and_redacted` — checks 503 status
- `test_provider_failure_is_redacted` — checks 503 status

Tests that DON'T need changes (they test choice/health/session endpoints which are unchanged):
- `test_health_reports_v2`
- `test_get_session_returns_created_state`
- `test_unknown_pack_and_session_return_404`
- etc.

- [ ] **Step 2: Update _decision_bundle fixture to parse SSE**

Replace the `_decision_bundle` fixture:

```python
@pytest.fixture
def _decision_bundle(tmp_path: Path) -> tuple[TestClient, SimpleNamespace]:
    app = create_app(build_test_dependencies(tmp_path))
    http = TestClient(app)
    created = http.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 11},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    # Advance via SSE and collect events
    blocks, choices, revision = stream_advance(http, session_id, 0, "req-00")
    session = SimpleNamespace(
        id=session_id,
        revision=revision,
        choices=choices,
    )
    return http, session


def stream_advance(http: TestClient, session_id: str, rev: int, key: str):
    """Helper: POST to advance and parse SSE response."""
    with http.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": rev, "idempotency_key": key},
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response)
    blocks = [e[1] for e in events if e[0] == "block"]
    choices_list = [e[1] for e in events if e[0] == "choices"]
    done = [e[1] for e in events if e[0] == "done"]
    choices = choices_list[0] if choices_list else []
    revision = done[0]["revision"] if done else rev
    return blocks, choices, revision


def _parse_sse(response):
    events = []
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
```

- [ ] **Step 3: Update test_create_advance_and_choose_v2_session**

```python
def test_create_advance_and_choose_v2_session(tmp_path: Path):
    app = create_app(build_test_dependencies(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 17},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    blocks, choices, revision = stream_advance(client, session_id, 0, "req-00")
    assert len(choices) == 2

    chosen = client.post(
        f"/api/v2/sessions/{session_id}/choices/{choices[0]['id']}",
        json={"expected_revision": revision, "idempotency_key": "req-01"},
    )
    assert chosen.status_code == 200
```

- [ ] **Step 4: Update replay test**

```python
def test_repeated_advance_with_same_key_replays_without_extra_events(tmp_path: Path):
    app = create_app(build_test_dependencies(tmp_path))
    http = TestClient(app)
    created = http.post("/api/v2/sessions", json={"pack_id": "test_pack", "session_seed": 2})
    session_id = created.json()["session_id"]

    blocks1, choices1, rev1 = stream_advance(http, session_id, 0, "advance-1")
    blocks2, choices2, rev2 = stream_advance(http, session_id, 0, "advance-1")

    assert rev1 == rev2
    assert [b["text"] for b in blocks1] == [b["text"] for b in blocks2]

    session = http.get(f"/api/v2/sessions/{session_id}").json()
    assert session["revision"] == rev1
```

- [ ] **Step 5: Update failure tests**

For `test_provider_failure_is_redacted` and `test_generation_contract_failure_is_retryable_and_redacted`: these tests now get an SSE stream with an `error` event instead of a 503 JSON response. Update them:

```python
def test_generation_contract_failure_is_retryable_and_redacted(tmp_path: Path):
    deps = build_test_dependencies(tmp_path, planner=ContractFailingPlanner(), writer=FakeWriter())
    http = TestClient(create_app(deps))
    created = http.post(
        "/api/v2/sessions",
        json={"pack_id": "test_pack", "session_seed": 13},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    with http.stream(
        "POST",
        f"/api/v2/sessions/{session_id}/advance",
        json={"expected_revision": 0, "idempotency_key": "advance-1"},
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response)
    error_events = [e for e in events if e[0] == "error"]
    assert len(error_events) == 1
    assert error_events[0][1]["code"] == "generation_unavailable"
    assert "contract failed" not in json.dumps(events)
    loaded = http.get(f"/api/v2/sessions/{session_id}")
    assert loaded.status_code == 200
    assert loaded.json()["revision"] == 0
```

For `test_provider_failure_is_redacted`, use the same SSE pattern.

- [ ] **Step 6: Update ending test**

`test_get_session_keeps_ending_title_and_epilogue_after_end` calls advance and checks the JSON body. Update it to parse SSE:

```python
    with http.stream(
        "POST",
        f"/api/v2/sessions/session_ending/advance",
        json={"expected_revision": 0, "idempotency_key": "ending-advance"},
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response)

    done_events = [e for e in events if e[0] == "done"]
    assert done_events[0][1]["ending_id"] == "safe_exit"
    assert done_events[0][1]["ending_title"] == "Closing Time"
    block_events = [e for e in events if e[0] == "block"]
    assert block_events[0][1]["text"] == "Ending: Closing Time"
```

- [ ] **Step 7: Run full backend test suite**

```bash
cd backend && uv run pytest tests/ -q
```

Expected: All tests PASS.

- [ ] **Step 8: Run ruff lint**

```bash
cd backend && uv run ruff check src/story src/main.py tests
```

Expected: No issues.

- [ ] **Step 9: Commit**

```bash
git add backend/tests/test_v2_api.py
git commit -m "test: migrate v2 API tests for SSE streaming endpoints"
```

---

## Task 6: Frontend SSE Consumer

**Files:**
- Create: `frontend/src/stream.ts`
- Modify: `frontend/src/api.ts` (remove `advanceSession` and `choose`)

**Interfaces:**
- Produces: `streamAdvance()` async generator yielding `{ event: string; data: StreamBlock | StreamChoices | StreamDone | StreamError }`

- [ ] **Step 1: Write the SSE consumer**

Create `frontend/src/stream.ts`:

```typescript
import type { NarrativeBlock, PresentedChoice } from './api'
import { ApiError } from './api'

export interface StreamBlock {
  event: 'block'
  data: NarrativeBlock
}

export interface StreamChoices {
  event: 'choices'
  data: PresentedChoice[]
}

export interface StreamDone {
  event: 'done'
  data: { session_id: string; revision: number; ending_id?: string; ending_title?: string }
}

export interface StreamError {
  event: 'error'
  data: { code: string }
}

export type StreamEvent = StreamBlock | StreamChoices | StreamDone | StreamError

export async function* streamAdvance(
  sessionId: string,
  expectedRevision: number,
  idempotencyKey: string,
): AsyncGenerator<StreamEvent> {
  const response = await fetch(`/api/v2/sessions/${sessionId}/advance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_revision: expectedRevision,
      idempotency_key: idempotencyKey,
    }),
  })

  if (!response.ok) {
    let code = `http_error_${response.status}`
    try {
      const body = await response.json()
      if (body.detail?.code) code = body.detail.code
    } catch {
      // non-JSON error
    }
    throw new ApiError(code, response.status)
  }

  if (!response.body) {
    throw new ApiError('network', 0)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''

    for (const part of parts) {
      const event = parseSSEChunk(part)
      if (event) yield event
    }
  }

  // Flush remaining buffer
  if (buffer.trim()) {
    const event = parseSSEChunk(buffer)
    if (event) yield event
  }
}

function parseSSEChunk(chunk: string): StreamEvent | null {
  let eventType = 'message'
  let dataStr = ''

  for (const line of chunk.split('\n')) {
    if (line.startsWith('event: ')) {
      eventType = line.slice(7).trim()
    } else if (line.startsWith('data: ')) {
      dataStr = line.slice(6)
    }
  }

  if (!dataStr) return null

  let data: unknown
  try {
    data = JSON.parse(dataStr)
  } catch {
    return null
  }

  return { event: eventType, data } as StreamEvent
}
```

- [ ] **Step 2: Remove old streaming-incompatible functions from api.ts**

In `frontend/src/api.ts`, remove `advanceSession()` and `choose()` functions. Keep `createSession`, `fetchSession`, `fetchPack`, `newCommandId`, `newSessionSeed`, and all type exports.

- [ ] **Step 3: Write tests for stream consumer**

Add tests to `frontend/src/api.test.ts` or create `frontend/src/stream.test.ts`. Since the stream consumer uses `fetch` with `ReadableStream`, mock it:

Create `frontend/src/stream.test.ts`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { streamAdvance } from './stream'

function makeSSEResponse(events: string[]): Response {
  const fullText = events.join('\n\n') + '\n\n'
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      for (const evt of events) {
        controller.enqueue(encoder.encode(evt + '\n\n'))
      }
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('streamAdvance', () => {
  it('yields block, choices, and done events in order', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: block\ndata: {"kind":"narration","text":"Hello."}',
        'event: block\ndata: {"kind":"dialogue","character_id":"alice","text":"Hi."}',
        'event: choices\ndata: [{"id":"c1","action_id":"ask","label":"Ask","intent":"ask"}]',
        'event: done\ndata: {"session_id":"s1","revision":3}',
      ]),
    )

    const events: string[] = []
    for await (const evt of streamAdvance('s1', 0, 'k1')) {
      events.push(evt.event)
    }

    expect(events).toEqual(['block', 'block', 'choices', 'done'])
  })

  it('throws ApiError on non-200 response', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { code: 'decision_required' } }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const error = await streamAdvance('s1', 0, 'k1').catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('decision_required')
  })

  it('handles error events in stream', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: error\ndata: {"code":"generation_unavailable"}',
      ]),
    )

    const events: string[] = []
    for await (const evt of streamAdvance('s1', 0, 'k1')) {
      events.push(evt.event)
    }

    expect(events).toEqual(['error'])
  })
})
```

- [ ] **Step 4: Run tests**

```bash
cd frontend && npm run test
```

Expected: Stream tests pass. Some App.test.tsx tests may break because `advanceSession`/`choose` were removed — those will be fixed in Task 7.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stream.ts frontend/src/stream.test.ts frontend/src/api.ts
git commit -m "feat: add SSE stream consumer and remove batch advance/choose"
```

---

## Task 7: Frontend Playback Component

**Files:**
- Create: `frontend/src/Playback.tsx`
- Create: `frontend/src/Playback.css`
- Modify: `frontend/src/App.tsx` (use Playback component)
- Modify: `frontend/src/App.css` (adjust shared styles)
- Modify: `frontend/src/App.test.tsx` (update for streaming)

- [ ] **Step 1: Write the Playback component**

Create `frontend/src/Playback.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import './Playback.css'
import type { NarrativeBlock, PackProjection, PresentedChoice } from './api'
import { streamAdvance, type StreamEvent } from './stream'
import { newCommandId } from './api'

const CHOICE_LETTERS = ['A', 'B', 'C', 'D']
const PLACEHOLDER_COLORS = ['#d96c5f', '#5f9bd9', '#d9b45f', '#7fbf7f', '#b08fd9', '#5fd0c4']
const TYPEWRITER_MS = 33 // ~30 chars/sec

function placeholderColor(characterId: string): string {
  let hash = 0
  for (const ch of characterId) {
    hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  }
  return PLACEHOLDER_COLORS[hash % PLACEHOLDER_COLORS.length]
}

function characterName(pack: PackProjection, characterId: string | null | undefined): string {
  if (!characterId) return ''
  return pack.characters.find((c) => c.character_id === characterId)?.name ?? characterId
}

interface PlaybackProps {
  pack: PackProjection
  sessionId: string
  expectedRevision: number
  onChoices: (choices: PresentedChoice[], revision: number) => void
  onEnding: (endingId: string, endingTitle: string, blocks: NarrativeBlock[], revision: number) => void
  onError: (message: string) => void
}

export default function Playback({
  pack,
  sessionId,
  expectedRevision,
  onChoices,
  onEnding,
  onError,
}: PlaybackProps) {
  const [archive, setArchive] = useState<NarrativeBlock[]>([])
  const [currentBlock, setCurrentBlock] = useState<NarrativeBlock | null>(null)
  const [typedText, setTypedText] = useState('')
  const [typing, setTyping] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [locationId, setLocationId] = useState('')
  const queueRef = useRef<NarrativeBlock[]>([])
  const typingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isMountedRef = useRef(true)

  // Start streaming on mount
  useEffect(() => {
    isMountedRef.current = true
    let cancelled = false

    async function startStream() {
      setWaiting(true)
      const key = newCommandId()
      let blockCount = 0
      let receivedChoices: PresentedChoice[] = []

      try {
        for await (const evt of streamAdvance(sessionId, expectedRevision, key)) {
          if (cancelled) return
          if (evt.event === 'block') {
            blockCount++
            queueRef.current.push(evt.data)
            setWaiting(false)
            // Auto-start first block
            if (blockCount === 1 && !currentBlockRef.current) {
              dequeueNext()
            }
          } else if (evt.event === 'choices') {
            receivedChoices = evt.data
          } else if (evt.event === 'done') {
            const done = evt.data
            if (done.ending_id) {
              onEnding(done.ending_id, done.ending_title ?? '', [...archiveRef.current], done.revision)
              return
            }
            if (receivedChoices.length > 0) {
              onChoices(receivedChoices, done.revision)
            } else {
              // Continue scene — will be advanced again by parent
              onChoices([], done.revision)
            }
          } else if (evt.event === 'error') {
            onError(errorMessageFor(evt.data.code))
            return
          }
        }
      } catch {
        if (!cancelled) onError('网络错误，请重试')
      }
    }

    void startStream()

    return () => {
      cancelled = true
      isMountedRef.current = false
      if (typingTimerRef.current) clearInterval(typingTimerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, expectedRevision])

  // Refs to access current state inside stream callback
  const currentBlockRef = useRef<NarrativeBlock | null>(null)
  const archiveRef = useRef<NarrativeBlock[]>([])
  useEffect(() => { currentBlockRef.current = currentBlock }, [currentBlock])
  useEffect(() => { archiveRef.current = archive }, [archive])

  const dequeueNext = useCallback(() => {
    const next = queueRef.current.shift()
    if (next && isMountedRef.current) {
      setCurrentBlock(next)
      setTypedText('')
      setTyping(true)
      startTypewriter(next.text)
    } else if (!next && isMountedRef.current) {
      setWaiting(true)
    }
  }, [])

  const startTypewriter = useCallback((fullText: string) => {
    if (typingTimerRef.current) clearInterval(typingTimerRef.current)
    let i = 0
    typingTimerRef.current = setInterval(() => {
      i++
      if (!isMountedRef.current) {
        clearInterval(typingTimerRef.current!)
        return
      }
      setTypedText(fullText.slice(0, i))
      if (i >= fullText.length) {
        clearInterval(typingTimerRef.current!)
        typingTimerRef.current = null
        setTyping(false)
      }
    }, TYPEWRITER_MS)
  }, [])

  const handleClick = useCallback(() => {
    if (typing) {
      // Skip animation
      if (typingTimerRef.current) {
        clearInterval(typingTimerRef.current)
        typingTimerRef.current = null
      }
      if (currentBlock) setTypedText(currentBlock.text)
      setTyping(false)
      return
    }
    // Advance to next block
    if (currentBlock) {
      setArchive((prev) => [...prev, currentBlock])
    }
    setCurrentBlock(null)
    dequeueNext()
  }, [typing, currentBlock, dequeueNext])

  // Keyboard: Enter
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        handleClick()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleClick])

  // Auto-scroll to bottom
  const logRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [archive, typedText])

  const locName = pack.locations.find((l) => l.location_id === locationId)?.name ?? locationId

  return (
    <>
      <header className="scene-header">
        <span className="scene-location">{locName || pack.title}</span>
      </header>

      <div
        className="playback-log"
        ref={logRef}
        onClick={handleClick}
        role="button"
        tabIndex={0}
        aria-label="对话框（点击继续）"
      >
        {archive.map((block, i) => (
          <BlockLine key={i} block={block} pack={pack} />
        ))}
        {currentBlock && (
          <BlockLine
            block={{ ...currentBlock, text: typedText }}
            pack={pack}
            typing={typing}
          />
        )}
        {waiting && <p className="waiting-hint">···</p>}
      </div>

      <div className="advance-hint" onClick={handleClick}>
        {typing ? '点击跳过' : waiting ? '' : '▼ 点击继续 / Enter'}
      </div>
    </>
  )
}

function errorMessageFor(code: string): string {
  switch (code) {
    case 'generation_unavailable':
      return '生成失败，请重试'
    case 'revision_conflict':
    case 'decision_required':
      return '状态已改变，正在同步'
    case 'session_ended':
      return '会话已结束'
    default:
      return '请求失败，请重试'
  }
}

interface BlockLineProps {
  block: NarrativeBlock
  pack: PackProjection
  typing?: boolean
}

function BlockLine({ block, pack, typing }: BlockLineProps) {
  if (block.kind === 'dialogue') {
    return (
      <div className="dialogue-entry">
        <span
          className="dialogue-speaker"
          style={{ '--speaker-color': placeholderColor(block.character_id ?? '') } as CSSProperties}
        >
          {characterName(pack, block.character_id)}
        </span>
        <p className="dialogue-text">
          {block.text}
          {typing && <span className="cursor">▌</span>}
        </p>
      </div>
    )
  }
  return (
    <p className="narration-text">
      {block.text}
      {typing && <span className="cursor">▌</span>}
    </p>
  )
}
```

- [ ] **Step 2: Write Playback CSS**

Create `frontend/src/Playback.css`:

```css
.playback-log {
  flex: 1 1 auto;
  min-height: 300px;
  max-height: 60vh;
  overflow-y: auto;
  padding: 20px 24px;
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  cursor: pointer;
  scroll-behavior: smooth;
}

.narration-text {
  color: var(--text-gal-dim);
  font-size: 15px;
  line-height: 2;
  margin: 0 0 16px;
}

.dialogue-entry {
  margin-bottom: 16px;
}

.dialogue-speaker {
  display: inline-block;
  color: var(--speaker-color, var(--accent-gal));
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.15em;
  margin-bottom: 4px;
  padding: 0 8px;
  border-bottom: 2px solid var(--speaker-color, var(--accent-gal-soft));
}

.dialogue-text {
  color: var(--text-gal);
  font-size: 17px;
  line-height: 2;
  margin: 4px 0 0;
}

.cursor {
  color: var(--accent-gal);
  animation: blink 0.8s infinite;
}

@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}

.waiting-hint {
  text-align: center;
  color: var(--text-gal-dim);
  font-size: 20px;
  letter-spacing: 0.3em;
  padding: 8px 0;
}

.advance-hint {
  text-align: center;
  color: var(--text-gal-dim);
  font-size: 13px;
  letter-spacing: 0.2em;
  padding: 8px 0;
  cursor: pointer;
}

@media (max-width: 480px) {
  .playback-log {
    min-height: 250px;
    max-height: 55vh;
    padding: 14px 14px;
  }
  .dialogue-text {
    font-size: 15px;
  }
}
```

- [ ] **Step 3: Rewrite App.tsx to use Playback**

Rewrite `frontend/src/App.tsx`. The play screen now uses `Playback` component. The app manages: start game, stream (via Playback), show choices, select choice, stream again.

Key changes:
- Remove `sendAdvance`, `sendChoice`, `pendingRef` logic
- Add `Playback` component for the streaming play screen
- Choice selection sends a simple POST (no streaming), then transitions back to Playback
- The `Screen` type stays similar but `play` carries session info

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import {
  ApiError,
  DEFAULT_PACK_ID,
  type PackProjection,
  type PresentedChoice,
  type SessionProjection,
  createSession,
  fetchPack,
  fetchSession,
  newCommandId,
  newSessionSeed,
} from './api'
import { clearSessionId, loadSessionId, saveSessionId } from './storage'
import Playback from './Playback'

type Screen =
  | { kind: 'booting' }
  | { kind: 'boot-error'; message: string }
  | { kind: 'start'; pack: PackProjection }
  | { kind: 'play'; pack: PackProjection; sessionId: string; revision: number }
  | { kind: 'choices'; pack: PackProjection; sessionId: string; revision: number; choices: PresentedChoice[] }
  | { kind: 'ending'; pack: PackProjection; sessionId: string; blocks: import('./api').NarrativeBlock[]; endingId: string; endingTitle: string }
  | { kind: 'error'; pack: PackProjection; sessionId: string; revision: number; message: string }

const CHOICE_LETTERS = ['A', 'B', 'C', 'D']

export default function App() {
  const [screen, setScreen] = useState<Screen>({ kind: 'booting' })
  const packRef = useRef<PackProjection | null>(null)
  const startingRef = useRef(false)
  const bootingRef = useRef(false)

  const boot = useCallback(async () => {
    if (bootingRef.current) return
    bootingRef.current = true
    try {
      const pack = await fetchPack(DEFAULT_PACK_ID)
      packRef.current = pack
      const storedId = loadSessionId()
      if (!storedId) {
        setScreen({ kind: 'start', pack })
        return
      }
      try {
        const session = await fetchSession(storedId)
        if (session.status === 'ended') {
          setScreen({
            kind: 'ending',
            pack,
            sessionId: storedId,
            blocks: session.blocks,
            endingId: session.ending_id ?? '',
            endingTitle: session.ending_title ?? '',
          })
        } else if (session.choices.length > 0) {
          setScreen({
            kind: 'choices',
            pack,
            sessionId: storedId,
            revision: session.revision,
            choices: session.choices,
          })
        } else {
          setScreen({ kind: 'play', pack, sessionId: storedId, revision: session.revision })
        }
      } catch (reason) {
        if (reason instanceof ApiError && reason.code === 'session_not_found') {
          clearSessionId()
          setScreen({ kind: 'start', pack })
        } else {
          setScreen({ kind: 'boot-error', message: '无法读取存档，请重试' })
        }
      }
    } catch {
      setScreen({ kind: 'boot-error', message: '无法获取剧本信息，请重试' })
    } finally {
      bootingRef.current = false
    }
  }, [])

  useEffect(() => {
    void boot()
  }, [boot])

  const startNewGame = useCallback(async () => {
    const pack = packRef.current
    if (!pack || startingRef.current) return
    startingRef.current = true
    try {
      const session = await createSession(DEFAULT_PACK_ID, newSessionSeed())
      saveSessionId(session.session_id)
      setScreen({ kind: 'play', pack, sessionId: session.session_id, revision: session.revision })
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'session_not_found') {
        clearSessionId()
        setScreen({ kind: 'start', pack })
      }
    } finally {
      startingRef.current = false
    }
  }, [])

  const handleChoices = useCallback(
    (pack: PackProjection, sessionId: string, choices: PresentedChoice[], revision: number) => {
      if (choices.length > 0) {
        setScreen({ kind: 'choices', pack, sessionId, revision, choices })
      } else {
        // Continue scene — stream next advance
        setScreen({ kind: 'play', pack, sessionId, revision })
      }
    },
    [],
  )

  const handleChoice = useCallback(
    async (sessionId: string, choiceId: string, revision: number) => {
      const pack = packRef.current
      if (!pack) return
      try {
        const response = await fetch(`/api/v2/sessions/${sessionId}/choices/${choiceId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_revision: revision,
            idempotency_key: newCommandId(),
          }),
        })
        if (!response.ok) throw new ApiError('choice_failed', response.status)
        const result = await response.json()
        // Transition to play screen with new revision
        setScreen({
          kind: 'play',
          pack,
          sessionId,
          revision: result.revision,
        })
      } catch {
        setScreen({
          kind: 'error',
          pack,
          sessionId,
          revision,
          message: '选择失败，请重试',
        })
      }
    },
    [],
  )

  const handleEnding = useCallback(
    (
      pack: PackProjection,
      sessionId: string,
      endingId: string,
      endingTitle: string,
      blocks: import('./api').NarrativeBlock[],
      revision: number,
    ) => {
      setScreen({ kind: 'ending', pack, sessionId, blocks, endingId, endingTitle })
    },
    [],
  )

  if (screen.kind === 'booting') {
    return (
      <main className="gal-app boot-screen">
        <p className="busy-hint" role="status">正在连接…</p>
      </main>
    )
  }

  if (screen.kind === 'boot-error') {
    return (
      <main className="gal-app boot-screen">
        <p className="error-message">{screen.message}</p>
        <button className="secondary-button" onClick={() => void boot()}>重试</button>
        <button
          className="secondary-button"
          onClick={() => {
            clearSessionId()
            const pack = packRef.current
            if (pack) setScreen({ kind: 'start', pack })
          }}
        >
          开始新游戏
        </button>
      </main>
    )
  }

  if (screen.kind === 'start') {
    return (
      <main className="gal-app start-screen">
        <h1 className="start-title">{screen.pack.title}</h1>
        <p className="start-subtitle">一段由 AI 驱动的故事，等待你的选择。</p>
        <button className="primary-button" onClick={() => void startNewGame()}>
          开始新游戏
        </button>
      </main>
    )
  }

  const { pack } = screen

  if (screen.kind === 'play') {
    return (
      <main className="gal-app">
        <Playback
          key={`${screen.sessionId}-${screen.revision}`}
          pack={pack}
          sessionId={screen.sessionId}
          expectedRevision={screen.revision}
          onChoices={(choices, rev) => handleChoices(pack, screen.sessionId, choices, rev)}
          onEnding={(eid, etitle, blocks, _rev) =>
            handleEnding(pack, screen.sessionId, eid, etitle, blocks, _rev)
          }
          onError={(msg) =>
            setScreen({
              kind: 'error',
              pack,
              sessionId: screen.sessionId,
              revision: screen.revision,
              message: msg,
            })
          }
        />
      </main>
    )
  }

  if (screen.kind === 'choices') {
    return (
      <main className="gal-app">
        <header className="scene-header">
          <span className="scene-location">{pack.title}</span>
        </header>
        <section className="dialogue-box" aria-label="选项">
          <ol className="choice-list">
            {screen.choices.map((choice, i) => (
              <li key={choice.id}>
                <button
                  className="choice-button"
                  aria-label={`${CHOICE_LETTERS[i] ?? i + 1} ${choice.label}`}
                  onClick={() => void handleChoice(screen.sessionId, choice.id, screen.revision)}
                >
                  <span className="choice-letter">{CHOICE_LETTERS[i] ?? i + 1}</span>
                  <span className="choice-label">{choice.label}</span>
                  {choice.preview && <span className="choice-preview">{choice.preview}</span>}
                </button>
              </li>
            ))}
          </ol>
        </section>
      </main>
    )
  }

  if (screen.kind === 'error') {
    return (
      <main className="gal-app">
        <div className="error-banner" role="alert">
          <p className="error-message">{screen.message}</p>
        </div>
        <button
          className="primary-button"
          onClick={() =>
            setScreen({
              kind: 'play',
              pack,
              sessionId: screen.sessionId,
              revision: screen.revision,
            })
          }
        >
          重试
        </button>
      </main>
    )
  }

  // Ending
  return (
    <main className="gal-app">
      <header className="scene-header">
        <span className="ending-eyebrow">END</span>
      </header>
      <section className="dialogue-box ending-box" aria-label="结局">
        <h2 className="ending-title">{screen.endingTitle}</h2>
        {screen.blocks.map((block, i) => (
          <p key={i} className={block.kind === 'dialogue' ? 'dialogue-text' : 'narration-line'}>
            {block.kind === 'dialogue'
              ? `${characterName(screen.pack, block.character_id)}：${block.text}`
              : block.text}
          </p>
        ))}
      </section>
      <div className="action-bar">
        <button
          className="primary-button"
          onClick={() => {
            clearSessionId()
            const p = packRef.current
            if (p) setScreen({ kind: 'start', pack: p })
          }}
        >
          重新开始
        </button>
      </div>
    </main>
  )
}

function characterName(pack: PackProjection, characterId: string | null | undefined): string {
  if (!characterId) return ''
  return pack.characters.find((c) => c.character_id === characterId)?.name ?? characterId
}
```

- [ ] **Step 4: Update App.test.tsx**

The tests need to be completely rewritten to work with the streaming architecture. The mock server must return SSE streams instead of JSON responses for the advance endpoint.

Key changes to the mock server:
- `/advance` returns a `ReadableStream` with SSE events instead of JSON
- Tests assert on text appearing in the playback log after streaming
- Choice tests click a choice button, then verify a new play stream starts

Replace `frontend/src/App.test.tsx` with tests that:
1. Test boot and start screen (unchanged behavior)
2. Test streaming playback: mock SSE response, verify text appears
3. Test choice flow: mock choice POST, then mock next SSE stream
4. Test ending flow
5. Test error handling

Due to the complexity of mocking ReadableStream in tests, focus on the critical user-facing behaviors. Here is the test for the core streaming playback flow:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { clearSessionId } from './storage'

const PACK = {
  pack_id: 'cafe_mystery',
  title: '咖啡馆疑云',
  language: 'zh-CN',
  characters: [
    { character_id: 'alice', name: '艾丽丝', public_profile: '' },
    { character_id: 'protagonist', name: '悠真', public_profile: '' },
  ],
  locations: [{ location_id: 'cafe', name: '街角咖啡馆' }],
}

function sseResponse(events: string[]): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      for (const evt of events) {
        controller.enqueue(encoder.encode(evt + '\n\n'))
      }
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let currentSceneBlocks = [
  'event: block\ndata: {"kind":"narration","text":"第一幕：咖啡馆。"}',
  'event: block\ndata: {"kind":"dialogue","character_id":"alice","text":"你好。"}',
]
let currentChoices: PresentedChoice[] | null = null

const fetchMock = vi.fn()

beforeEach(() => {
  currentSceneBlocks = [
    'event: block\ndata: {"kind":"narration","text":"第一幕：咖啡馆。"}',
    'event: block\ndata: {"kind":"dialogue","character_id":"alice","text":"你好。"}',
  ]
  currentChoices = null
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'

    if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
    if (url === '/api/v2/sessions' && method === 'POST') {
      return jsonResponse({ session_id: 's1', pack_id: 'cafe_mystery', revision: 0, status: 'active', phase: 'opening', scene_count: 0, pending_decision_id: null, scene_id: null, blocks: [], choices: [], ending_id: null, ending_title: null, location_id: 'cafe', time_label: 'opening', present_character_ids: ['alice'] }, 201)
    }
    if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') {
      return jsonResponse({ session_id: 's1', pack_id: 'cafe_mystery', revision: 1, status: 'active', phase: 'opening', scene_count: 1, pending_decision_id: null, scene_id: null, blocks: [], choices: [], ending_id: null, ending_title: null, location_id: 'cafe', time_label: 'opening', present_character_ids: ['alice'] })
    }
    if (url.endsWith('/advance') && method === 'POST') {
      const events = [...currentSceneBlocks]
      if (currentChoices) {
        events.push(`event: choices\ndata: ${JSON.stringify(currentChoices)}`)
      }
      events.push('event: done\ndata: {"session_id":"s1","revision":1}')
      return sseResponse(events)
    }
    if (url.includes('/choices/') && method === 'POST') {
      return jsonResponse({ session_id: 's1', revision: 2, action_id: 'ask', outcome: 'success' })
    }
    return jsonResponse({ detail: { code: 'not_found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  cleanup()
  clearSessionId()
})

describe('streaming playback', () => {
  it('shows start screen and starts game on click', async () => {
    render(<App />)
    const start = await screen.findByRole('button', { name: '开始新游戏' })
    fireEvent.click(start)
    // Wait for first block text to appear in the playback log
    expect(await screen.findByText(/第一幕/)).toBeInTheDocument()
  })

  it('displays dialogue with character name', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))
    expect(await screen.findByText('你好。')).toBeInTheDocument()
  })

  it('shows choice buttons after stream delivers choices', async () => {
    currentChoices = [
      { id: 'ch1', action_id: 'ask', label: '询问', intent: 'ask' },
    ]
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))
    expect(await screen.findByRole('button', { name: /A 询问/ })).toBeInTheDocument()
  })
})
```

Import `PresentedChoice` type at the top: `import type { PresentedChoice } from './api'`

- [ ] **Step 5: Run frontend tests and fix issues**

```bash
cd frontend && npm run test
```

Iterate until all tests pass.

- [ ] **Step 6: Build check**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no TypeScript errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/Playback.tsx frontend/src/Playback.css frontend/src/App.tsx frontend/src/App.css frontend/src/App.test.tsx
git commit -m "feat: galgame streaming playback with typewriter, click/Enter, and buffer"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task(s) |
|-----------------|---------|
| Single streaming model call | Task 2 (StreamingSceneGenerator) |
| Incremental block parsing | Task 1 (BlockStreamParser) |
| SSE endpoint | Task 4 (SSE endpoints) |
| Drop Simulator post-check | Task 3 (light per-block validation only) |
| Atomic commit at stream end | Task 3 (commit_command after stream) |
| Frontend block buffer | Task 7 (Playback queue) |
| Typewriter effect (~30 chars/sec) | Task 7 (Playback component) |
| Click + Enter to advance | Task 7 (Playback keyboard handler) |
| Skip animation on click | Task 7 (handleClick skip logic) |
| Buffer threshold (2-3 blocks) | Task 7 (auto-start first block, rest on click) |
| Choices display | Task 7 (App choices screen) |
| Loading indicator (···) | Task 7 (waiting hint) |
| Error handling | Tasks 4 + 7 (SSE error event + frontend display) |
| Scrolling log layout | Task 7 (archive + current block) |

### Placeholder scan

No TBD, TODO, or vague requirements remain.

### Type consistency

- `BlockStreamParser.feed(text: str) -> list[dict]` matches usage in Task 2
- `StreamingSceneGenerator.generate_scene()` yields `tuple[str, dict]` consumed by Task 3
- `advance_streamed()` yields `tuple[str, Any]` consumed by Task 4 SSE endpoint
- `streamAdvance()` yields `StreamEvent` consumed by Task 7 Playback
- `NarrativeBlock` and `PresentedChoice` types unchanged from existing code

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-11-streaming-galgame.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
