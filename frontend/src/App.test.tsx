import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { clearSessionId, saveSessionId } from './storage'
import type { PresentedChoice } from './api'

const PACK = {
  pack_id: 'yokai_after_school',
  title: '放学后，狐签与心跳',
  language: 'zh-CN',
  characters: [
    { character_id: 'alice', name: '艾丽丝', public_profile: '' },
    { character_id: 'protagonist', name: '悠真', public_profile: '' },
  ],
  locations: [{ location_id: 'cafe', name: '街角咖啡馆' }],
}

/** Build a SSE response that delivers events with small delays between them,
 *  simulating real streaming so the typewriter has time to render. */
function sseResponse(events: string[], delayMs = 20): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    async start(controller) {
      for (const evt of events) {
        controller.enqueue(encoder.encode(evt + '\n\n'))
        await new Promise((r) => setTimeout(r, delayMs))
      }
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

/**
 * SSE response that stops delivering events when the fetch init's signal
 * aborts (StrictMode double-mount cleanup): the stream errors so the
 * abandoned mount's turn cannot deliver a late segment_ready.
 */
function sseResponseAbortAware(
  events: string[],
  delayMs: number,
  signal?: AbortSignal | null,
  onDone?: (aborted: boolean) => void,
): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    async start(controller) {
      let aborted = false
      const onAbort = () => {
        aborted = true
        controller.error(signal?.reason ?? new DOMException('Aborted', 'AbortError'))
      }
      if (signal) signal.addEventListener('abort', onAbort)
      try {
        for (const evt of events) {
          if (aborted) break
          controller.enqueue(encoder.encode(evt + '\n\n'))
          await new Promise((r) => setTimeout(r, delayMs))
        }
      } catch {
        // controller was errored by an abort landing mid-enqueue
      }
      if (!aborted) {
        controller.close()
        signal?.removeEventListener('abort', onAbort)
      }
      onDone?.(aborted)
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

// SessionProjection shape — includes the segment replay fields and the
// optional completion fields the current App/Playback consume.
const SESSION_BODY = {
  session_id: 's1',
  pack_id: 'yokai_after_school',
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
  cleared: null,
  completion_summaries: [],
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

let currentSceneBlocks: string[]
let currentChoices: PresentedChoice[] | null
let currentDoneRevision: number
let currentEnding: { ending_id: string; title: string; tone: string; terminal_state_summary: string } | null

const fetchMock = vi.fn()

beforeEach(() => {
  currentSceneBlocks = [...SEGMENT_BLOCKS]
  currentChoices = null
  currentDoneRevision = 1
  currentEnding = null
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
      const events = [...currentSceneBlocks]
      events.push(`event: segment_ready\ndata: ${JSON.stringify({
        segment_id: 'seg-1',
        revision: currentDoneRevision,
        terminal: currentEnding ? 'ending' : 'decision',
        choices: currentChoices,
        ending: currentEnding,
      })}`)
      return sseResponse(events)
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

/** Click through the current block (skip typewriter, then advance). */
async function clickThrough(log: HTMLElement): Promise<void> {
  fireEvent.click(log) // skip typewriter
  fireEvent.click(log) // advance to next block
}

// ── Spec 12.4 Test 1: Blocks arrive before playback starts, played in order ──
// The plan's two Test 1 snippets are literal equivalents of the two
// 'streaming playback' tests below, so the existing versions are kept and
// hardened:
//   - 'shows start screen and starts game on click' ≈ plan snippet 1a
//     (first block renders only after segment_ready unlocks the buffer; the
//     buffering window also asserts provisional blocks stay hidden)
//   - 'displays dialogue after advancing past narration' == plan snippet 1b
//     (second block plays only after clicking past the first)

describe('streaming playback', () => {
  it('shows start screen and starts game on click', async () => {
    // Delay segment_ready after the first block so the buffering window
    // (provisional blocks hidden behind the overlay) is observable.
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        return sseResponse([...SEGMENT_BLOCKS.slice(0, 2), SEGMENT_READY_DECISION], 50)
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    const start = await screen.findByRole('button', { name: '开始新游戏' })
    fireEvent.click(start)

    // Before segment_ready the buffering overlay is up and provisional
    // blocks must NOT be displayed.
    expect(await screen.findByRole('status', {}, { timeout: 3000 })).toHaveTextContent('正在生成…')
    expect(screen.queryByText(/第一幕/)).not.toBeInTheDocument()

    // segment_ready unlocks the buffer — the first block renders.
    expect(await screen.findByText(/第一幕/, {}, { timeout: 3000 })).toBeInTheDocument()
  })

  it('displays dialogue after advancing past narration', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Wait for first block to appear
    await screen.findByText(/第一幕/, {}, { timeout: 3000 })

    // Click playback to advance past block 1 (skip typewriter + advance)
    const log = screen.getByRole('button', { name: '对话框（点击继续）' })
    await clickThrough(log)

    expect(await screen.findByText('你好。', {}, { timeout: 3000 })).toBeInTheDocument()
  })

  it('starts a new stream after selecting a choice', async () => {
    currentChoices = [
      { id: 'ch1', action_id: 'ask', label: '询问', intent: 'ask' },
    ]
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Wait for choices (drain the opening segment first) and click
    const log = await screen.findByRole('button', { name: '对话框（点击继续）' }, { timeout: 3000 })
    await screen.findByText(/第一幕/, {}, { timeout: 3000 })
    await clickThrough(log) // block 1
    await screen.findByText('你好。', {}, { timeout: 3000 })
    await clickThrough(log) // block 2 — queue drains, choices surface
    const choiceBtn = await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })
    fireEvent.click(choiceBtn)

    // After choice, a new stream starts and eventually delivers choices again
    const log2 = await screen.findByRole('button', { name: '对话框（点击继续）' }, { timeout: 3000 })
    await screen.findByText(/第一幕/, {}, { timeout: 3000 })
    await clickThrough(log2) // block 1
    await screen.findByText('你好。', {}, { timeout: 3000 })
    await clickThrough(log2) // block 2 — queue drains, choices surface
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /A 询问/ })).toBeInTheDocument()
    }, { timeout: 5000 })

    // The choice turn's POST /turns body must carry the chosen id, the
    // decision revision, and a fresh idempotency key.
    const turnCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/turns'))
    const choiceTurn = turnCalls[turnCalls.length - 1]
    const body = JSON.parse(choiceTurn[1].body as string)
    expect(body.choice_id).toBe('ch1')
    expect(body.expected_revision).toBe(1)
    expect(typeof body.idempotency_key).toBe('string')
    expect(body.idempotency_key.length).toBeGreaterThan(0)
  })
})

