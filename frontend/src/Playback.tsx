import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import './Playback.css'
import type { NarrativeBlock, PackProjection, PresentedChoice } from './api'
import { newCommandId } from './api'
import { streamAdvance } from './stream'

const PLACEHOLDER_COLORS = ['#d96c5f', '#5f9bd9', '#d9b45f', '#7fbf7f', '#b08fd9', '#5fd0c4']
const TYPEWRITER_MS = 33 // ~30 chars/sec

function placeholderColor(characterId: string): string {
  let hash = 0
  for (const ch of characterId) {
    hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  }
  return PLACEHOLDER_COLORS[hash % PLACEHOLDER_COLORS.length]
}

function characterName(pack: PackProjection, characterId: string | null | undefined): string {
  if (!characterId) return ''
  return pack.characters.find((c) => c.character_id === characterId)?.name ?? characterId
}

interface PlaybackProps {
  pack: PackProjection
  sessionId: string
  expectedRevision: number
  onChoices: (choices: PresentedChoice[], revision: number) => void
  onEnding: (endingId: string, endingTitle: string, blocks: NarrativeBlock[], revision: number) => void
  onError: (message: string) => void
}

export default function Playback({
  pack,
  sessionId,
  expectedRevision,
  onChoices,
  onEnding,
  onError,
}: PlaybackProps) {
  const [archive, setArchive] = useState<NarrativeBlock[]>([])
  const [currentBlock, setCurrentBlock] = useState<NarrativeBlock | null>(null)
  const [typedText, setTypedText] = useState('')
  const [typing, setTyping] = useState(false)
  const [waiting, setWaiting] = useState(false)

  const queueRef = useRef<NarrativeBlock[]>([])
  const typingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isMountedRef = useRef(true)
  const logRef = useRef<HTMLDivElement>(null)

  // Refs to read current state inside async stream callback
  const currentBlockRef = useRef<NarrativeBlock | null>(null)
  const archiveRef = useRef<NarrativeBlock[]>([])
  useEffect(() => { currentBlockRef.current = currentBlock }, [currentBlock])
  useEffect(() => { archiveRef.current = archive }, [archive])

  const startTypewriter = useCallback((fullText: string) => {
    if (typingTimerRef.current) clearInterval(typingTimerRef.current)
    let i = 0
    typingTimerRef.current = setInterval(() => {
      i++
      if (!isMountedRef.current) {
        clearInterval(typingTimerRef.current!)
        return
      }
      setTypedText(fullText.slice(0, i))
      if (i >= fullText.length) {
        clearInterval(typingTimerRef.current!)
        typingTimerRef.current = null
        setTyping(false)
      }
    }, TYPEWRITER_MS)
  }, [])

  const dequeueNext = useCallback(() => {
    const next = queueRef.current.shift()
    if (next && isMountedRef.current) {
      setCurrentBlock(next)
      setTypedText('')
      setTyping(true)
      startTypewriter(next.text)
    } else if (!next && isMountedRef.current) {
      setWaiting(true)
    }
  }, [startTypewriter])

  // Start streaming on mount
  useEffect(() => {
    isMountedRef.current = true
    let cancelled = false

    async function startStream() {
      setWaiting(true)
      const key = newCommandId()
      let blockCount = 0
      let receivedChoices: PresentedChoice[] = []

      try {
        for await (const evt of streamAdvance(sessionId, expectedRevision, key)) {
          if (cancelled) return
          if (evt.event === 'block') {
            blockCount++
            queueRef.current.push(evt.data)
            setWaiting(false)
            // Auto-start first block
            if (blockCount === 1 && !currentBlockRef.current) {
              dequeueNext()
            }
          } else if (evt.event === 'choices') {
            receivedChoices = evt.data
          } else if (evt.event === 'done') {
            const done = evt.data
            if (done.ending_id) {
              onEnding(done.ending_id, done.ending_title ?? '', [...archiveRef.current], done.revision)
              return
            }
            onChoices(receivedChoices, done.revision)
          } else if (evt.event === 'error') {
            onError(errorMessageFor(evt.data.code))
            return
          }
        }
      } catch {
        if (!cancelled) onError('网络错误，请重试')
      }
    }

    void startStream()

    return () => {
      cancelled = true
      isMountedRef.current = false
      if (typingTimerRef.current) clearInterval(typingTimerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, expectedRevision])

  const handleClick = useCallback(() => {
    if (typing) {
      // Skip animation
      if (typingTimerRef.current) {
        clearInterval(typingTimerRef.current)
        typingTimerRef.current = null
      }
      if (currentBlock) setTypedText(currentBlock.text)
      setTyping(false)
      return
    }
    // Advance to next block
    if (currentBlock) {
      setArchive((prev) => [...prev, currentBlock])
    }
    setCurrentBlock(null)
    dequeueNext()
  }, [typing, currentBlock, dequeueNext])

  // Keyboard: Enter
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        handleClick()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleClick])

  // Auto-scroll to bottom
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [archive, typedText])

  return (
    <>
      <div
        className="playback-log"
        ref={logRef}
        onClick={handleClick}
        role="button"
        tabIndex={0}
        aria-label="对话框（点击继续）"
      >
        {archive.map((block, i) => (
          <BlockLine key={i} block={block} pack={pack} />
        ))}
        {currentBlock && (
          <BlockLine
            block={{ ...currentBlock, text: typedText }}
            pack={pack}
            typing={typing}
          />
        )}
        {waiting && <p className="waiting-hint">···</p>}
      </div>

      <div className="advance-hint" onClick={handleClick}>
        {typing ? '点击跳过' : waiting ? '' : '▼ 点击继续 / Enter'}
      </div>
    </>
  )
}

function errorMessageFor(code: string): string {
  switch (code) {
    case 'generation_unavailable':
      return '生成失败，请重试'
    case 'revision_conflict':
    case 'decision_required':
      return '状态已改变，正在同步'
    case 'session_ended':
      return '会话已结束'
    default:
      return '请求失败，请重试'
  }
}

interface BlockLineProps {
  block: NarrativeBlock
  pack: PackProjection
  typing?: boolean
}

function BlockLine({ block, pack, typing }: BlockLineProps) {
  if (block.kind === 'dialogue') {
    return (
      <div className="dialogue-entry">
        <span
          className="dialogue-speaker"
          style={{ '--speaker-color': placeholderColor(block.character_id ?? '') } as CSSProperties}
        >
          {characterName(pack, block.character_id)}
        </span>
        <p className="dialogue-text">
          {block.text}
          {typing && <span className="cursor">▌</span>}
        </p>
      </div>
    )
  }
  return (
    <p className="narration-text">
      {block.text}
      {typing && <span className="cursor">▌</span>}
    </p>
  )
}
