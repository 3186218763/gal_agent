const SESSION_ID_KEY = 'gal.session_id'

export function loadSessionId(): string | null {
  return localStorage.getItem(SESSION_ID_KEY)
}

export function saveSessionId(sessionId: string): void {
  localStorage.setItem(SESSION_ID_KEY, sessionId)
}

export function clearSessionId(): void {
  localStorage.removeItem(SESSION_ID_KEY)
}
