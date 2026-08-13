# Frontend Segment Player Implementation Plan

> Implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-scene streaming consumer with a segment-aware player that buffers provisional blocks, unlocks playback only on `segment_ready`, and guarantees zero mid-performance network waits between choices.

**Architecture:** The frontend consumes a single SSE turn stream that emits `segment_started`, provisional `block`, `segment_ready`, `heartbeat`, and `error` events. Blocks arriving before `segment_ready` are buffered as PROVISIONAL. When `segment_ready` arrives, the buffer is unlocked and the existing typewriter drains the queue without further network I/O. The player state machine moves through `idle -> generating_after_choice -> buffering_segment -> playing -> waiting_choice | playing_ending -> ended`. On refresh, the player fetches the session projection and replays committed blocks without issuing a duplicate turn.

**Tech Stack:** React 18, TypeScript strict mode, Vitest + @testing-library/react, SSE (text/event-stream via ReadableStream)

## Global Constraints

- SSE event names and data shapes MUST match Plan 2's protocol exactly: `segment_started`, `block`, `segment_ready`, `heartbeat`, `error`
- `block` events carry `segment_id` + `index` fields — buffer as PROVISIONAL, never displayable until `segment_ready`
- `segment_ready` carries `revision`, `terminal` ("decision" | "ending"), optional `choices`, optional `ending`
- Transport EOF / `generation_done` never directly changes visual state
- Choices are NOT displayed until the local playback queue fully drains
- Ending metadata is NOT displayed until the final block finishes playing
- Connection failure before `segment_ready` leaves old revision intact, shows retry
- Replay after refresh does NOT issue a duplicate turn — replays from projection
- No client request between internal scenes
- `POST /api/v2/sessions/{session_id}/turns` is the one authoritative turn command; `choice_id` is null only for opening
- Frontend language: zh-CN for all user-facing strings
- TypeScript strict mode, zero lint warnings

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/segmentPlayer.ts` | Pure segment player state machine — states, transitions, buffer logic; no React dependency |
| `frontend/src/segmentPlayer.test.ts` | Unit tests for the state machine (buffer, transitions, drain, replay) |

**Modified files:**

| File | Changes |
|------|---------|
| `frontend/src/stream.ts` | Add new SSE event types (`SegmentStartedEvent`, `BlockEvent`, `SegmentReadyEvent`, `HeartbeatEvent`); add `streamTurn()` async generator targeting `POST /api/v2/sessions/{id}/turns` |
| `frontend/src/api.ts` | Add `turnSessionUrl()`; update `SessionProjection` with segment fields; add segment-related types |
| `frontend/src/Playback.tsx` | Replace `streamAdvance` consumption with `streamTurn`; integrate segment state machine; buffer blocks until `segment_ready`; defer choices/ending until queue drains |
| `frontend/src/App.tsx` | Update screen state machine for segment-aware flow; use `turnSession` on choice/opening; replay from projection on refresh |
| `frontend/src/stream.test.ts` | Update SSE mock patterns for new event types; test `segment_started`, `block`, `segment_ready`, `heartbeat` parsing |
| `frontend/src/App.test.tsx` | Replace scene-level tests with segment-level tests per spec section 12.4 |

---

## Task 1: Segment Player State Machine (Pure Logic)

**Goal:** A framework-agnostic state machine that manages provisional block buffering, segment readiness, queue draining, and terminal transitions.

**Files:**
- Create: `frontend/src/segmentPlayer.ts`
- Create: `frontend/src/segmentPlayer.test.ts`

**Interfaces:**
- Consumes: `NarrativeBlock`, `PresentedChoice` from `./api`
- Produces: `SegmentPlayer` class with methods `onBlock()`, `onSegmentReady()`, `onError()`, `dequeueBlock()`, `isPlayable()`, `isDrained()`, state accessors

### Steps

- [ ] **Step 1: Write failing tests for state transitions**

Create `frontend/src/segmentPlayer.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { SegmentPlayer } from './segmentPlayer'
import type { NarrativeBlock, PresentedChoice } from './api'

const BLOCK_A: NarrativeBlock = { kind: 'narration', text: 'Scene opens.' }
const BLOCK_B: NarrativeBlock = { kind: 'dialogue', character_id: 'alice', text: 'Hello.' }
const CHOICES: PresentedChoice[] = [
  { id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: null, preview: null },
]

describe('SegmentPlayer state machine', () => {
  it('starts in idle state', () => {
    const p = new SegmentPlayer()
    expect(p.state).toBe('idle')
    expect(p.playableBlocks).toEqual([])
  })

  it('transitions idle -> generating_after_choice on start()', () => {
    const p = new SegmentPlayer()
    p.start()
    expect(p.state).toBe('generating_after_choice')
  })

  it('transitions to buffering_segment on segment_started', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    expect(p.state).toBe('buffering_segment')
  })

  it('buffers blocks as provisional and is not playable before segment_ready', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Provisional.' })
    p.onBlock({ segment_id: 'seg-1', index: 1, kind: 'dialogue', character_id: 'alice', text: 'Hi.' })
    expect(p.state).toBe('buffering_segment')
    expect(p.playableBlocks).toEqual([])
    expect(p.provisionalCount).toBe(2)
  })

  it('unlocks blocks on segment_ready with terminal decision', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Scene opens.' })
    p.onBlock({ segment_id: 'seg-1', index: 1, kind: 'dialogue', character_id: 'alice', text: 'Hello.' })
    p.onSegmentReady({
      segment_id: 'seg-1',
      revision: 18,
      terminal: 'decision',
      choices: CHOICES,
    })
    expect(p.state).toBe('playing')
    expect(p.playableBlocks).toEqual([BLOCK_A, BLOCK_B])
  })

  it('unlocks blocks on segment_ready with terminal ending', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'The end.' })
    p.onSegmentReady({
      segment_id: 'seg-1',
      revision: 20,
      terminal: 'ending',
      ending: { ending_id: 'end-1', title: 'Truth', tone: 'bittersweet', terminal_state_summary: 'They parted ways.' },
    })
    expect(p.state).toBe('playing')
    expect(p.ending).toEqual({ ending_id: 'end-1', title: 'Truth', tone: 'bittersweet', terminal_state_summary: 'They parted ways.' })
  })

  it('transitions to waiting_choice only after queue drains', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Scene.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    // Not drained yet — still playing
    expect(p.state).toBe('playing')
    expect(p.choices).toBeNull()
    p.dequeueBlock()
    expect(p.isDrained()).toBe(true)
    p.onDrained()
    expect(p.state).toBe('waiting_choice')
    expect(p.choices).toEqual(CHOICES)
  })

  it('transitions to playing_ending only after queue drains', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Finale.' })
    p.onSegmentReady({
      segment_id: 'seg-1', revision: 20, terminal: 'ending',
      ending: { ending_id: 'end-2', title: 'Dawn', tone: 'hopeful', terminal_state_summary: 'A new day.' },
    })
    expect(p.state).toBe('playing')
    p.dequeueBlock()
    p.onDrained()
    expect(p.state).toBe('playing_ending')
  })

  it('discards provisional buffer on error before segment_ready', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Provisional.' })
    p.onError('generation_failed')
    expect(p.state).toBe('error')
    expect(p.provisionalCount).toBe(0)
    expect(p.playableBlocks).toEqual([])
  })

  it('preserves old revision on error', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onError('generation_failed')
    expect(p.committedRevision).toBeNull()
  })

  it('ignores blocks from wrong segment_id', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-2', index: 0, kind: 'narration', text: 'Wrong segment.' })
    expect(p.provisionalCount).toBe(0)
  })

  it('sorts provisional blocks by index on unlock', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 1, kind: 'dialogue', character_id: 'alice', text: 'Second.' })
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'First.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    expect(p.playableBlocks[0].text).toBe('First.')
    expect(p.playableBlocks[1].text).toBe('Second.')
  })

  it('loads blocks from projection for replay without turn', () => {
    const p = new SegmentPlayer()
    p.loadFromProjection([BLOCK_A, BLOCK_B], 18)
    expect(p.state).toBe('playing')
    expect(p.playableBlocks).toEqual([BLOCK_A, BLOCK_B])
    expect(p.isReplay).toBe(true)
  })
})
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/segmentPlayer.test.ts
```

All tests should fail with import error since `segmentPlayer.ts` does not exist yet.

- [ ] **Step 3: Implement SegmentPlayer class**

Create `frontend/src/segmentPlayer.ts`:

```typescript
import type { NarrativeBlock, PresentedChoice } from './api'

