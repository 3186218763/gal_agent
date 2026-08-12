import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import '../demo/demo.css'
import './live.css'
import {
  createSession,
  fetchPack,
  newCommandId,
  newSessionSeed,
  type PackProjection,
  type SessionProjection,
} from '../api'
import { streamTurn } from '../stream'
import type { NarrativeBlock, PresentedChoice } from '../api'

// ── Constants ──

const TYPEWRITER_MS = 30
const STREAM_TIMEOUT_MS = 180_000

// ── Types ──

type Phase = 'title' | 'loading' | 'playing' | 'choice' | 'ending' | 'error'

interface CharacterInfo {
  id: string
  name: string
  color: string
}

// ── Character color palette (matches demo) ──

const CHAR_COLORS: Record<string, string> = {
  alice: '#F08A8A',
  bob: '#6BA3E8',
  mina: '#7FC9A0',
  protagonist: '#E8D55A',
}

// ── Main Component ──

export default function LiveApp() {
  const [phase, setPhase] = useState<Phase>('title')
  const [pack, setPack] = useState<PackProjection | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [blocks, setBlocks] = useState<NarrativeBlock[]>([])
  const [blockIndex, setBlockIndex] = useState(0)
  const [typedText, setTypedText] = useState('')
  const [typing, setTyping] = useState(false)
  const [choices, setChoices] = useState<PresentedChoice[]>([])
  const [ending, setEnding] = useState<{
    title: string
    tone: string
    summary: string
    cleared: boolean
  } | null>(null)
  const [errorMsg, setErrorMsg] = useState('')
  const [sceneLabel] = useState('cafe')

  const typewriterRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const phaseRef = useRef<Phase>('title')
  const advancingRef = useRef(false)

  // Keep phaseRef in sync for use in callbacks
  useEffect(() => { phaseRef.current = phase }, [phase])

  // ── Typewriter ──

  const stopTypewriter = useCallback(() => {
    if (typewriterRef.current) {
      clearInterval(typewriterRef.current)
      typewriterRef.current = null
    }
  }, [])

  const startTypewriter = useCallback(
    (fullText: string) => {
      stopTypewriter()
      setTyping(true)
      let i = 0
      typewriterRef.current = setInterval(() => {
        i++
        setTypedText(fullText.slice(0, i))
        if (i >= fullText.length) {
          stopTypewriter()
          setTyping(false)
        }
      }, TYPEWRITER_MS)
    },
    [stopTypewriter],
  )

  // ── Start playing blocks from index 0 ──

  const beginPlayback = useCallback(
    (newBlocks: NarrativeBlock[]) => {
      setBlocks(newBlocks)
      setBlockIndex(0)
      setTypedText('')
      setPhase('playing')
      if (newBlocks.length > 0) {
        startTypewriter(newBlocks[0].text)
      }
    },
    [startTypewriter],
  )

  // ── SSE stream consumer ──

  const startTurn = useCallback(
    async (sid: string, rev: number, choiceId: string | null) => {
      setPhase('loading')
      setErrorMsg('')

      const controller = new AbortController()
      abortRef.current = controller
      const timeout = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS)

      const collectedBlocks: NarrativeBlock[] = []

      try {
        for await (const evt of streamTurn(sid, choiceId, rev, newCommandId(), controller.signal)) {
          switch (evt.event) {
            case 'segment_started':
              // Director finished, Writer running
              break
            case 'heartbeat':
              break
            case 'block':
              collectedBlocks.push({
                kind: evt.data.kind,
                text: evt.data.text,
                character_id: evt.data.character_id ?? undefined,
              })
              break
            case 'segment_ready': {
              const data = evt.data
              setRevision(data.revision)

              if (data.terminal === 'decision' && data.choices) {
                // Store choices for after playback
                setChoices(data.choices)
              } else if (data.terminal === 'ending' && data.ending) {
                setEnding({
                  title: data.ending.title,
                  tone: data.ending.tone,
                  summary: data.ending.terminal_state_summary,
                  cleared: data.cleared ?? false,
                })
                setChoices([])
              }
              // Begin typewriter playback
              beginPlayback(collectedBlocks)
              break
            }
            case 'error':
              setErrorMsg(errorText(evt.data.code))
              setPhase('error')
              break
            case 'retry_after':
              setErrorMsg(`服务器忙碌，请在 ${evt.data.retry_after_seconds} 秒后重试`)
              setPhase('error')
              break
          }
        }
      } catch (err) {
        if (controller.signal.aborted) {
          setErrorMsg('请求超时，请重试')
        } else {
          setErrorMsg(`连接错误: ${err instanceof Error ? err.message : String(err)}`)
        }
        setPhase('error')
      } finally {
        clearTimeout(timeout)
        abortRef.current = null
        advancingRef.current = false
      }
    },
    [beginPlayback],
  )

  // ── Start game ──

  const startGame = useCallback(async () => {
    setPhase('loading')
    setErrorMsg('')
    try {
      const pk = await fetchPack('cafe_mystery')
      setPack(pk)
      const session: SessionProjection = await createSession('cafe_mystery', newSessionSeed())
      setSessionId(session.session_id)
      setRevision(session.revision)
      await startTurn(session.session_id, session.revision, null)
    } catch (err) {
      setErrorMsg(`初始化失败: ${err instanceof Error ? err.message : String(err)}`)
      setPhase('error')
    }
  }, [startTurn])

  // ── Advance within current blocks ──

  const advance = useCallback(() => {
    if (phase !== 'playing') return

    // Typing → complete current text
    if (typing) {
      stopTypewriter()
      setTypedText(blocks[blockIndex]?.text ?? '')
      setTyping(false)
      return
    }

    const nextIndex = blockIndex + 1
    if (nextIndex < blocks.length) {
      setBlockIndex(nextIndex)
      setTypedText('')
      startTypewriter(blocks[nextIndex].text)
      return
    }

    // All blocks played
    if (choices.length > 0) {
      setPhase('choice')
    } else if (ending) {
      setPhase('ending')
    }
    // If no choices and no ending, something is wrong; stay
  }, [phase, typing, blocks, blockIndex, choices, ending, stopTypewriter, startTypewriter])

  // ── Select a choice ──

  const handleChoice = useCallback(
    (choice: PresentedChoice) => {
      if (!sessionId || advancingRef.current) return
      advancingRef.current = true
      setChoices([])
      setEnding(null)
      startTurn(sessionId, revision, choice.id)
    },
    [sessionId, revision, startTurn],
  )

  // ── Retry after error ──

  const retry = useCallback(() => {
    if (sessionId) {
      startTurn(sessionId, revision, null)
    } else {
      startGame()
    }
  }, [sessionId, revision, startTurn, startGame])

  // ── Keyboard ──

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        if (phaseRef.current === 'playing') {
          e.preventDefault()
          advance()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [advance])

  // ── Cleanup ──

  useEffect(() => {
    return () => {
      stopTypewriter()
      abortRef.current?.abort()
    }
  }, [stopTypewriter])

  // ── Render ──

  const currentBlock = phase === 'playing' ? blocks[blockIndex] : null
  const activeSpeaker = currentBlock?.kind === 'dialogue' ? (currentBlock.character_id ?? null) : null

  // Determine present characters from current blocks
  const presentCharIds = new Set<string>()
  for (const b of blocks) {
    if (b.kind === 'dialogue' && b.character_id) {
      presentCharIds.add(b.character_id)
    }
  }

  const characters: CharacterInfo[] = (pack?.characters ?? [])
    .filter((c) => c.character_id !== 'protagonist')
    .map((c) => ({
      id: c.character_id,
      name: c.name,
      color: CHAR_COLORS[c.character_id] ?? '#94A3B8',
    }))

  // ── Title Screen ──

  if (phase === 'title') {
    return (
      <div className="demo-stage demo-bg-cafe">
        <div className="demo-title-overlay">
          <h1 className="demo-title">咖啡馆疑云</h1>
          <p className="demo-subtitle">AI 动态叙事 · Live Backend</p>
          <button className="demo-btn-primary" onClick={startGame}>
            开始游戏
          </button>
        </div>
      </div>
    )
  }

  // ── Loading Screen ──

  if (phase === 'loading') {
    return (
      <div className="demo-stage demo-bg-cafe">
        <div className="demo-buffering" style={{ bottom: '50%' }}>
          <span className="demo-buffering-dot" />
          <span className="demo-buffering-dot" />
          <span className="demo-buffering-dot" />
        </div>
        <p className="live-loading-text">AI 正在生成剧情…</p>
      </div>
    )
  }

  // ── Error Screen ──

  if (phase === 'error') {
    return (
      <div className="demo-stage demo-bg-cafe">
        <div className="demo-ending-overlay">
          <div className="demo-ending-eyebrow" style={{ color: '#d96c5f' }}>ERROR</div>
          <p className="demo-ending-tone">{errorMsg}</p>
          <button className="demo-btn-primary" onClick={retry}>
            重试
          </button>
          <button className="demo-menu-button" style={{ marginTop: 12 }} onClick={() => window.location.reload()}>
            回到标题
          </button>
        </div>
      </div>
    )
  }

  // ── Ending Screen ──

  if (phase === 'ending' && ending) {
    return (
      <div className="demo-stage demo-bg-ending">
        <div className="demo-ending-overlay">
          <div className="demo-ending-eyebrow">— END —</div>
          <h2 className="demo-ending-title">{ending.title}</h2>
          <p className="demo-ending-tone">{ending.tone}</p>
          <p className="demo-ending-summary">{ending.summary}</p>
          <div className={`demo-ending-status ${ending.cleared ? 'is-cleared' : 'is-failed'}`}>
            {ending.cleared ? '✦ 已通过' : '✦ 未通过'}
          </div>
          <button className="demo-btn-primary" onClick={() => window.location.reload()}>
            回到标题
          </button>
        </div>
      </div>
    )
  }

  // ── Main Play / Choice Screen ──

  return (
    <div className={`demo-stage demo-bg-${sceneLabel}`} onClick={advance}>
      {/* Character standees */}
      <div className="demo-standee-area">
        {characters
          .filter((c) => presentCharIds.has(c.id))
          .map((c) => (
            <CharacterStandee
              key={c.id}
              character={c}
              active={activeSpeaker === c.id}
            />
          ))}
      </div>

      {/* Choice panel */}
      {phase === 'choice' && choices.length > 0 && (
        <div className="demo-choice-layer" onClick={(e) => e.stopPropagation()}>
          <div className="demo-choice-panel">
            {choices.map((choice, i) => (
              <button
                key={choice.id}
                className="demo-choice-button"
                onClick={() => handleChoice(choice)}
              >
                <span className="demo-choice-letter">{String.fromCharCode(65 + i)}</span>
                <span className="demo-choice-label">{choice.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Dialogue box */}
      {phase === 'playing' && currentBlock && (
        <div className="demo-dialogue-box">
          {currentBlock.kind === 'dialogue' && currentBlock.character_id && (
            <div
              className="demo-speaker-name"
              style={{
                '--speaker-color': CHAR_COLORS[currentBlock.character_id] ?? '#fff',
              } as CSSProperties}
            >
              {pack?.characters.find((c) => c.character_id === currentBlock.character_id)?.name ?? '???'}
            </div>
          )}
          <p className={`demo-dialogue-text ${currentBlock.kind === 'narration' ? 'is-narration' : ''}`}>
            {typedText}
            {typing && <span className="demo-cursor">▌</span>}
          </p>
          {!typing && blockIndex < blocks.length - 1 && (
            <div className="demo-advance-hint">▼</div>
          )}
        </div>
      )}

      {/* Menu bar */}
      <div className="demo-menu-bar" onClick={(e) => e.stopPropagation()}>
        <span className="live-scene-info">
          Rev.{revision} · {blocks.length} blocks
        </span>
      </div>
    </div>
  )
}

// ── Sub-components ──

function CharacterStandee({
  character,
  active,
}: {
  character: CharacterInfo
  active: boolean
}) {
  return (
    <div className={`demo-standee ${active ? 'is-active' : 'is-dim'}`}>
      <div
        className="demo-standee-body"
        style={{ '--char-color': character.color } as CSSProperties}
      >
        <div className="demo-standee-head" />
        <div className="demo-standee-torso" />
      </div>
      {active && (
        <div className="demo-standee-name" style={{ color: character.color }}>
          {character.name}
        </div>
      )}
    </div>
  )
}

// ── Helpers ──

function errorText(code: string): string {
  const messages: Record<string, string> = {
    generation_unavailable: 'AI 模型生成失败，请重试。',
    revision_conflict: '会话状态已变更，请重试。',
    session_ended: '会话已结束。',
    pack_mismatch: '剧本包不匹配。',
    internal_error: '服务器内部错误，请重试。',
  }
  return messages[code] ?? `未知错误: ${code}`
}
