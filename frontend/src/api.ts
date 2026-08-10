const API_BASE = 'http://localhost:8000';

export interface CreateSessionResponse {
  session_id: string;
  chapter_id: string;
}

export const api = {
  async createSession(chapterId: string = 'chapter_01'): Promise<CreateSessionResponse> {
    const response = await fetch(`${API_BASE}/api/sessions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ chapter_id: chapterId }),
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
