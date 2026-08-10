import { useState, useEffect, useRef, useCallback } from 'react'
import type { GameMessage, PlayerChoiceMessage } from '../types'

interface UseWebSocketGameOptions {
  sessionId: string
  onMessage?: (message: GameMessage) => void
  onError?: (error: string) => void
  onConnectionChange?: (connected: boolean) => void
}

export function useWebSocketGame({
  sessionId,
  onMessage,
  onError,
  onConnectionChange,
}: UseWebSocketGameOptions) {
  const [isConnected, setIsConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const ws = new WebSocket(`ws://localhost:8000/ws/game/${sessionId}`)
    wsRef.current = ws

    ws.onopen = () => {
      console.log('WebSocket connected')
      setIsConnected(true)
      onConnectionChange?.(true)
    }

    ws.onmessage = (event) => {
      try {
        const message: GameMessage = JSON.parse(event.data)
        onMessage?.(message)
      } catch (err) {
        console.error('Failed to parse message:', err)
        onError?.('消息解析失败')
      }
    }

    ws.onerror = (error) => {
      console.error('WebSocket error:', error)
      onError?.('连接错误')
    }

    ws.onclose = () => {
      console.log('WebSocket disconnected')
      setIsConnected(false)
      onConnectionChange?.(false)
    }

    return () => {
      ws.close()
    }
  }, [sessionId, onMessage, onError, onConnectionChange])

  const sendChoice = useCallback((optionIndex: number) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.error('WebSocket not connected')
      return
    }

    const message: PlayerChoiceMessage = {
      type: 'player_choice',
      option_index: optionIndex,
    }

    wsRef.current.send(JSON.stringify(message))
  }, [])

  return {
    isConnected,
    sendChoice,
  }
}
