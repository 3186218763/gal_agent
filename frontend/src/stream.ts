import { ApiError, advanceUrl } from './api'
import type { NarrativeBlock, PresentedChoice } from './api'

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

/**
 * POST to the advance endpoint and yield SSE events as they arrive.
 *
 * The response is a `text/event-stream` — each frame is separated by `\n\n`
 * and contains an `event:` line and a `data:` line.
 */
export async function* streamAdvance(
  sessionId: string,
  expectedRevision: number,
  idempotencyKey: string,
): AsyncGenerator<StreamEvent> {
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
