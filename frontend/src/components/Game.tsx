import { useState, useEffect, useRef, useCallback } from 'react'
import type {
  GameMessage,
  NarrationMessage,
  DialogueMessage,
  OptionsMessage,
  Option,
  EndingMessage
} from '../types'
import { useWebSocketGame } from '../hooks/useWebSocketGame'
import './Game.css'

interface GameProps {
  sessionId: string
  onEnd: () => void
}

interface DisplayMessage {
  id: string
  type: 'narration' | 'dialogue' | 'options' | 'ending'
  content: string
  character?: string
  mood?: string
  options?: Option[]
  endingData?: {
    title: string
    ending_type: string
  }
}

export default function Game({ sessionId, onEnd }: GameProps) {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [currentOptions, setCurrentOptions] = useState<Option[] | null>(null)
  const [isEnded, setIsEnded] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const handleGameMessage = useCallback((message: GameMessage) => {
    console.log('Received message:', message)

    switch (message.type) {
      case 'game_start': {
        const msg = message as any
        setMessages([{
          id: `start-${Date.now()}`,
          type: 'narration',
          content: `第${msg.chapter}章开始`,
          mood: '开场'
        }])
        break
      }

      case 'narration': {
        const msg = message as NarrationMessage
        setMessages(prev => [...prev, {
          id: `narration-${Date.now()}`,
          type: 'narration',
          content: msg.content,
          mood: msg.mood
        }])
        break
      }

      case 'dialogue': {
        const msg = message as DialogueMessage
        setMessages(prev => [...prev, {
          id: `dialogue-${Date.now()}`,
          type: 'dialogue',
          content: msg.content,
          character: msg.character,
          mood: msg.mood
        }])
        break
      }

      case 'options': {
        const msg = message as OptionsMessage
        setCurrentOptions(msg.options)
        setMessages(prev => [...prev, {
          id: `options-${Date.now()}`,
          type: 'options',
          content: '',
          options: msg.options
        }])
        break
      }

      case 'ending': {
        const msg = message as EndingMessage
        setMessages(prev => [...prev, {
          id: `ending-${Date.now()}`,
          type: 'ending',
          content: msg.content,
          endingData: {
            title: msg.title,
            ending_type: msg.ending_type
          }
        }])
        setIsEnded(true)
        break
      }

      case 'error': {
        setError(message.message || '发生错误')
        break
      }
    }
  }, [])

  // Use WebSocket hook
  const { isConnected, sendChoice } = useWebSocketGame({
    sessionId,
    onMessage: handleGameMessage,
    onError: setError,
    onConnectionChange: (connected) => {
      console.log('Connection status:', connected)
    },
  })

  const handleChoice = (optionIndex: number) => {
    if (!currentOptions) return
    sendChoice(optionIndex)
    setCurrentOptions(null)
  }

  return (
    <div className="game-container">
      <div className="game-header">
        <div className="status">
          <span className={`status-dot ${isConnected ? 'connected' : 'disconnected'}`} />
          <span className="status-text">
            {isConnected ? '已连接' : '未连接'}
          </span>
        </div>
        <button className="end-button" onClick={onEnd}>
          结束游戏
        </button>
      </div>

      {error && (
        <div className="game-error">
          {error}
        </div>
      )}

      <div className="messages-container">
        {messages.map((msg) => (
          <div key={msg.id} className={`message message-${msg.type}`}>
            {msg.type === 'narration' && (
              <div className="narration">
                {msg.mood && <span className="mood-tag">{msg.mood}</span>}
                <p className="content">{msg.content}</p>
              </div>
            )}

            {msg.type === 'dialogue' && (
              <div className="dialogue">
                <div className="character-name">{msg.character}</div>
                {msg.mood && <span className="mood-tag">{msg.mood}</span>}
                <p className="content">{msg.content}</p>
              </div>
            )}

            {msg.type === 'options' && msg.options && (
              <div className="options">
                <p className="options-prompt">请选择：</p>
                <div className="options-list">
                  {msg.options.map((option, index) => (
                    <button
                      key={option.id}
                      className="option-button"
                      onClick={() => handleChoice(index)}
                      disabled={!currentOptions}
                    >
                      <span className="option-text">{option.text}</span>
                      {option.preview && (
                        <span className="option-preview">{option.preview}</span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {msg.type === 'ending' && msg.endingData && (
              <div className="ending">
                <div className="ending-badge">{msg.endingData.ending_type}</div>
                <h2 className="ending-title">{msg.endingData.title}</h2>
                <p className="ending-content">{msg.content}</p>
              </div>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {isEnded && (
        <div className="game-footer">
          <button className="restart-button" onClick={onEnd}>
            返回主菜单
          </button>
        </div>
      )}
    </div>
  )
}