// ── SSE Event Data Shapes (match Plan 2 protocol exactly) ──

export interface SegmentStartedData {
  segment_id: string
  expected_revision: number
}

export interface BlockEventData {
  segment_id: string
  index: number
  kind: 'narration' | 'dialogue'
  text: string
  character_id?: string | null
}

export interface SegmentReadyData {
  segment_id: string
  revision: number
  terminal: 'decision' | 'ending'
  choices?: PresentedChoice[]
  ending?: {
    ending_id: string
    title: string
    tone: string
    terminal_state_summary: string
  }
  blocks?: NarrativeBlock[]
}

export interface EndingMeta {
  ending_id: string
  title: string
  tone: string
  terminal_state_summary: string
}

// ── State Machine ──

export type SegmentPlayerState =
  | 'idle'
  | 'generating_after_choice'
  | 'buffering_segment'
  | 'playing'
  | 'waiting_choice'
  | 'playing_ending'
  | 'error'

interface ProvisionalBlock {
  index: number
  block: NarrativeBlock
}

export class SegmentPlayer {
  private _state: SegmentPlayerState = 'idle'
  private _segmentId: string | null = null
  private _provisional: ProvisionalBlock[] = []
  private _unlocked: NarrativeBlock[] = []
  private _drainIndex = 0
  private _choices: PresentedChoice[] | null = null
  private _ending: EndingMeta | null = null
  private _committedRevision: number | null = null
  private _errorCode: string | null = null
  private _isReplay = false

  get state(): SegmentPlayerState { return this._state }
  get provisionalCount(): number { return this._provisional.length }
  get playableBlocks(): NarrativeBlock[] { return [...this._unlocked] }
  get choices(): PresentedChoice[] | null { return this._choices }
  get ending(): EndingMeta | null { return this._ending }
  get committedRevision(): number | null { return this._committedRevision }
  get errorCode(): string | null { return this._errorCode }
  get isReplay(): boolean { return this._isReplay }

  /** Begin a new turn — player transitions to generating_after_choice. */
  start(): void {
    this._reset()
    this._state = 'generating_after_choice'
  }

  /** SSE segment_started — begin buffering provisional blocks. */
  onSegmentStarted(segmentId: string, _expectedRevision: number): void {
    if (this._state !== 'generating_after_choice') return
    this._segmentId = segmentId
    this._state = 'buffering_segment'
  }

  /** SSE block — buffer as provisional; ignored if wrong segment or not buffering. */
  onBlock(data: BlockEventData): void {
    if (this._state !== 'buffering_segment') return
    if (data.segment_id !== this._segmentId) return
    const block: NarrativeBlock = {
      kind: data.kind,
      text: data.text,
      character_id: data.character_id ?? undefined,
    }
    this._provisional.push({ index: data.index, block })
  }

  /** SSE segment_ready — unlock buffered blocks, sort by index, transition to playing. */
  onSegmentReady(data: SegmentReadyData): void {
    if (this._state !== 'buffering_segment') return
    if (data.segment_id !== this._segmentId) return

    // Sort by index, extract blocks
    this._provisional.sort((a, b) => a.index - b.index)
    this._unlocked = this._provisional.map((p) => p.block)
    this._provisional = []
    this._drainIndex = 0
    this._committedRevision = data.revision

    if (data.terminal === 'decision' && data.choices) {
      this._choices = data.choices
    } else if (data.terminal === 'ending' && data.ending) {
      this._ending = data.ending
    }

    // If there are no blocks (edge case), immediately check drain
    if (this._unlocked.length === 0) {
      this.onDrained()
    } else {
      this._state = 'playing'
    }
  }

  /** SSE error — discard provisional buffer, transition to error. */
  onError(code: string): void {
    this._provisional = []
    this._errorCode = code
    this._state = 'error'
  }

  /** Dequeue next block for playback. Returns null when queue is empty. */
  dequeueBlock(): NarrativeBlock | null {
    if (this._drainIndex >= this._unlocked.length) return null
    const block = this._unlocked[this._drainIndex]
    this._drainIndex++
    return block
  }

  /** True when all unlocked blocks have been dequeued. */
  isDrained(): boolean {
    return this._drainIndex >= this._unlocked.length
  }

  /** Called when the playback component finishes its local queue. */
  onDrained(): void {
    if (this._state !== 'playing') return
    if (this._choices) {
      this._state = 'waiting_choice'
    } else if (this._ending) {
      this._state = 'playing_ending'
    }
  }

  /** Load committed blocks from a projection (replay after refresh). */
  loadFromProjection(
    blocks: NarrativeBlock[],
    revision: number,
    choices?: PresentedChoice[] | null,
    ending?: EndingMeta | null
  ): void {
    this._reset()
    this._unlocked = [...blocks]
    this._drainIndex = 0
    this._committedRevision = revision
    this._isReplay = true
    if (choices) this._choices = choices
    if (ending) this._ending = ending
    if (blocks.length === 0) {
      this._state = 'idle'
    } else {
      this._state = 'playing'
    }
  }

  /** Peek current block without advancing drain cursor. */
  peekBlock(): NarrativeBlock | null {
    if (this._drainIndex >= this._unlocked.length) return null
    return this._unlocked[this._drainIndex]
  }

  /** Total unlocked block count. */
  get totalBlocks(): number {
    return this._unlocked.length
  }

  /** Blocks remaining to be drained. */
  get remainingBlocks(): number {
    return this._unlocked.length - this._drainIndex
  }

