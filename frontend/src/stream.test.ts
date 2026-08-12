import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { streamTurn } from './stream'
import type { StreamEvent } from './stream'

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

  it('throws ApiError network when the signal is already aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    fetchMock.mockImplementation(async (_input, init) => {
      if (init?.signal?.aborted) {
        throw new DOMException('The operation was aborted.', 'AbortError')
      }
      throw new Error('fetch should not proceed with an aborted signal')
    })

    const events: unknown[] = []
    let error: unknown = null
    try {
      for await (const evt of streamTurn('s1', null, 0, 'key-ab', controller.signal)) {
        events.push(evt)
      }
    } catch (e) {
      error = e
    }

    expect(events).toHaveLength(0)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('network')
    expect((error as ApiError).status).toBe(0)
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal)
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

  it('yields retry_after with backend payload', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: retry_after\ndata: {"retry_after_seconds":5,"message":"Command is already being processed"}',
      ]),
    )

    const results: StreamEvent[] = []
    for await (const evt of streamTurn('s1', null, 0, 'key-r')) {
      results.push(evt)
    }

    expect(results).toHaveLength(1)
    const retry = results[0]
    expect(retry.event).toBe('retry_after')
    if (retry.event === 'retry_after') {
      expect(retry.data.retry_after_seconds).toBe(5)
      expect(retry.data.message).toBe('Command is already being processed')
    }
  })

  it('parses multi-line data joined with newline', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: segment_started\ndata: {"segment_id":"seg-ml",\ndata: "expected_revision":7}',
      ]),
    )

    const events: StreamEvent[] = []
    for await (const evt of streamTurn('s1', null, 0, 'key-ml')) {
      events.push(evt)
    }

    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('segment_started')
    if (events[0].event === 'segment_started') {
      expect(events[0].data.segment_id).toBe('seg-ml')
      expect(events[0].data.expected_revision).toBe(7)
    }
  })

  it('parses event and data prefixes without a space', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event:segment_started\ndata:{"segment_id":"seg-ns","expected_revision":3}',
      ]),
    )

    const events: StreamEvent[] = []
    for await (const evt of streamTurn('s1', null, 0, 'key-ns')) {
      events.push(evt)
    }

    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('segment_started')
    if (events[0].event === 'segment_started') {
      expect(events[0].data.segment_id).toBe('seg-ns')
    }
  })

  it('ignores unknown event types', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":0}',
        'event: unknown_type\ndata: {"foo":1}',
        'event: segment_ready\ndata: {"segment_id":"seg-1","revision":1,"terminal":"decision","choices":[]}',
      ]),
    )

    const events: string[] = []
    for await (const evt of streamTurn('s1', null, 0, 'key-u')) {
      events.push(evt.event)
    }

    expect(events).toEqual(['segment_started', 'segment_ready'])
  })

  it('ignores frames with malformed JSON', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: segment_started\ndata: {"segment_id":"seg-1","expected_revision":0}',
        'event: block\ndata: {not-json',
        'event: segment_ready\ndata: {"segment_id":"seg-1","revision":1,"terminal":"decision","choices":[]}',
      ]),
    )

    const events: string[] = []
    for await (const evt of streamTurn('s1', null, 0, 'key-j')) {
      events.push(evt.event)
    }

    expect(events).toEqual(['segment_started', 'segment_ready'])
  })

  it('reassembles a frame split across reader chunks', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: segment_started\ndata: {"segment_id":"seg-sp'))
        controller.enqueue(encoder.encode('lit","expected_revision":4}\n\n'))
        controller.close()
      },
    })
    fetchMock.mockResolvedValueOnce(
      new Response(stream, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )

    const events: StreamEvent[] = []
    for await (const evt of streamTurn('s1', null, 0, 'key-sp')) {
      events.push(evt)
    }

    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('segment_started')
    if (events[0].event === 'segment_started') {
      expect(events[0].data.segment_id).toBe('seg-split')
    }
  })

  it('flushes a trailing frame without final newline delimiter', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode('event: segment_started\ndata: {"segment_id":"seg-f","expected_revision":0}\n\n'),
        )
        controller.enqueue(encoder.encode('event: heartbeat\ndata: {}'))
        controller.close()
      },
    })
    fetchMock.mockResolvedValueOnce(
      new Response(stream, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )

    const events: string[] = []
    for await (const evt of streamTurn('s1', null, 0, 'key-f')) {
      events.push(evt.event)
    }

    expect(events).toEqual(['segment_started', 'heartbeat'])
  })
})
