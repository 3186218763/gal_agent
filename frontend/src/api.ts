export interface NarrativeBlock {
  kind: 'narration' | 'dialogue'
  text: string
  character_id?: string | null
}

export interface PresentedChoice {
  id: string
  action_id: string
  label: string
  intent: string
  target_character_id?: string | null
  preview?: string | null
}

export interface SessionProjection {
  session_id: string
  pack_id: string
  revision: number
  status: 'active' | 'resolving' | 'ended'
  phase: string
  scene_count: number
  pending_decision_id: string | null
  scene_id: string | null
  blocks: NarrativeBlock[]
  choices: PresentedChoice[]
  ending_id: string | null
  ending_title: string | null
  location_id: string
  time_label: string
  present_character_ids: string[]
}

export interface PackCharacterProjection {
  character_id: string
  name: string
  public_profile: string
}

export interface PackLocationProjection {
  location_id: string
  name: string
}

export interface PackProjection {
  pack_id: string
  title: string
  language: string
  characters: PackCharacterProjection[]
  locations: PackLocationProjection[]
}

export interface ActionResult {
  session_id: string
  revision: number
  action_id: string
  outcome: string
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(code: string, status: number) {
    super(code)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

export const DEFAULT_PACK_ID = 'cafe_mystery'

const rawApiBase = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''
const API_BASE = rawApiBase.replace(/\/+$/, '')

const JSON_HEADERS = { 'Content-Type': 'application/json' }

function apiPath(path: string): string {
  return `${API_BASE}${path}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiPath(path), init)
  if (!response.ok) {
    throw await toApiError(response)
  }
  return (await response.json()) as T
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = `http_error_${response.status}`
  try {
    const body = (await response.json()) as { detail?: { code?: string } }
    if (body.detail?.code) {
      code = body.detail.code
    }
  } catch {
    // non-JSON error body; keep the generic code
  }
  return new ApiError(code, response.status)
}

export function createSession(packId: string, sessionSeed: number): Promise<SessionProjection> {
  return request<SessionProjection>('/api/v2/sessions', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ pack_id: packId, session_seed: sessionSeed }),
  })
}

export function fetchSession(sessionId: string): Promise<SessionProjection> {
  return request<SessionProjection>(`/api/v2/sessions/${sessionId}`, { method: 'GET' })
}

export function fetchPack(packId: string): Promise<PackProjection> {
  return request<PackProjection>(`/api/v2/packs/${packId}`, { method: 'GET' })
}

export function choose(
  sessionId: string,
  choiceId: string,
  expectedRevision: number,
  idempotencyKey: string,
): Promise<ActionResult> {
  return request<ActionResult>(`/api/v2/sessions/${sessionId}/choices/${choiceId}`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ expected_revision: expectedRevision, idempotency_key: idempotencyKey }),
  })
}

export function newCommandId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `cmd-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function newSessionSeed(): number {
  return Math.floor(Math.random() * 2 ** 31)
}

/** Build the full URL for the SSE advance endpoint (used by stream.ts). */
export function advanceUrl(sessionId: string): string {
  return apiPath(`/api/v2/sessions/${sessionId}/advance`)
}
