import { useState } from 'react'
import './App.css'
import { api } from './api'
import Game from './components/Game'

function App() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleStartGame = async () => {
    setIsCreating(true)
    setError(null)

    try {
      const response = await api.createSession('chapter_01')
      setSessionId(response.session_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建游戏会话失败')
      console.error('Failed to create session:', err)
    } finally {
      setIsCreating(false)
    }
  }

  const handleEndGame = () => {
    if (sessionId) {
      api.deleteSession(sessionId).catch(console.error)
      setSessionId(null)
    }
  }

  if (sessionId) {
    return <Game sessionId={sessionId} onEnd={handleEndGame} />
  }

  return (
    <div className="app">
      <div className="title-screen">
        <h1 className="game-title">Galgame AI</h1>
        <p className="game-subtitle">AI 驱动的动态视觉小说</p>

        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        <button
          className="start-button"
          onClick={handleStartGame}
          disabled={isCreating}
        >
          {isCreating ? '正在创建...' : '开始游戏'}
        </button>

        <div className="info">
          <p>本游戏使用 AI 实时生成剧情和角色对话</p>
          <p>每次游玩都将是独特的体验</p>
        </div>
      </div>
    </div>
  )
}

export default App
