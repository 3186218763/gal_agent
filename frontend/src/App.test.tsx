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
}

let currentSceneBlocks: string[]
let currentChoices: PresentedChoice[] | null
let currentDoneRevision: number
let currentEnding: { ending_id: string; title: string; tone: string; terminal_state_summary: string } | null

const fetchMock = vi.fn()

beforeEach(() => {
  currentSceneBlocks = [
    'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":0}',
    'event: block\ndata: {"segment_id":"seg-1","index":0,"kind":"narration","text":"第一幕：咖啡馆。"}',
    'event: block\ndata: {"segment_id":"seg-1","index":1,"kind":"dialogue","character_id":"alice","text":"你好。"}',
  ]
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
    if (url.includes('/choices/') && method === 'POST') {
      return jsonResponse({
        session_id: 's1',
        revision: currentDoneRevision + 1,
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

/** Click through the current block (skip typewriter, then advance). */
async function clickThrough(log: HTMLElement): Promise<void> {
  fireEvent.click(log) // skip typewriter
  fireEvent.click(log) // advance to next block
}

describe('streaming playback', () => {
  it('shows start screen and starts game on click', async () => {
    render(<App />)
    const start = await screen.findByRole('button', { name: '开始新游戏' })
    fireEvent.click(start)
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

  it('shows choice buttons after stream delivers choices and blocks drain', async () => {
    currentChoices = [
      { id: 'ch1', action_id: 'ask', label: '询问', intent: 'ask' },
    ]
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))

    // Segment blocks must drain before choices are shown
    const log = await screen.findByRole('button', { name: '对话框（点击继续）' }, { timeout: 3000 })
    await screen.findByText(/第一幕/, {}, { timeout: 3000 })
    await clickThrough(log) // block 1
    await screen.findByText('你好。', {}, { timeout: 3000 })
    await clickThrough(log) // block 2 — queue drains, choices surface

    expect(await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })).toBeInTheDocument()
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
  })

  it('shows ending screen when segment_ready has an ending', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/')) return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') {
        return jsonResponse(SESSION_BODY, 201)
      }
      if (url.endsWith('/turns') && method === 'POST') {
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":0}',
          'event: block\ndata: {"segment_id":"seg-1","index":0,"kind":"narration","text":"故事结束。"}',
          'event: segment_ready\ndata: {"segment_id":"seg-1","revision":5,"terminal":"ending","ending":{"ending_id":"truth","title":"真相","tone":"bittersweet","terminal_state_summary":"They parted ways."}}',
        ])
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))
    const log = await screen.findByRole('button', { name: '对话框（点击继续）' }, { timeout: 3000 })
    await screen.findByText(/故事结束/, {}, { timeout: 3000 })
    await clickThrough(log) // drain the final block — ending screen shows
    expect(await screen.findByText('真相', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByText('END')).toBeInTheDocument()
  })
})
