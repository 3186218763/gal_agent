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