// ── Spec 12.4 Test 2: Player does NOT transition on transport done alone ──
// EOF without segment_ready is a disconnect, not a terminal state: the
// Playback surfaces 连接中断 and App shows the error screen — no choices,
// no ending may appear from transport done alone.

describe('spec 12.4: player does not transition on transport done alone', () => {
  it('shows the connection error when EOF arrives without segment_ready', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        // Blocks + unknown event, then EOF — no segment_ready ever arrives.
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

    expect(await screen.findByText('连接中断，请重试', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()
    expect(screen.queryByText('END')).not.toBeInTheDocument()
  })

  it('does not show choices when the stream ends with heartbeats but no segment_ready', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        // Blocks + heartbeats, then EOF — still no segment_ready.
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-x","expected_revision":0}',
          'event: block\ndata: {"segment_id":"seg-x","index":0,"kind":"narration","text":"Some text."}',
          'event: heartbeat\ndata: {}',
        ])
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    expect(await screen.findByText('连接中断，请重试', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()
    expect(screen.queryByText('END')).not.toBeInTheDocument()
  })
})

// ── Spec 12.4 Test 3: Choice not displayed until local queue drains ──
// Stronger than the removed 'shows choice buttons after stream delivers
// choices and blocks drain' test: it also asserts the negative case (choices
// stay hidden while blocks remain in the local queue).

describe('spec 12.4: choice not displayed until local queue drains', () => {
  it('does not show choice buttons while blocks are still being played', async () => {
    currentChoices = CHOICES
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // segment_ready has unlocked the buffer (first block is playing) but the
    // local queue has not drained — choices must stay hidden.
    await screen.findByText(/第一幕/, {}, { timeout: 3000 })
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()

    const log = screen.getByRole('button', { name: '对话框（点击继续）' })
    await clickThrough(log) // block 1 → block 2

    await screen.findByText('你好。', {}, { timeout: 3000 })
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()

    await clickThrough(log) // block 2 — queue drains, choices surface
    expect(await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })).toBeInTheDocument()
  })
})

// ── Spec 12.4 Test 4: Ending metadata waits for final-block playback ──
// Stronger than the removed 'shows ending screen when segment_ready has an
// ending' test: it also asserts the ending stays hidden until the queue
// drains, even though segment_ready already delivered the ending metadata.

describe('spec 12.4: ending metadata waits for final-block playback', () => {
  it('does not show ending until all blocks are played', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
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

    // Ending metadata arrived with segment_ready, but blocks are still queued.
    await screen.findByText(/故事终结/, {}, { timeout: 3000 })
    expect(screen.queryByText('END')).not.toBeInTheDocument()
    expect(screen.queryByText('黎明')).not.toBeInTheDocument()

    const log = screen.getByRole('button', { name: '对话框（点击继续）' })
    await clickThrough(log) // block 1 → block 2
    await screen.findByText(/黎明到来/, {}, { timeout: 3000 })
    expect(screen.queryByText('END')).not.toBeInTheDocument()

    await clickThrough(log) // block 2 — queue drains, ending screen shows
    expect(await screen.findByText('END', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByText('黎明')).toBeInTheDocument()
  })
})

// ── Spec 12.4 Test 5: Connection failure before segment_ready leaves old revision ──

