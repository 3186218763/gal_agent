import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import './Playback.css'
import type { NarrativeBlock, PackProjection, PresentedChoice } from './api'
import { newCommandId } from './api'
import { streamTurn } from './stream'
import { SegmentPlayer, type EndingMeta, type SegmentPlayerState } from './segmentPlayer'

const PLACEHOLDER_COLORS = ['#d96c5f', '#5f9bd9', '#d9b45f', '#7fbf7f', '#b08fd9', '#5fd0c4']
const TYPEWRITER_MS = 33

function placeholderColor(characterId: string): string {
  let hash = 0
  for (const ch of characterId) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return PLACEHOLDER_COLORS[hash % PLACEHOLDER_COLORS.length]
}

function characterName(pack: PackProjection, characterId: string | null | undefined): string {
  if (!characterId) return ''
  return pack.characters.find((c) => c.character_id === characterId)?.name ?? characterId
}

export interface PlaybackProps {
  pack: PackProjection
  sessionId: string
  expectedRevision: number
  choiceId: string | null
  /** Pre-loaded blocks for replay (no network turn). If provided, no stream is started. */
  replayBlocks?: NarrativeBlock[]
  replayRevision?: number
  replayChoices?: PresentedChoice[]
  replayEnding?: EndingMeta | null
  onChoices: (choices: PresentedChoice[], revision: number) => void
  onEnding: (ending: EndingMeta, blocks: NarrativeBlock[], revision: number, cleared?: boolean | null) => void
  onError: (message: string) => void
}

