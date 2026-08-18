import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { clearSessionId, saveSessionId } from './storage'
import type { PackProjection, SessionProjection } from './api'

const PACK: PackProjection = {
  pack_id: 'yokai_after_school',
  title: '放学后，狐签与心跳',
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
    pack_id: 'yokai_after_school',
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
const SECOND_CHOICE = { id: 'c2', action_id: 'observe', label: '观察', intent: 'observe' }

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

  it('boots with a stored pending-decision session and shows choices without a turn', async () => {
    saveSessionId('s1')
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') {
        // REAL backend shape at pending decision: segment_blocks is empty and
        // segment_choices is populated (DecisionPresented clears pending_scene).
        return jsonResponse(
          sessionBody({
            segment_blocks: [],
            segment_choices: [CHOICE, SECOND_CHOICE],
          }),
        )
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    render(<App />)
    expect(await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /B 观察/ })).toBeInTheDocument()
    const turnCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/turns'))
    expect(turnCalls).toHaveLength(0)
  })

  it('recovers a committed choice once after refresh without replaying provisional content', async () => {
    saveSessionId('s1')
    let projection: SessionProjection = sessionBody({
      segment_blocks: [],
      segment_choices: [CHOICE, SECOND_CHOICE],
    })
    const turnBodies: Array<{
      choice_id: string | null
      expected_revision: number
      idempotency_key: string
    }> = []

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') return jsonResponse(projection)
      if (url.endsWith('/turns') && method === 'POST') {
        const body = JSON.parse(init?.body as string) as (typeof turnBodies)[number]
        turnBodies.push(body)
        if (body.choice_id === CHOICE.id) {
          return sseResponse([
            'event: segment_started\ndata: {"segment_id":"failed-segment","expected_revision":1}',
            'event: block\ndata: {"segment_id":"failed-segment","index":0,"kind":"narration","text":"未提交的失败文本。"}',
            'event: error\ndata: {"code":"generation_failed"}',
          ])
        }
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"recovered-segment","expected_revision":2}',
          'event: block\ndata: {"segment_id":"recovered-segment","index":0,"kind":"narration","text":"恢复后的已提交结果。"}',
          'event: segment_ready\ndata: ' + JSON.stringify({
            segment_id: 'recovered-segment',
            revision: 3,
            terminal: 'decision',
            choices: [SECOND_CHOICE],
          }),
        ], 50)
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })

    const firstMount = render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /A 询问/ }, { timeout: 3000 }))

    expect(await screen.findByText('生成失败，请重试', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByText('未提交的失败文本。')).not.toBeInTheDocument()

    firstMount.unmount()
    projection = sessionBody({
      revision: 2,
      pending_consequence_status: 'awaiting_resolution',
      // Even if a stale client-facing choice list is present, recovery takes precedence.
      choices: [CHOICE],
      segment_choices: [CHOICE],
    })
    render(<App />)

    expect(await screen.findByText('正在生成…', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByText('未提交的失败文本。')).not.toBeInTheDocument()
    expect(screen.queryByText('恢复后的已提交结果。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /询问/ })).not.toBeInTheDocument()

    expect(await screen.findByText('恢复后的已提交结果。', {}, { timeout: 3000 })).toBeInTheDocument()
    const recoveryTurns = turnBodies.filter((body) => body.choice_id === null)
    expect(recoveryTurns).toHaveLength(1)
    expect(recoveryTurns[0].expected_revision).toBe(2)
    expect(typeof recoveryTurns[0].idempotency_key).toBe('string')
    expect(recoveryTurns[0].idempotency_key.length).toBeGreaterThan(0)
    expect(turnBodies.filter((body) => body.choice_id === CHOICE.id)).toHaveLength(1)
    expect(screen.queryByRole('button', { name: /A 询问/ })).not.toBeInTheDocument()
  })

  it('clears a stale stored session and returns to start on session_not_found', async () => {
    saveSessionId('s1')
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') {
        return jsonResponse({ detail: { code: 'session_not_found' } }, 404)
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    render(<App />)
    expect(await screen.findByRole('button', { name: '开始新游戏' }, { timeout: 3000 })).toBeInTheDocument()
    expect(localStorage.getItem('gal.session_id')).toBeNull()
  })

  it('shows the ending screen from a stored ended session with cleared=true', async () => {
    saveSessionId('s1')
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') {
        return jsonResponse(
          sessionBody({
            status: 'ended',
            segment_ending: {
              ending_id: 'truth',
              title: '真相大白',
              tone: '苦涩',
              terminal_state_summary: '两人各奔东西。',
            },
            cleared: true,
          }),
        )
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    render(<App />)
    expect(await screen.findByText('真相大白', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByText('已通过')).toBeInTheDocument()
  })

  it('shows the not-cleared badge from a stored ended session with cleared=false', async () => {
    saveSessionId('s1')
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') {
        return jsonResponse(
          sessionBody({
            status: 'ended',
            segment_ending: {
              ending_id: 'truth',
              title: '真相大白',
              tone: '苦涩',
              terminal_state_summary: '两人各奔东西。',
            },
            cleared: false,
          }),
        )
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    render(<App />)
    expect(await screen.findByText('真相大白', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByText('未通过')).toBeInTheDocument()
  })

  it('falls back to legacy ending fields when segment_ending is absent', async () => {
    saveSessionId('s1')
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') {
        return jsonResponse(
          sessionBody({
            status: 'ended',
            ending_id: 'legacy_end',
            ending_title: '旧日结局',
            segment_ending: null,
          }),
        )
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    render(<App />)
    expect(await screen.findByText('旧日结局', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByText('END')).toBeInTheDocument()
  })

  it('re-boots (re-fetches the pack) when retrying from the error screen', async () => {
    saveSessionId('s1')
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url.match(/\/sessions\/[^/]+$/) && method === 'GET') return jsonResponse(sessionBody())
      if (url.endsWith('/turns') && method === 'POST') {
        return sseResponse(['event: error\ndata: {"code":"generation_unavailable"}'])
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    render(<App />)
    const retry = await screen.findByRole('button', { name: '重试' }, { timeout: 3000 })
    const packCallsBefore = fetchMock.mock.calls.filter(([input]) => String(input).includes('/packs/')).length
    expect(packCallsBefore).toBe(1)
    fireEvent.click(retry)
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([input]) => String(input).includes('/packs/')).length,
      ).toBeGreaterThan(1)
    })
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

  it('shows an explicit error screen when the segment delivers no choices', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/packs/') && method === 'GET') return jsonResponse(PACK)
      if (url === '/api/v2/sessions' && method === 'POST') return jsonResponse(sessionBody({ revision: 0 }), 201)
      if (url.endsWith('/turns') && method === 'POST') {
        return sseResponse([
          'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":0}',
          'event: segment_ready\ndata: {"segment_id":"seg-1","revision":1,"terminal":"decision","choices":[]}',
        ])
      }
      return jsonResponse({ detail: { code: 'not_found' } }, 404)
    })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '开始新游戏' }))
    expect(await screen.findByText('未收到可选项，请重试', {}, { timeout: 3000 })).toBeInTheDocument()
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