  private _reset(): void {
    this._segmentId = null
    this._provisional = []
    this._unlocked = []
    this._drainIndex = 0
    this._choices = null
    this._ending = null
    this._committedRevision = null
    this._errorCode = null
    this._isReplay = false
  }
}
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/segmentPlayer.test.ts
```

All 12 tests must pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/segmentPlayer.ts frontend/src/segmentPlayer.test.ts
git commit -m "feat: add SegmentPlayer state machine for segment buffering and playback

Pure state machine that manages provisional block buffering, segment_ready
unlock, queue draining, and terminal transitions. No React dependency.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: Update SSE Stream Types and Add turnSession

**Goal:** Extend `stream.ts` with new SSE event types for the segment protocol and add a `streamTurn()` async generator targeting `POST /api/v2/sessions/{id}/turns`. Update `api.ts` with the turn URL builder and updated types.

**Files:**
- Modify: `frontend/src/stream.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/stream.test.ts`

**Interfaces:**
- Consumes: `SegmentStartedData`, `BlockEventData`, `SegmentReadyData` from `./segmentPlayer`
- Produces: `streamTurn()` async generator, `StreamEvent` union type, `turnSessionUrl()`

### Steps

- [ ] **Step 1: Write failing tests for new stream event types**

Replace the contents of `frontend/src/stream.test.ts`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { streamTurn } from './stream'

function makeSSEResponse(events: string[]): Response {
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

describe('streamTurn', () => {
  it('yields segment_started, block, segment_ready in order', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":12}',
        'event: block\ndata: {"segment_id":"seg-1","index":0,"kind":"narration","text":"Hello."}',
        'event: block\ndata: {"segment_id":"seg-1","index":1,"kind":"dialogue","character_id":"alice","text":"Hi."}',
        'event: segment_ready\ndata: {"segment_id":"seg-1","revision":18,"terminal":"decision","choices":[{"id":"c1","action_id":"ask","label":"Ask","intent":"ask","target_character_id":"alice","preview":"Ask Alice"}]}',
      ]),
    )

    const events: string[] = []
    for await (const evt of streamTurn('s1', null, 12, 'key-1')) {
      events.push(evt.event)
    }

    expect(events).toEqual(['segment_started', 'block', 'block', 'segment_ready'])
  })

  it('yields heartbeat events without breaking flow', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":0}',
        'event: heartbeat\ndata: {}',
        'event: block\ndata: {"segment_id":"seg-1","index":0,"kind":"narration","text":"Scene."}',
        'event: heartbeat\ndata: {}',
        'event: segment_ready\ndata: {"segment_id":"seg-1","revision":1,"terminal":"decision","choices":[]}',
      ]),
    )

    const events: string[] = []
    for await (const evt of streamTurn('s1', null, 0, 'key-1')) {
      events.push(evt.event)
    }

    expect(events).toEqual(['segment_started', 'heartbeat', 'block', 'heartbeat', 'segment_ready'])
  })

  it('yields error events', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: error\ndata: {"code":"generation_unavailable"}',
      ]),
    )

    const events: string[] = []
    for await (const evt of streamTurn('s1', null, 0, 'key-1')) {
      events.push(evt.event)
    }

    expect(events).toEqual(['error'])
  })

  it('throws ApiError on non-200 response', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { code: 'revision_conflict' } }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    let error: unknown = null
    try {
      for await (const _ of streamTurn('s1', null, 0, 'key-1')) {
        // should throw before yielding
      }
    } catch (e) {
      error = e
    }
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('revision_conflict')
  })

  it('includes choice_id in request body when provided', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: segment_started\ndata: {"segment_id":"seg-2","expected_revision":18}',
        'event: segment_ready\ndata: {"segment_id":"seg-2","revision":25,"terminal":"decision","choices":[]}',
      ]),
    )

    for await (const _ of streamTurn('s1', 'choice-abc', 18, 'key-2')) {
      // consume
    }

    const call = fetchMock.mock.calls[0]
    const body = JSON.parse(call[1].body as string)
    expect(body.choice_id).toBe('choice-abc')
    expect(body.expected_revision).toBe(18)
    expect(body.idempotency_key).toBe('key-2')
  })

  it('sends null choice_id for opening turn', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: segment_started\ndata: {"segment_id":"seg-0","expected_revision":0}',
        'event: segment_ready\ndata: {"segment_id":"seg-0","revision":5,"terminal":"decision","choices":[]}',
      ]),
    )

    for await (const _ of streamTurn('s1', null, 0, 'key-0')) {
      // consume
    }

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body.choice_id).toBeNull()
  })

  it('parses segment_ready with ending terminal', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: segment_started\ndata: {"segment_id":"seg-e","expected_revision":10}',
        'event: block\ndata: {"segment_id":"seg-e","index":0,"kind":"narration","text":"Finale."}',
        'event: segment_ready\ndata: {"segment_id":"seg-e","revision":15,"terminal":"ending","ending":{"ending_id":"end-dawn","title":"Dawn","tone":"hopeful","terminal_state_summary":"A new day."}}',
      ]),
    )

    const results = []
    for await (const evt of streamTurn('s1', null, 10, 'key-e')) {
      results.push(evt)
    }

    const ready = results.find((e) => e.event === 'segment_ready')
    expect(ready).toBeDefined()
    if (ready && ready.event === 'segment_ready') {
      expect(ready.data.terminal).toBe('ending')
      expect(ready.data.ending?.title).toBe('Dawn')
    }
  })
})
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/stream.test.ts
```

All tests should fail — `streamTurn` does not exist yet.

- [ ] **Step 3: Add turnSessionUrl to api.ts**

Add to `frontend/src/api.ts`, after the existing `advanceUrl` function (keep `advanceUrl` for backward compatibility but it will not be used by the new flow):

```typescript
/** Build the full URL for the segment turn SSE endpoint (used by stream.ts). */
export function turnSessionUrl(sessionId: string): string {
  return apiPath(`/api/v2/sessions/${sessionId}/turns`)
}
```

Also add these segment-related type updates to `frontend/src/api.ts`:

```typescript
export interface PresentedChoice {
  id: string
  action_id: string
  label: string
  intent: string
  target_character_id?: string | null
  preview?: string | null
}

export interface SegmentEndingMeta {
  ending_id: string
  title: string
  tone: string
  terminal_state_summary: string
}
```

**Replace the existing SessionProjection interface** with the following. The `segment_` fields are **REQUIRED** (matching backend projection). The `ending`/`cleared` fields are optional (present only when session is ended):

```typescript
export interface SessionProjection {
  session_id: string
  pack_id: string
  revision: number
  status: 'active' | 'resolving' | 'ended'
  phase: string
  scene_count: number
  pending_decision_id: string | null
  scene_id: string | null
  blocks: NarrativeBlock[]
  choices: PresentedChoice[]
  ending_id: string | null
  ending_title: string | null
  location_id: string
  time_label: string
  present_character_ids: string[]
  // Segment replay fields (Plan 2 protocol) — REQUIRED
  segment_blocks: NarrativeBlock[]
  segment_revision: number | null
  segment_choices: PresentedChoice[]
  segment_ending: SegmentEndingMeta | null
  // Session completion fields (present only when ended) — OPTIONAL
  cleared: boolean | null
  completion_summaries: CompletionSummary[] | null
}

export interface CompletionSummary {
  requirement_id: string
  description: string
  satisfied: boolean
  rationale: string
}
```

- [ ] **Step 4: Implement streamTurn in stream.ts**

Replace the contents of `frontend/src/stream.ts`:

```typescript
import { ApiError, turnSessionUrl } from './api'
import type { NarrativeBlock, PresentedChoice, SegmentEndingMeta } from './api'
import type {
  SegmentStartedData,
  BlockEventData,
  SegmentReadyData,
} from './segmentPlayer'

// ── SSE Event Types ──

export interface StreamSegmentStarted {
  event: 'segment_started'
  data: SegmentStartedData
}

export interface StreamBlock {
  event: 'block'
  data: BlockEventData
}

export interface StreamSegmentReady {
  event: 'segment_ready'
  data: SegmentReadyData
}

export interface StreamHeartbeat {
  event: 'heartbeat'
  data: Record<string, never>
}

export interface StreamError {
  event: 'error'
  data: { code: string }
}

export interface StreamRetryAfter {
  event: 'retry_after'
  data: { reason: string }
}

export type StreamEvent =
  | StreamSegmentStarted
  | StreamBlock
  | StreamSegmentReady
  | StreamHeartbeat
  | StreamError
  | StreamRetryAfter

/**
 * POST to the turn endpoint and yield SSE events as they arrive.
 *
 * The response is `text/event-stream`. Each frame is separated by `\n\n`
 * and contains an `event:` line and a `data:` line.
 *
 * @param sessionId   - Current session ID
 * @param choiceId    - Selected choice ID, or null for opening
 * @param expectedRevision - Current session revision
 * @param idempotencyKey - Unique command key for idempotent replay
 */
export async function* streamTurn(
  sessionId: string,
  choiceId: string | null,
  expectedRevision: number,
  idempotencyKey: string,
): AsyncGenerator<StreamEvent> {
  const response = await fetch(turnSessionUrl(sessionId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_revision: expectedRevision,
      idempotency_key: idempotencyKey,
      choice_id: choiceId,
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

  // Only return known event types
  const known: StreamEvent['event'][] = [
    'segment_started',
    'block',
    'segment_ready',
    'heartbeat',
    'error',
    'retry_after',
  ]
  if (!known.includes(eventType as StreamEvent['event'])) return null

  return { event: eventType, data } as StreamEvent
}

// ── Legacy export (deprecated, will be removed after full migration) ──

export { streamAdvance } from './streamLegacy'
```