export default function Playback({
  pack,
  sessionId,
  expectedRevision,
  choiceId,
  replayBlocks,
  replayRevision,
  replayChoices,
  replayEnding,
  onChoices,
  onEnding,
  onError,
}: PlaybackProps) {
  const [archive, setArchive] = useState<NarrativeBlock[]>([])
  const [currentBlock, setCurrentBlock] = useState<NarrativeBlock | null>(null)
  const [typedText, setTypedText] = useState('')
  const [typing, setTyping] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [isBuffering, setIsBuffering] = useState(!replayBlocks)

  // Lazy-init: SegmentPlayer is a class instance, so creating it in the
  // useRef initializer would allocate a new player on every render.
  const playerRef = useRef<SegmentPlayer | null>(null)
  if (playerRef.current === null) {
    playerRef.current = new SegmentPlayer()
  }
  const keyRef = useRef<string | null>(null)
  if (keyRef.current === null) {
    keyRef.current = newCommandId()
  }
  const typingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isMountedRef = useRef(true)
  const logRef = useRef<HTMLDivElement>(null)
  const drainedNotifiedRef = useRef(false)

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
    const player = playerRef.current!
    const next = player.dequeueBlock()
    if (next && isMountedRef.current) {
      setCurrentBlock(next)
      setTypedText('')
      setTyping(true)
      startTypewriter(next.text)
    } else if (!next && isMountedRef.current) {
      // Queue is empty — check if we should notify drain
      setWaiting(true)
      // Narrow a local instead of player.state: TS keeps property narrowing
      // across the onDrained() call, which would make the post-drain checks
      // appear impossible.
      const stateBeforeDrain = player.state
      if (!drainedNotifiedRef.current && stateBeforeDrain === 'playing') {
        drainedNotifiedRef.current = true
        player.onDrained()
        const rev = player.committedRevision ?? expectedRevision
        const stateAfterDrain: SegmentPlayerState = player.state
        if (stateAfterDrain === 'waiting_choice' && player.choices) {
          onChoices(player.choices, rev)
        } else if (stateAfterDrain === 'playing_ending' && player.ending) {
          onEnding(player.ending, [...archiveRef.current], rev, player.cleared ?? null)
        }
      }
    }
  }, [startTypewriter, onChoices, onEnding, expectedRevision])

  // Start streaming or replay on mount
  useEffect(() => {
    isMountedRef.current = true
    drainedNotifiedRef.current = false
    let cancelled = false
    const controller = new AbortController()
    const player = playerRef.current!
    const key = keyRef.current!

    async function startStream() {
      setWaiting(true)
      setIsBuffering(true)
      player.start()

      try {
        for await (const evt of streamTurn(sessionId, choiceId, expectedRevision, key, controller.signal)) {
          if (cancelled) return

          if (evt.event === 'segment_started') {
            player.onSegmentStarted(evt.data.segment_id, evt.data.expected_revision)
          } else if (evt.event === 'block') {
            player.onBlock(evt.data)
          } else if (evt.event === 'segment_ready') {
            player.onSegmentReady(evt.data)
            // Blocks are now unlocked in SegmentPlayer; start dequeuing
            setIsBuffering(false)
            setWaiting(false)
            // Auto-start first block
            const playable = player.playableBlocks
            if (playable.length > 0 && !currentBlockRef.current) {
              dequeueNext()
            } else if (playable.length === 0) {
              // Empty segment — handle immediately
              const rev = player.committedRevision ?? expectedRevision
              if (player.state === 'waiting_choice' && player.choices) {
                onChoices(player.choices, rev)
              } else if (player.state === 'playing_ending' && player.ending) {
                onEnding(player.ending, [], rev, player.cleared ?? null)
              }
            }
          } else if (evt.event === 'heartbeat') {
            // Keep-alive, no state change
          } else if (evt.event === 'retry_after') {
            // Command lease still active — show fixed retry text, keep old revision
            onError('正在处理中，请稍后重试')
            return
          } else if (evt.event === 'error') {
            onError(errorMessageFor(evt.data.code))
            return
          }
        }
        // Transport EOF — do NOT change visual state.
        // If player is still buffering, this is a disconnect before segment_ready.
        if (player.state === 'buffering_segment' || player.state === 'generating_after_choice') {
          if (!cancelled) onError('连接中断，请重试')
        }
      } catch {
        if (!cancelled) {
          // Network error — if segment not ready, old revision is intact
          onError('网络错误，请重试')
        }
      }
    }

    function startReplay() {
      setIsBuffering(false)
      const blocks = replayBlocks ?? []
      // Pass choices/ending through loadFromProjection so onDrained() can access them
      player.loadFromProjection(blocks, replayRevision ?? expectedRevision, replayChoices, replayEnding)
      setWaiting(blocks.length === 0)
      // Dequeue unconditionally: with zero blocks the drain-notify path fires
      // onChoices/onEnding immediately (pending-decision projections).
      dequeueNext()
    }

    if (replayBlocks !== undefined) {
      startReplay()
    } else {
      void startStream()
    }

    return () => {
      cancelled = true
      isMountedRef.current = false
      controller.abort()
      if (typingTimerRef.current) clearInterval(typingTimerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, expectedRevision, choiceId])

  const handleClick = useCallback(() => {
    if (typing) {
      if (typingTimerRef.current) {
        clearInterval(typingTimerRef.current)
        typingTimerRef.current = null
      }
      if (currentBlock) setTypedText(currentBlock.text)
      setTyping(false)
      return
    }
    if (currentBlock) {
      // Update ref synchronously so the drain callback sees the final block.
      const nextArchive = [...archiveRef.current, currentBlock]
      archiveRef.current = nextArchive
      setArchive(nextArchive)
    }
    setCurrentBlock(null)
    dequeueNext()
  }, [typing, currentBlock, dequeueNext])

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

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [archive, typedText])

  return (
    <>
      {isBuffering && (
        <div className="buffering-overlay" role="status">
          <p className="busy-hint">正在生成…</p>
        </div>
      )}
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
        {waiting && !isBuffering && <p className="waiting-hint">···</p>}
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
      return '状态已改变，正在同步'
    case 'decision_required':
      return '状态已改变，正在同步'
    case 'session_ended':
      return '会话已结束'
    case 'generation_failed':
      return '生成失败，请重试'
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
