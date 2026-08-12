import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { clearSessionId, saveSessionId } from './storage'
import type { PackProjection, SessionProjection } from './api'

const PACK: PackProjection = {
  pack_id: 'cafe_mystery',
  title: '咖啡馆疑云',
  language: 'zh-CN',
  characters: [
    { character_id: 'alice', name: '艾丽丝', public_profile: '' },
    { character_id: 'protagonist', name: '悠真', public_profile: '' },
  ],
  locations: [{ location_id: 'cafe', name: '街角咖啡馆' }],
}

function sseResponse(events: string[], delayMs = 10): Response {
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

function sessionBody(overrides: Partial<SessionProjection> = {}): SessionProjection {
  return {
    session_id: 's1',
    pack_id: 'cafe_mystery',
    revision: 1,
    status: 'active',
    phase: 'opening',
    scene_count: 1,
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
    ...overrides,
  }
}

const CHOICE = { id: 'c1', action_id: 'ask', label: '询问', intent: 'ask' }

const TURN_EVENTS = [
  'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":0}',
  'event: block\ndata: {"segment_id":"seg-1","index":0,"kind":"narration","text":"新的故事开始了。"}',
  'event: segment_ready\ndata: ' + JSON.stringify({
    segment_id: 'seg-1',
    revision: 1,
    terminal: 'decision',
    choices: [CHOICE],
  }),
]

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
    if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(sessionBody({ revision: 0 }), 201)
    if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') return jsonResponse(sessionBody())
    if (url.endsWith('/turns') && method === 'POST') return sseResponse(TURN_EVENTS)
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

describe('App screen transitions', () => {
  it('transitions from start to play screen on new game', async () => {
    render(<App />)
    const start = await screen.findByRole('button', { name: '开始新游戏' })
    fireEvent.click(start)
    // Start screen is gone; Playback is rendered and streams the opening turn.
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '开始新游戏' })).not.toBeInTheDocument()
    })
    expect(await screen.findByRole('button', { name: '对话框（点击继续）' }, { timeout: 3000 })).toBeInTheDocument()
    expect(await screen.findByText(/新的故事开始了/, {}, { timeout: 3000 })).toBeInTheDocument()
  })

  it('boots with stored session and replays committed segment blocks without a turn', async () => {
    saveSessionId('s1')
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') {
        return jsonResponse(
          sessionBody({
            segment_blocks: [{ kind: 'narration', text: '回忆片段。' }],
            segment_choices: [CHOICE],
          }),
        )
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    render(<App />)
    expect(await screen.findByText(/回忆片段/, {}, { timeout: 3000 })).toBeInTheDocument()
    const turnCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/turns'))
    expect(turnCalls).toHaveLength(0)
  })

  it('shows choices screen after playback drains', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))
    const log = await screen.findByRole('button', { name: '对话框（点击继续）' }, { timeout: 3000 })
    await screen.findByText(/新的故事开始了/, {}, { timeout: 3000 })
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance — queue drains, choices surface
    expect(await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })).toBeInTheDocument()
  })

  it('shows ending screen after segment_ready with terminal ending', async () => {
    const ending = {
      ending_id: 'truth',
      title: '真相大白',
      tone: '苦涩',
      terminal_state_summary: '两人各奔东西。',
    }
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(sessionBody({ revision: 0 }), 201)
      if (url.endsWith('/turns') && method === 'POST') {
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":0}',
          'event: block\ndata: {"segment_id":"seg-1","index":0,"kind":"narration","text":"故事走到终点。"}',
          'event: segment_ready\ndata: ' + JSON.stringify({ segment_id: 'seg-1', revision: 2, terminal: 'ending', ending }),
        ])
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))
    const log = await screen.findByRole('button', { name: '对话框（点击继续）' }, { timeout: 3000 })
    await screen.findByText(/故事走到终点/, {}, { timeout: 3000 })
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance — queue drains, ending screen shows
    expect(await screen.findByText('真相大白', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByText('END')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新开始' })).toBeInTheDocument()
  })
})