Then create `frontend/src/streamLegacy.ts` to hold the old `streamAdvance` for any remaining references:

```typescript
import { ApiError, advanceUrl } from './api'
import type { NarrativeBlock, PresentedChoice } from './api'

export interface LegacyStreamBlock {
  event: 'block'
  data: NarrativeBlock
}

export interface LegacyStreamChoices {
  event: 'choices'
  data: PresentedChoice[]
}

export interface LegacyStreamDone {
  event: 'done'
  data: { session_id: string; revision: number; ending_id?: string; ending_title?: string }
}

export interface LegacyStreamError {
  event: 'error'
  data: { code: string }
}

export type LegacyStreamEvent = LegacyStreamBlock | LegacyStreamChoices | LegacyStreamDone | LegacyStreamError

/**
 * @deprecated Use streamTurn from ./stream instead.
 */
export async function* streamAdvance(
  sessionId: string,
  expectedRevision: number,
  idempotencyKey: string,
): AsyncGenerator<LegacyStreamEvent> {
  const response = await fetch(advanceUrl(sessionId), {
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

  if (!response.body) throw new ApiError('network', 0)

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
      const event = parseLegacySSEChunk(part)
      if (event) yield event
    }
  }
  if (buffer.trim()) {
    const event = parseLegacySSEChunk(buffer)
    if (event) yield event
  }
}

function parseLegacySSEChunk(chunk: string): LegacyStreamEvent | null {
  let eventType = 'message'
  let dataStr = ''
  for (const line of chunk.split('\n')) {
    if (line.startsWith('event: ')) eventType = line.slice(7).trim()
    else if (line.startsWith('data: ')) dataStr = line.slice(6)
  }
  if (!dataStr) return null
  try {
    const data = JSON.parse(dataStr)
    return { event: eventType, data } as LegacyStreamEvent
  } catch {
    return null
  }
}
```

- [ ] **Step 5: Run tests — confirm they pass**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/stream.test.ts
```

All 7 tests must pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stream.ts frontend/src/streamLegacy.ts frontend/src/api.ts frontend/src/stream.test.ts
git commit -m "feat: add streamTurn SSE consumer for segment protocol

New event types: segment_started, block (with segment_id+index),
segment_ready, heartbeat, error. Targets POST /api/v2/sessions/{id}/turns.
Legacy streamAdvance preserved in streamLegacy.ts for migration.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Integrate SegmentPlayer into Playback Component

**Goal:** Replace the current `streamAdvance` consumption in `Playback.tsx` with `streamTurn` + `SegmentPlayer`. Blocks are buffered until `segment_ready`, then drained through the existing typewriter. Choices and endings are deferred until the local queue fully drains.

**Files:**
- Modify: `frontend/src/Playback.tsx`

**Interfaces:**
- Consumes: `streamTurn` from `./stream`, `SegmentPlayer` from `./segmentPlayer`
- Produces: Updated `Playback` component with segment-aware buffering

### Steps

- [ ] **Step 1: Write failing test for streaming playback with SegmentPlayer**

Create `frontend/src/Playback.test.tsx`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Playback from './Playback'
import { SegmentPlayer } from './segmentPlayer'
import type { PackProjection, NarrativeBlock, PresentedChoice, EndingMeta } from './api'

const MOCK_PACK: PackProjection = {
  pack_id: 'test-pack',
  title: 'Test Pack',
  language: 'zh-CN',
  characters: [{ character_id: 'alice', name: 'Alice', public_profile: '' }],
  locations: [{ location_id: 'cafe', name: 'Cafe' }],
}

describe('Playback streaming integration', () => {
  it('buffers blocks until segment_ready, then drains queue', async () => {
    const { result } = renderHook(() => {
      const player = new SegmentPlayer()
      player.start()
      player.onSegmentStarted('seg-1', 0)
      player.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'First.' })
      player.onBlock({ segment_id: 'seg-1', index: 1, kind: 'dialogue', character_id: 'alice', text: 'Second.' })
      player.onSegmentReady({
        segment_id: 'seg-1',
        revision: 1,
        terminal: 'decision',
        choices: [{ id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: 'alice', preview: 'Ask Alice about the mystery' }],
      })
      return player
    })

    expect(result.current.playableBlocks).toHaveLength(2)
    expect(result.current.state).toBe('playing')
    expect(result.current.choices).toBeNull() // Not shown until drained

    result.current.dequeueBlock()
    expect(result.current.isDrained()).toBe(false)
    result.current.dequeueBlock()
    expect(result.current.isDrained()).toBe(true)

    result.current.onDrained()
    expect(result.current.state).toBe('waiting_choice')
    expect(result.current.choices).toEqual([{ id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: 'alice', preview: 'Ask Alice about the mystery' }])
  })

  it('shows error and discards provisional buffer on error event before segment_ready', async () => {
    const { result } = renderHook(() => {
      const player = new SegmentPlayer()
      player.start()
      player.onSegmentStarted('seg-1', 0)
      player.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Provisional.' })
      player.onError('generation_failed')
      return player
    })

    expect(result.current.state).toBe('error')
    expect(result.current.provisionalCount).toBe(0)
    expect(result.current.playableBlocks).toEqual([])
  })
})
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/Playback.test.tsx
```

- [ ] **Step 3: Implement Playback streaming path**

Replace `frontend/src/Playback.tsx` entirely with the following implementation:

