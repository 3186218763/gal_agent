import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { streamAdvance } from './stream'

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

    let error: unknown = null
    try {
      for await (const _ of streamAdvance('s1', 0, 'k1')) {
        // should throw before yielding
      }
    } catch (e) {
      error = e
    }
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

  it('yields ending metadata in done event', async () => {
    fetchMock.mockResolvedValueOnce(
      makeSSEResponse([
        'event: block\ndata: {"kind":"narration","text":"The end."}',
        'event: done\ndata: {"session_id":"s1","revision":5,"ending_id":"truth","ending_title":"Truth"}',
      ]),
    )

    const results = []
    for await (const evt of streamAdvance('s1', 0, 'k1')) {
      results.push(evt)
    }

    expect(results).toHaveLength(2)
    const done = results[1]
    expect(done.event).toBe('done')
    if (done.event === 'done') {
      expect(done.data.ending_id).toBe('truth')
      expect(done.data.ending_title).toBe('Truth')
      expect(done.data.revision).toBe(5)
    }
  })
})
