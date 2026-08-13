import { ApiError, turnSessionUrl } from './api'
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
  data: { retry_after_seconds: number; message: string }
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
 * @param signal        - Optional AbortSignal; aborting rejects with ApiError('network', 0)
 */
export async function* streamTurn(
  sessionId: string,
  choiceId: string | null,
  expectedRevision: number,
  idempotencyKey: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  let response: Response
  try {
    response = await fetch(turnSessionUrl(sessionId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_revision: expectedRevision,
        idempotency_key: idempotencyKey,
        choice_id: choiceId,
      }),
      signal,
    })
  } catch (err) {
    if (signal?.aborted) throw new ApiError('network', 0)
    throw err
  }

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
  const dataLines: string[] = []

  for (const line of chunk.split('\n')) {
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  const dataStr = dataLines.join('\n')
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