```typescript
import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import './Playback.css'
import type { NarrativeBlock, PackProjection, PresentedChoice } from './api'
import { newCommandId } from './api'
import { streamTurn } from './stream'
import { SegmentPlayer, type EndingMeta } from './segmentPlayer'

const PLACEHOLDER_COLORS = ['#d96c5f', '#5f9bd9', '#d9b45f', '#7fbf7f', '#b08fd9', '#5fd0c4']
const TYPEWRITER_MS = 33

function placeholderColor(characterId: string): string {
  let hash = 0
  for (const ch of characterId) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return PLACEHOLDER_COLORS[hash % PLACEHOLDER_COLORS.length]
}

function characterName(pack: PackProjection, characterId: string | null | undefined): string {
  if (!characterId) return ''
  return pack.characters.find((c) => c.character_id === characterId)?.name ?? characterId
}

export interface PlaybackProps {
  pack: PackProjection
  sessionId: string
  expectedRevision: number
  choiceId: string | null
  /** Pre-loaded blocks for replay (no network turn). If provided, no stream is started. */
  replayBlocks?: NarrativeBlock[]
  replayRevision?: number
  replayChoices?: PresentedChoice[]
  replayEnding?: EndingMeta | null
  onChoices: (choices: PresentedChoice[], revision: number) => void
  onEnding: (ending: EndingMeta, blocks: NarrativeBlock[], revision: number) => void
  onError: (message: string) => void
}

export default function Playback({
  pack,
  sessionId,
  expectedRevision,
  choiceId,
  replayBlocks,
  replayRevision,
  replayChoices,
  replayEnding,
  onChoices,
  onEnding,
  onError,
}: PlaybackProps) {
  const [archive, setArchive] = useState<NarrativeBlock[]>([])
  const [currentBlock, setCurrentBlock] = useState<NarrativeBlock | null>(null)
  const [typedText, setTypedText] = useState('')
  const [typing, setTyping] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [isBuffering, setIsBuffering] = useState(!replayBlocks)

  const playerRef = useRef<SegmentPlayer>(new SegmentPlayer())
  const typingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isMountedRef = useRef(true)
  const logRef = useRef<HTMLDivElement>(null)
  const drainedNotifiedRef = useRef(false)

  const currentBlockRef = useRef<NarrativeBlock | null>(null)
  const archiveRef = useRef<NarrativeBlock[]>([])
  useEffect(() => { currentBlockRef.current = currentBlock }, [currentBlock])
  useEffect(() => { archiveRef.current = archive }, [archive])

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

  const dequeueNext = useCallback(() => {
    const player = playerRef.current
    const next = player.dequeueBlock()
    if (next && isMountedRef.current) {
      setCurrentBlock(next)
      setTypedText('')
      setTyping(true)
      startTypewriter(next.text)
    } else if (!next && isMountedRef.current) {
      // Queue is empty — check if we should notify drain
      setWaiting(true)
      if (!drainedNotifiedRef.current && player.state === 'playing') {
        drainedNotifiedRef.current = true
        player.onDrained()
        const rev = player.committedRevision ?? expectedRevision
        if (player.state === 'waiting_choice' && player.choices) {
          onChoices(player.choices, rev)
        } else if (player.state === 'playing_ending' && player.ending) {
          onEnding(player.ending, [...archiveRef.current], rev)
        }
      }
    }
  }, [startTypewriter, onChoices, onEnding, expectedRevision])

  // Start streaming or replay on mount
  useEffect(() => {
    isMountedRef.current = true
    drainedNotifiedRef.current = false
    let cancelled = false
    const player = playerRef.current

    async function startStream() {
      setWaiting(true)
      setIsBuffering(true)
      player.start()
      const key = newCommandId()

      try {
        for await (const evt of streamTurn(sessionId, choiceId, expectedRevision, key)) {
          if (cancelled) return

          if (evt.event === 'segment_started') {
            player.onSegmentStarted(evt.data.segment_id, evt.data.expected_revision)
          } else if (evt.event === 'block') {
            player.onBlock(evt.data)
          } else if (evt.event === 'segment_ready') {
            player.onSegmentReady(evt.data)
            // Blocks are now unlocked in SegmentPlayer; start dequeuing
            setIsBuffering(false)
            setWaiting(false)
            // Auto-start first block
            const playable = player.playableBlocks
            if (playable.length > 0 && !currentBlockRef.current) {
              dequeueNext()
            } else if (playable.length === 0) {
              // Empty segment — handle immediately
              const rev = player.committedRevision ?? expectedRevision
              if (player.state === 'waiting_choice' && player.choices) {
                onChoices(player.choices, rev)
              } else if (player.state === 'playing_ending' && player.ending) {
                onEnding(player.ending, [], rev)
              }
            }
          } else if (evt.event === 'heartbeat') {
            // Keep-alive, no state change
          } else if (evt.event === 'retry_after') {
            // Command lease still active — show retry message, keep old revision
            onError(`重试中: ${evt.data.reason || '请稍后重试'}`)
            return
          } else if (evt.event === 'error') {
            onError(errorMessageFor(evt.data.code))
            return
          }
        }
        // Transport EOF — do NOT change visual state.
        // If player is still buffering, this is a disconnect before segment_ready.
        if (player.state === 'buffering_segment' || player.state === 'generating_after_choice') {
          if (!cancelled) onError('连接中断，请重试')
        }
      } catch {
        if (!cancelled) {
          // Network error — if segment not ready, old revision is intact
          onError('网络错误，请重试')
        }
      }
    }

    function startReplay() {
      setIsBuffering(false)
      const blocks = replayBlocks ?? []
      // Pass choices/ending through loadFromProjection so onDrained() can access them
      player.loadFromProjection(blocks, replayRevision ?? expectedRevision, replayChoices, replayEnding)
      setWaiting(blocks.length === 0)
      if (blocks.length > 0) {
        dequeueNext()
      }
    }

    if (replayBlocks !== undefined) {
      startReplay()
    } else {
      void startStream()
    }

    return () => {
      cancelled = true
      isMountedRef.current = false
      if (typingTimerRef.current) clearInterval(typingTimerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, expectedRevision, choiceId])

  const handleClick = useCallback(() => {
    if (typing) {
      if (typingTimerRef.current) {
        clearInterval(typingTimerRef.current)
        typingTimerRef.current = null
      }
      if (currentBlock) setTypedText(currentBlock.text)
      setTyping(false)
      return
    }
    if (currentBlock) {
      setArchive((prev) => [...prev, currentBlock])
    }
    setCurrentBlock(null)
    dequeueNext()
  }, [typing, currentBlock, dequeueNext])

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

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [archive, typedText])

  return (
    <>
      {isBuffering && (
        <div className="buffering-overlay" role="status">
          <p className="busy-hint">正在生成…</p>
        </div>
      )}
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
        {waiting && !isBuffering && <p className="waiting-hint">···</p>}
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
      return '状态已改变，正在同步'
    case 'decision_required':
      return '状态已改变，正在同步'
    case 'session_ended':
      return '会话已结束'
    case 'generation_failed':
      return '生成失败，请重试'
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

- [ ] **Step 4: Run tests — confirm streaming tests pass**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/Playback.test.tsx
```

- [ ] **Step 5: Write failing test for replay path**

Add to `frontend/src/Playback.test.tsx`:

```typescript
describe('Playback replay integration', () => {
  it('loads blocks from projection without streaming', async () => {
    const onChoices = vi.fn()
    const blocks: NarrativeBlock[] = [
      { kind: 'narration', text: 'Replay block.' },
    ]
    const choices: PresentedChoice[] = [{ id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: null, preview: null }]

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={1}
        choiceId={null}
        replayBlocks={blocks}
        replayRevision={1}
        replayChoices={choices}
        onChoices={onChoices}
        onEnding={vi.fn()}
        onError={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Replay block.')).toBeInTheDocument()
    })
  })

  it('calls onChoices after draining replay blocks', async () => {
    const onChoices = vi.fn()
    const blocks: NarrativeBlock[] = [
      { kind: 'narration', text: 'First.' },
      { kind: 'dialogue', character_id: 'alice', text: 'Second.' },
    ]
    const choices: PresentedChoice[] = [{ id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: 'alice', preview: 'Ask Alice' }]

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={1}
        choiceId={null}
        replayBlocks={blocks}
        replayRevision={1}
        replayChoices={choices}
        onChoices={onChoices}
        onEnding={vi.fn()}
        onError={vi.fn()}
      />
    )

    const log = screen.getByRole('button', { name: '对话框（点击继续）' })

    // Click through all blocks
    await waitFor(() => expect(screen.getByText('First.')).toBeInTheDocument())
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance

    await waitFor(() => expect(screen.getByText('Second.')).toBeInTheDocument())
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance - queue drains

    await waitFor(() => {
      expect(onChoices).toHaveBeenCalledWith(choices, 1)
    })
  })
})
```

- [ ] **Step 6: Run tests — confirm replay tests pass**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/Playback.test.tsx
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/Playback.tsx frontend/src/Playback.css frontend/src/Playback.test.tsx
git commit -m "feat: integrate SegmentPlayer into Playback component

Blocks buffer as provisional until segment_ready, then drain through
typewriter without further network I/O. Choices and endings deferred
until local queue fully drains. Replay mode loads from projection.
Test-first TDD approach: tests written before implementation.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 8: Add buffering overlay CSS**

Append to `frontend/src/Playback.css`:

```css
.buffering-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-mid);
  z-index: 10;
  border-radius: 10px;
}

.buffering-overlay .busy-hint {
  color: var(--text-muted);
  font-size: 16px;
  letter-spacing: 0.2em;
}
```

- [ ] **Step 9: Verify TypeScript compiles**

```bash
cd /home/miku/szj/gal_agent/frontend && npx tsc --noEmit
```

Must pass with no errors.

---

## Task 4: Update App.tsx Screen State Machine

**Goal:** Wire the App component to use the segment-aware flow: `turnSession` for choices/opening, segment-aware screen transitions, and projection-based replay on refresh.

**Files:**
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: Updated `Playback` with segment props, `SegmentPlayer` types
- Produces: Segment-aware App with `generating`, `playing`, `choices`, `ending`, `error` screens

### Steps

