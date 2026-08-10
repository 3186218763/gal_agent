const API_BASE = 'http://localhost:8000';

export interface CreateSessionResponse {
  session_id: string;
  pack_id: string;
  /** Backend echoes pack_id here for older clients. */
  chapter_id?: string;
}

export const api = {
  /**
   * Create a game session.
   * Prefers `pack_id`; still accepts a string default of `chapter_01`.
   * Body also includes `chapter_id` for backward-compatible backends.
   */
  async createSession(packId: string = 'chapter_01'): Promise<CreateSessionResponse> {
    const response = await fetch(`${API_BASE}/api/sessions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ pack_id: packId, chapter_id: packId }),
    });

    if (!response.ok) {
      throw new Error(`Failed to create session: ${response.statusText}`);
    }

    return response.json();
  },

  async getSession(sessionId: string) {
    const response = await fetch(`${API_BASE}/api/sessions/${sessionId}`);

    if (!response.ok) {
      throw new Error(`Failed to get session: ${response.statusText}`);
    }

    return response.json();
  },

  async deleteSession(sessionId: string) {
    const response = await fetch(`${API_BASE}/api/sessions/${sessionId}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      throw new Error(`Failed to delete session: ${response.statusText}`);
    }

    return response.json();
  },

  async listSessions() {
    const response = await fetch(`${API_BASE}/api/sessions`);

    if (!response.ok) {
      throw new Error(`Failed to list sessions: ${response.statusText}`);
    }

    return response.json();
  },
};
