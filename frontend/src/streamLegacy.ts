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