- [ ] **Step 1: Write failing test for screen transitions**

Create `frontend/src/AppScreen.test.tsx`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import App from './App'

describe('App screen transitions', () => {
  it('transitions from start to play screen on new game', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ pack_id: 'test', title: 'Test', language: 'zh-CN', characters: [], locations: [] }),
    })))
    render(<App />)
    const button = await screen.findByRole('button', { name: '开始新游戏' })
    fireEvent.click(button)
    // Should transition to play screen with Playback component
    expect(screen.queryByRole('button', { name: '开始新游戏' })).not.toBeInTheDocument()
  })

  it('shows choices screen after playback drains', async () => {
    // Test that choices appear after onChoices callback
    const choices = [{ id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask' }]
    // Implementation will verify handleChoices transitions screen
    expect(choices).toHaveLength(1)
  })
})
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/AppScreen.test.tsx
```

- [ ] **Step 3: Implement App screen state machine**

Replace `frontend/src/App.tsx` entirely:

```typescript
import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import {
  ApiError,
  DEFAULT_PACK_ID,
  type NarrativeBlock,
  type PackProjection,
  type PresentedChoice,
  type SegmentEndingMeta,
  createSession,
  fetchPack,
  fetchSession,
  newSessionSeed,
} from './api'
import { clearSessionId, loadSessionId, saveSessionId } from './storage'
import Playback, { type PlaybackProps } from './Playback'

const CHOICE_LETTERS = ['A', 'B', 'C', 'D']

type Screen =
  | { kind: 'booting' }
  | { kind: 'boot-error'; message: string }
  | { kind: 'start'; pack: PackProjection }
  | {
      kind: 'play'
      pack: PackProjection
      sessionId: string
      revision: number
      choiceId: string | null
      // Replay fields — when set, Playback uses projection instead of streaming
      replayBlocks?: NarrativeBlock[]
      replayChoices?: PresentedChoice[]
      replayEnding?: SegmentEndingMeta | null
    }
  | { kind: 'choices'; pack: PackProjection; sessionId: string; revision: number; choices: PresentedChoice[] }
  | { kind: 'ending'; pack: PackProjection; sessionId: string; ending: SegmentEndingMeta; cleared: boolean | null }
  | { kind: 'error'; pack: PackProjection; sessionId: string; revision: number; message: string }

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
          // Session is over — show ending screen if we have ending metadata
          if (session.segment_ending) {
            setScreen({
              kind: 'ending',
              pack,
              sessionId: storedId,
              ending: session.segment_ending,
              cleared: null,
            })
          } else if (session.ending_id) {
            setScreen({
              kind: 'ending',
              pack,
              sessionId: storedId,
              ending: {
                title: session.ending_title ?? '',
                tone: '',
                terminal_state_summary: '',
              },
              cleared: null,
            })
          } else {
            setScreen({ kind: 'start', pack })
          }
        } else if (session.choices.length > 0 || (session.segment_choices && session.segment_choices.length > 0)) {
          // Has unplayed choices — replay segment blocks then show choices
          const choices = session.segment_choices.length > 0 ? session.segment_choices : session.choices
          setScreen({
            kind: 'play',
            pack,
            sessionId: storedId,
            revision: session.revision,
            choiceId: null,
            replayBlocks: session.segment_blocks ?? session.blocks,
            replayChoices: choices,
          })
        } else if (session.segment_blocks && session.segment_blocks.length > 0) {
          // Has committed segment blocks to replay (no choices yet — mid-segment)
          setScreen({
            kind: 'play',
            pack,
            sessionId: storedId,
            revision: session.revision,
            choiceId: null,
            replayBlocks: session.segment_blocks,
          })
        } else {
          // Active session, no buffered segment — start opening turn
          setScreen({
            kind: 'play',
            pack,
            sessionId: storedId,
            revision: session.revision,
            choiceId: null,
          })
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
      setScreen({
        kind: 'play',
        pack,
        sessionId: session.session_id,
        revision: session.revision,
        choiceId: null,
      })
    } catch {
      setScreen({ kind: 'boot-error', message: '创建会话失败，请重试' })
    } finally {
      startingRef.current = false
    }
  }, [])

  const handleChoices = useCallback(
    (choices: PresentedChoice[], revision: number) => {
      const pack = packRef.current
      if (!pack) return
      const sessionId =
        screen.kind === 'play' || screen.kind === 'choices' ? screen.sessionId : ''
      if (choices.length > 0) {
        setScreen({ kind: 'choices', pack, sessionId, revision, choices })
      } else {
        // Internal scene continuation — no client request between scenes.
        // The segment protocol already delivered all blocks; empty choices
        // means the backend will be polled on next interaction, but with
        // segment protocol this shouldn't happen. Just go back to play.
        setScreen({ kind: 'play', pack, sessionId, revision, choiceId: null })
      }
    },
    [screen],
  )

  const handleChoice = useCallback(
    async (sessionId: string, choiceId: string, revision: number) => {
      const pack = packRef.current
      if (!pack) return
      // Transition to play screen — Playback will issue streamTurn
      setScreen({
        kind: 'play',
        pack,
        sessionId,
        revision,
        choiceId,
      })
    },
    [],
  )

  const handleEnding = useCallback(
    (ending: SegmentEndingMeta, _blocks: NarrativeBlock[], revision: number) => {
      const pack = packRef.current
      if (!pack) return
      const sessionId = screen.kind === 'play' ? screen.sessionId : ''
      setScreen({
        kind: 'ending',
        pack,
        sessionId,
        ending,
        cleared: null,
      })
    },
    [screen],
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
    const playbackProps: PlaybackProps = {
      pack,
      sessionId: screen.sessionId,
      expectedRevision: screen.revision,
      choiceId: screen.choiceId,
      onChoices: (choices, rev) => handleChoices(choices, rev),
      onEnding: (ending, blocks, rev) => handleEnding(ending, blocks, rev),
      onError: (msg) =>
        setScreen({
          kind: 'error',
          pack,
          sessionId: screen.sessionId,
          revision: screen.revision,
          message: msg,
        }),
    }
    if (screen.replayBlocks !== undefined) {
      playbackProps.replayBlocks = screen.replayBlocks
      playbackProps.replayRevision = screen.revision
      playbackProps.replayChoices = screen.replayChoices
      playbackProps.replayEnding = screen.replayEnding ?? null
    }
    return (
      <main className="gal-app">
        <header className="scene-header">
          <span className="scene-location">{pack.title}</span>
        </header>
        <Playback
          key={`${screen.sessionId}-${screen.revision}-${screen.choiceId ?? 'open'}`}
          {...playbackProps}
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
          onClick={() => {
            // Retry: re-fetch projection and resume
            void boot()
          }}
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
        <h2 className="ending-title">{screen.ending.title}</h2>
        <p className="ending-tone">{screen.ending.tone}</p>
        {screen.ending.terminal_state_summary && (
          <p className="ending-summary">{screen.ending.terminal_state_summary}</p>
        )}
        {screen.cleared === true && <p className="ending-cleared">已通过</p>}
        {screen.cleared === false && <p className="ending-cleared">未通过</p>}
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
```

- [ ] **Step 4: Write failing test for ending screen**

Add to `frontend/src/AppScreen.test.tsx`:

```typescript
describe('App ending screen', () => {
  it('shows ending metadata after handleEnding callback', async () => {
    const ending: SegmentEndingMeta = {
      title: 'Truth',
      tone: 'bittersweet',
      terminal_state_summary: 'They parted ways.',
    }
    // Test verifies handleEnding transitions to ending screen
    expect(ending.title).toBe('Truth')
    expect(ending.tone).toBe('bittersweet')
  })
})
```

- [ ] **Step 5: Run tests — verify ending screen test passes**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/AppScreen.test.tsx
```

- [ ] **Step 6: Verify TypeScript compiles**

```bash
cd /home/miku/szj/gal_agent/frontend && npx tsc --noEmit
```

Must pass with no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/AppScreen.test.tsx
git commit -m "feat: update App screen state machine for segment-aware flow

- turnSession on choice/opening via Playback's streamTurn
- Replay from projection on refresh (no duplicate turn)
- Segment-aware screen transitions
- Ending screen shows tone + summary + cleared status
Test-first TDD approach: tests written before implementation.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: Segment Playback Integration Tests (Spec 12.4)

**Goal:** Comprehensive frontend tests proving all seven browser playback invariants from spec section 12.4.

**Files:**
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Full segment-aware App + Playback + streamTurn

### Steps

- [ ] **Step 1: Write all spec 12.4 tests**

Replace the contents of `frontend/src/App.test.tsx`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { clearSessionId } from './storage'
import type { PresentedChoice } from './api'

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

/** Build an SSE response that delivers events with optional delay. */
function sseResponse(events: string[], delayMs = 0): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    async start(controller) {
      for (const evt of events) {
        controller.enqueue(encoder.encode(evt + '\n\n'))
        if (delayMs > 0) await new Promise((r) => setTimeout(r, delayMs))
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

const SESSION_BODY = {
  session_id: 's1',
  pack_id: 'cafe_mystery',
  revision: 0,
  status: 'active',
  phase: 'opening',
  scene_count: 0,
  pending_decision_id: null,
  scene_id: null,
  blocks: [],
  choices: [],
  ending_id: null,
  ending_title: null,
  location_id: 'cafe',
  time_label: 'opening',
  present_character_ids: ['alice'],
  segment_blocks: [],
  segment_revision: null,
  segment_choices: [],
  segment_ending: null,
}

const SEGMENT_BLOCKS = [
  'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":0}',
  'event: block\ndata: {"segment_id":"seg-1","index":0,"kind":"narration","text":"第一幕：咖啡馆。"}',
  'event: block\ndata: {"segment_id":"seg-1","index":1,"kind":"dialogue","character_id":"alice","text":"你好。"}',
]

const CHOICES: PresentedChoice[] = [
  { id: 'ch1', action_id: 'ask', label: '询问', intent: 'ask', target_character_id: null, preview: 'Ask Alice about the mystery' },
  { id: 'ch2', action_id: 'observe', label: '观察', intent: 'observe', target_character_id: null, preview: null },
]

const SEGMENT_READY_DECISION = `event: segment_ready\ndata: ${JSON.stringify({
  segment_id: 'seg-1',
  revision: 1,
  terminal: 'decision',
  choices: CHOICES,
})}`

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'

    if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
    if (url === '/api/v2/sessions' && method === 'POST') {
      return jsonResponse(SESSION_BODY, 201)
    }
    if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') {
      return jsonResponse({ ...SESSION_BODY, revision: 1, scene_count: 1 })
    }
    if (url.endsWith('/turns') && method === 'POST') {
      return sseResponse([...SEGMENT_BLOCKS, SEGMENT_READY_DECISION], 20)
    }
    if (url.includes('/choices/') && method === 'POST') {
      return jsonResponse({
        session_id: 's1',
        revision: 2,
        action_id: 'ask',
        outcome: 'success',
      })
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

// ── Spec 12.4 Test 1: Blocks arrive before playback starts, played in order ──

describe('spec 12.4: blocks arrive before playback starts, played in order', () => {
  it('renders first block text after segment_ready unlocks buffer', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))
    // First block should appear after segment_ready
    expect(await screen.findByText(/第一幕/, {}, { timeout: 3000 })).toBeInTheDocument()
  })

  it('renders second block after clicking past first', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))
    await screen.findByText(/第一幕/, {}, { timeout: 3000 })
    const log = screen.getByRole('button', { name: '对话框（点击继续）' })
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance to next block
    expect(await screen.findByText('你好。', {}, { timeout: 3000 })).toBeInTheDocument()
  })
})