describe('spec 12.4: connection failure before segment_ready', () => {
  it('shows retry and preserves old revision when the stream fails before segment_ready', async () => {
    fetchMock.mockReset()
    let lastTurnRevision: number | null = null
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        const body = JSON.parse((init?.body as string) ?? '{}')
        lastTurnRevision = body.expected_revision
        throw new TypeError('Failed to fetch')
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    expect(await screen.findByText('网络错误，请重试', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()

    // The failed turn used the old revision (0), not an incremented one.
    expect(lastTurnRevision).toBe(0)
  })

  it('discards provisional blocks on an error event before segment_ready', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
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

    expect(await screen.findByText('生成失败，请重试', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByText(/Provisional text/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()
  })
})

// ── Spec 12.4 Test 6: Replay after refresh does not issue duplicate turn ──
// A stored-session projection with committed segment_blocks/segment_choices
// must replay entirely from the projection — zero POST /turns calls.

describe('spec 12.4: replay after refresh does not duplicate turn', () => {
  it('replays committed blocks from the projection without calling /turns', async () => {
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
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
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

    saveSessionId('s1')
    render(<App />)

    // Replay shows committed block text from the projection…
    expect(await screen.findByText(/已提交的旁白/, {}, { timeout: 3000 })).toBeInTheDocument()

    // …and never issues a duplicate turn.
    await waitFor(() => {
      expect(turnsCallCount).toBe(0)
    }, { timeout: 2000 })

    // Draining the replayed blocks surfaces the stored choices — still no /turns.
    const log = screen.getByRole('button', { name: '对话框（点击继续）' })
    await clickThrough(log) // block 1
    await screen.findByText(/已提交的台词/, {}, { timeout: 3000 })
    await clickThrough(log) // block 2 — queue drains, choices surface
    expect(await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })).toBeInTheDocument()
    expect(turnsCallCount).toBe(0)
  })
})

// ── Spec 12.4 Test 7: No client request between internal scenes ──
// A single turn with 2 blocks + segment_ready(decision) must result in
// exactly one POST /turns call total after the queue drains.

describe('spec 12.4: no client request between internal scenes', () => {
  it('does not issue additional requests while playing a multi-block segment', async () => {
    let turnsCallCount = 0
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        turnsCallCount++
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-m","expected_revision":0}',
          'event: block\ndata: {"segment_id":"seg-m","index":0,"kind":"narration","text":"场景一。"}',
          'event: block\ndata: {"segment_id":"seg-m","index":1,"kind":"dialogue","character_id":"alice","text":"台词一。"}',
          `event: segment_ready\ndata: ${JSON.stringify({
            segment_id: 'seg-m',
            revision: 1,
            terminal: 'decision',
            choices: [CHOICES[0]],
          })}`,
        ], 20)
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Play the whole segment (2 blocks) without any intermediate requests.
    await screen.findByText(/场景一/, {}, { timeout: 3000 })
    const log = screen.getByRole('button', { name: '对话框（点击继续）' })
    await clickThrough(log) // block 1
    await screen.findByText(/台词一/, {}, { timeout: 3000 })
    await clickThrough(log) // block 2 — queue drains, choices surface

    expect(await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })).toBeInTheDocument()

    // One turn covered both internal scenes — no extra requests.
    expect(turnsCallCount).toBe(1)
  })
})

// ── StrictMode regression (fix 1f7c320): the first StrictMode mount's turn
//    is aborted; it must not surface an error or count as a real turn ──

describe('spec 12.4: StrictMode double-mount aborts the first turn cleanly', () => {
  it('streams to choices with exactly one non-aborted /turns call', async () => {
    let turnCalls = 0
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(SESSION_BODY, 201)
      if (url.endsWith('/turns') && method === 'POST') {
        // Count only turns whose stream completed without being aborted:
        // StrictMode's first mount aborts its fetch before any segment_ready.
        return sseResponseAbortAware([...SEGMENT_BLOCKS, SEGMENT_READY_DECISION], 20, init?.signal, (aborted) => {
          if (!aborted) turnCalls++
        })
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <StrictMode>
        <App />
      </StrictMode>
    )
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // The second (non-aborted) mount streams the segment to choices.
    const log = await screen.findByRole('button', { name: '对话框（点击继续）' }, { timeout: 3000 })
    await screen.findByText(/第一幕/, {}, { timeout: 3000 })
    await clickThrough(log) // block 1
    await screen.findByText('你好。', {}, { timeout: 3000 })
    await clickThrough(log) // block 2 — queue drains, choices surface

    expect(await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })).toBeInTheDocument()
    // The aborted first mount must not surface an error screen.
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument()
    expect(screen.queryByText(/状态已改变/)).not.toBeInTheDocument()
    // Exactly one non-aborted turn — the StrictMode abort is not counted.
    await waitFor(() => {
      expect(turnCalls).toBe(1)
    }, { timeout: 3000 })
  })
})