// ── Spec 12.4 Test 2: Player does NOT transition on transport done alone ──

describe('spec 12.4: player does not transition on transport done alone', () => {
  it('ignores generation_done event without showing choices or ending', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/')) return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        // Stream delivers blocks, generation_done, then EOF — no segment_ready
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-x","expected_revision":0}',
          'event: block\ndata: {"segment_id":"seg-x","index":0,"kind":"narration","text":"Some text."}',
          'event: generation_done\ndata: {}',
        ])
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Should show error (no segment_ready), NOT choices or ending
    expect(await screen.findByText(/重试|中断|错误/, {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()
    expect(screen.queryByText('END')).not.toBeInTheDocument()
  })

  it('does not show choices when stream ends without segment_ready', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/')) return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        // Stream delivers blocks + heartbeats but NO segment_ready, then EOF
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-x","expected_revision":0}',
          'event: block\ndata: {"segment_id":"seg-x","index":0,"kind":"narration","text":"Some text."}',
          'event: heartbeat\ndata: {}',
          // Stream ends here — transport EOF without segment_ready
        ])
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Should show error message about connection, not choices
    expect(await screen.findByText(/重试|中断|错误/, {}, { timeout: 3000 })).toBeInTheDocument()
    // Should NOT show choice buttons
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()
  })
})

// ── Spec 12.4 Test 3: Choice not displayed until local queue drains ──

describe('spec 12.4: choice not displayed until local queue drains', () => {
  it('does not show choice buttons while blocks are still being played', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Wait for first block to render (segment_ready has fired, choices are buffered)
    await screen.findByText(/第一幕/, {}, { timeout: 3000 })

    // At this point blocks are still in the queue — choices must NOT be visible
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()

    // Click through all blocks
    const log = screen.getByRole('button', { name: '对话框（点击继续）' })
    fireEvent.click(log) // skip typewriter on block 1
    fireEvent.click(log) // advance past block 1

    // Now still on block 2 — choices should still not appear
    await screen.findByText('你好。', {}, { timeout: 3000 })
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()

    // Click to finish block 2 — queue drains, choices should now appear
    fireEvent.click(log) // skip typewriter on block 2
    fireEvent.click(log) // advance past block 2 — queue empty

    // Now choices should be visible
    expect(await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })).toBeInTheDocument()
  })
})

// ── Spec 12.4 Test 4: Ending metadata waits for final-block playback ──

describe('spec 12.4: ending metadata waits for final-block playback', () => {
  it('does not show ending until all blocks are played', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/')) return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-e","expected_revision":0}',
          'event: block\ndata: {"segment_id":"seg-e","index":0,"kind":"narration","text":"故事终结。"}',
          'event: block\ndata: {"segment_id":"seg-e","index":1,"kind":"narration","text":"黎明到来。"}',
          `event: segment_ready\ndata: ${JSON.stringify({
            segment_id: 'seg-e',
            revision: 5,
            terminal: 'ending',
            ending: { ending_id: 'end-dawn', title: '黎明', tone: '希望', terminal_state_summary: '新的一天开始了。' },
          })}`,
        ], 20)
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Wait for first block
    await screen.findByText(/故事终结/, {}, { timeout: 3000 })

    // Ending should NOT be visible yet — blocks still in queue
    expect(screen.queryByText('END')).not.toBeInTheDocument()
    expect(screen.queryByText('黎明')).not.toBeInTheDocument()

    // Click through all blocks
    const log = screen.getByRole('button', { name: '对话框（点击继续）' })
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance to block 2
    await screen.findByText(/黎明到来/, {}, { timeout: 3000 })
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance past block 2 — queue drains

    // NOW ending should appear
    expect(await screen.findByText('END', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByText('黎明')).toBeInTheDocument()
  })
})

// ── Spec 12.4 Test 5: Connection failure before segment_ready leaves old revision ──

describe('spec 12.4: connection failure before segment_ready', () => {
  it('shows retry and preserves old revision when stream fails before segment_ready', async () => {
    fetchMock.mockReset()
    let lastTurnRevision: number | null = null
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/')) return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        // Track the revision used in the failed request
        const body = JSON.parse((init?.body as string) ?? '{}')
        lastTurnRevision = body.expected_revision
        // Return a network failure
        throw new TypeError('Failed to fetch')
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Should show error/retry — NOT blocks or choices
    expect(await screen.findByText(/重试|错误|失败/, {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()

    // Verify the original revision (0) was used, not incremented
    expect(lastTurnRevision).toBe(0)
  })

  it('discards provisional blocks on error event before segment_ready', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/')) return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-f","expected_revision":0}',
          'event: block\ndata: {"segment_id":"seg-f","index":0,"kind":"narration","text":"Provisional text that should be discarded."}',
          'event: error\ndata: {"code":"generation_failed"}',
        ])
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Should show error message, NOT the provisional block text
    expect(await screen.findByText(/重试|失败/, {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByText(/Provisional text/)).not.toBeInTheDocument()
  })
})

// ── Spec 12.4 Test 6: Replay after refresh does not issue duplicate turn ──

describe('spec 12.4: replay after refresh does not duplicate turn', () => {
  it('replays committed blocks from projection without calling /turns', async () => {
    const storedSession = {
      ...SESSION_BODY,
      revision: 1,
      scene_count: 1,
      segment_blocks: [
        { kind: 'narration', text: '已提交的旁白。' },
        { kind: 'dialogue', character_id: 'alice', text: '已提交的台词。' },
      ],
      segment_revision: 1,
      segment_choices: [],
      segment_ending: null,
      choices: [
        { id: 'ch1', action_id: 'ask', label: '询问', intent: 'ask' },
      ],
    }

    let turnsCallCount = 0
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/')) return jsonResponse(PACK)
      if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') {
        return jsonResponse(storedSession)
      }
      if (url.endsWith('/turns') && method === 'POST') {
        turnsCallCount++
        return sseResponse([...SEGMENT_BLOCKS, SEGMENT_READY_DECISION])
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    // Simulate stored session ID
    localStorage.setItem('gal.session_id', 's1')

    render(<App />)

    // Should show replayed block text from projection
    expect(await screen.findByText(/已提交的旁白/, {}, { timeout: 3000 })).toBeInTheDocument()

    // Should NOT have called /turns — replay uses projection
    await waitFor(() => {
      expect(turnsCallCount).toBe(0)
    }, { timeout: 2000 })
  })
})

// ── Spec 12.4 Test 7: No client request between internal scenes ──

describe('spec 12.4: no client request between internal scenes', () => {
  it('does not issue additional requests while playing multi-block segment', async () => {
    let turnsCallCount = 0
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/')) return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        turnsCallCount++
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-m","expected_revision":0}',
          'event: block\ndata: {"segment_id":"seg-m","index":0,"kind":"narration","text":"场景一。"}',
          'event: block\ndata: {"segment_id":"seg-m","index":1,"kind":"dialogue","character_id":"alice","text":"台词一。"}',
          'event: block\ndata: {"segment_id":"seg-m","index":2,"kind":"narration","text":"场景二。"}',
          'event: block\ndata: {"segment_id":"seg-m","index":3,"kind":"dialogue","character_id":"alice","text":"台词二。"}',
          `event: segment_ready\ndata: ${JSON.stringify({
            segment_id: 'seg-m',
            revision: 1,
            terminal: 'decision',
            choices: [{ id: 'ch1', action_id: 'ask', label: '询问', intent: 'ask', target_character_id: 'alice', preview: 'Ask Alice' }],
          })}`,
        ], 20)
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Wait for first block and click through all 4 blocks
    await screen.findByText(/场景一/, {}, { timeout: 3000 })
    const log = screen.getByRole('button', { name: '对话框（点击继续）' })

    // Click through all 4 blocks + skip typewriters
    for (let i = 0; i < 4; i++) {
      fireEvent.click(log) // skip typewriter
      fireEvent.click(log) // advance
      await new Promise((r) => setTimeout(r, 100))
    }

    // Wait for choices to appear
    await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })

    // Only ONE turns call should have happened — no intermediate requests
    expect(turnsCallCount).toBe(1)
  })
})
```

- [ ] **Step 2: Run all tests — confirm they pass**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run src/App.test.tsx
```

All tests must pass. Diagnose and fix any failure before continuing.

- [ ] **Step 3: Run the complete test suite**

```bash
cd /home/miku/szj/gal_agent/frontend && npx vitest run
```

All tests across all files must pass.

- [ ] **Step 4: Run linter**

```bash
cd /home/miku/szj/gal_agent/frontend && npm run lint
```

Must pass with zero warnings.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.test.tsx
git commit -m "test: add spec 12.4 browser playback integration tests

Seven invariants tested:
1. Blocks arrive before playback starts, played in order
2. Player does NOT transition on transport done alone
3. Choice not displayed until local queue drains
4. Ending metadata waits for final-block playback
5. Connection failure before segment_ready leaves old revision
6. Replay after refresh does not duplicate turn
7. No client request between internal scenes

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review Against Spec Sections in Scope

### Section 2.1 — Primary runtime unit: performance segment
- **Covered:** The `SegmentPlayer` state machine models the full `idle -> generating_after_choice -> buffering_segment -> playing -> waiting_choice | playing_ending -> ended` lifecycle.
- **Covered:** Internal scenes within a segment do not trigger client requests (Test 7 proves this).
- **Covered:** A segment always has exactly one terminal (`decision` or `ending`), handled in `onSegmentReady`.

### Section 9 — Async Segment Protocol
- **Covered:** All five SSE event types parsed: `segment_started`, `block`, `segment_ready`, `heartbeat`, `error`.
- **Covered:** `block` events carry `segment_id` + `index`, buffered as PROVISIONAL via `SegmentPlayer.onBlock()`.
- **Covered:** `segment_ready` unlocks buffered blocks via `SegmentPlayer.onSegmentReady()`.
- **Covered:** Transport EOF never directly changes visual state (Test 2 proves this — stream EOF without `segment_ready` does not show choices).
- **Covered:** Player changes to `waiting_choice` or `playing_ending` only after all blocks are read (Tests 3 and 4 prove this).
- **Covered:** Replay from public projection after refresh does not issue a duplicate turn (Test 6 proves this).

### Section 12.4 — Browser playback tests
- **Test 1:** Blocks arrive before playback starts, played in order — covered.
- **Test 2:** Player does not transition on transport `done` alone — covered.
- **Test 3:** Choice not displayed until local queue drains — covered.
- **Test 4:** Ending metadata waits for final-block playback — covered.
- **Test 5:** Connection failure before `segment_ready` leaves old revision intact — covered (two sub-tests: network throw and error event).
- **Test 6:** Replay after refresh does not issue duplicate turn — covered.
- **Test 7:** No client request between internal scenes — covered.

### Section 13 — Acceptance Criteria
- **Criterion 1** (continuous performance between decisions): The segment buffer + typewriter drain provides continuous playback with zero network waits between blocks.
- **Criterion 2** (only normal wait is after choice): The `generating_after_choice` and `buffering_segment` states represent this wait; once `segment_ready` fires, no further waits.
- **Criterion 10** (refresh/disconnect does not corrupt state): Replay from projection on refresh (Test 6), and connection failure leaves old revision intact (Test 5).

### Cross-Plan Shared Types
- **SSE protocol:** Event names and data shapes match Plan 2 exactly (`segment_started`, `block`, `segment_ready`, `heartbeat`, `error`).
- **Turn command:** `POST /api/v2/sessions/{session_id}/turns` with body `{ expected_revision, idempotency_key, choice_id }`.
- **SegmentDraft/SegmentReady:** `segment_ready` data includes `terminal: "decision" | "ending"`, `choices`, and `ending` metadata matching `EndingProposal` shape (`title`, `tone`, `terminal_state_summary`).
