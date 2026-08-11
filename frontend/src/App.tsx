import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import {
  ApiError,
  DEFAULT_PACK_ID,
  type NarrativeBlock,
  type PackProjection,
  type PresentedChoice,
  createSession,
  fetchPack,
  fetchSession,
  newCommandId,
  newSessionSeed,
} from './api'
import { clearSessionId, loadSessionId, saveSessionId } from './storage'
import Playback from './Playback'

const CHOICE_LETTERS = ['A', 'B', 'C', 'D']

type Screen =
  | { kind: 'booting' }
  | { kind: 'boot-error'; message: string }
  | { kind: 'start'; pack: PackProjection }
  | { kind: 'play'; pack: PackProjection; sessionId: string; revision: number }
  | { kind: 'choices'; pack: PackProjection; sessionId: string; revision: number; choices: PresentedChoice[] }
  | { kind: 'ending'; pack: PackProjection; sessionId: string; blocks: NarrativeBlock[]; endingId: string; endingTitle: string }
  | { kind: 'error'; pack: PackProjection; sessionId: string; revision: number; message: string }

function characterName(pack: PackProjection, characterId: string | null | undefined): string {
  if (!characterId) return ''
  return pack.characters.find((c) => c.character_id === characterId)?.name ?? characterId
}

export default function App() {
  const [screen, setScreen] = useState<Screen>({ kind: 'booting' })
  const packRef = useRef<PackProjection | null>(null)
  const startingRef = useRef(false)
  const bootingRef = useRef(false)

  const boot = useCallback(async () => {
    if (bootingRef.current) return
    bootingRef.current = true
    try {
      const pack = await fetchPack(DEFAULT_PACK_ID)
      packRef.current = pack
      const storedId = loadSessionId()
      if (!storedId) {
        setScreen({ kind: 'start', pack })
        return
      }
      try {
        const session = await fetchSession(storedId)
        if (session.status === 'ended') {
          setScreen({
            kind: 'ending',
            pack,
            sessionId: storedId,
            blocks: session.blocks,
            endingId: session.ending_id ?? '',
            endingTitle: session.ending_title ?? '',
          })
        } else if (session.choices.length > 0) {
          setScreen({
            kind: 'choices',
            pack,
            sessionId: storedId,
            revision: session.revision,
            choices: session.choices,
          })
        } else {
          setScreen({ kind: 'play', pack, sessionId: storedId, revision: session.revision })
        }
      } catch (reason) {
        if (reason instanceof ApiError && reason.code === 'session_not_found') {
          clearSessionId()
          setScreen({ kind: 'start', pack })
        } else {
          setScreen({ kind: 'boot-error', message: '无法读取存档，请重试' })
        }
      }
    } catch {
      setScreen({ kind: 'boot-error', message: '无法获取剧本信息，请重试' })
    } finally {
      bootingRef.current = false
    }
  }, [])

  useEffect(() => {
    void boot()
  }, [boot])

  const startNewGame = useCallback(async () => {
    const pack = packRef.current
    if (!pack || startingRef.current) return
    startingRef.current = true
    try {
      const session = await createSession(DEFAULT_PACK_ID, newSessionSeed())
      saveSessionId(session.session_id)
      setScreen({ kind: 'play', pack, sessionId: session.session_id, revision: session.revision })
    } catch {
      setScreen({ kind: 'boot-error', message: '创建会话失败，请重试' })
    } finally {
      startingRef.current = false
    }
  }, [])

  const handleChoices = useCallback(
    (choices: PresentedChoice[], revision: number) => {
      const pack = packRef.current
      if (!pack) return
      const sessionId = (screen.kind === 'play' || screen.kind === 'choices') ? screen.sessionId : ''
      if (choices.length > 0) {
        setScreen({ kind: 'choices', pack, sessionId, revision, choices })
      } else {
        // Continue scene — stream next advance
        setScreen({ kind: 'play', pack, sessionId, revision })
      }
    },
    [screen],
  )

  const handleChoice = useCallback(
    async (sessionId: string, choiceId: string, revision: number) => {
      const pack = packRef.current
      if (!pack) return
      try {
        const response = await fetch(`/api/v2/sessions/${sessionId}/choices/${choiceId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_revision: revision,
            idempotency_key: newCommandId(),
          }),
        })
        if (!response.ok) throw new ApiError('choice_failed', response.status)
        const result = await response.json()
        setScreen({
          kind: 'play',
          pack,
          sessionId,
          revision: result.revision,
        })
      } catch {
        setScreen({
          kind: 'error',
          pack,
          sessionId,
          revision,
          message: '选择失败，请重试',
        })
      }
    },
    [],
  )

  const handleEnding = useCallback(
    (endingId: string, endingTitle: string, blocks: NarrativeBlock[]) => {
      const pack = packRef.current
      if (!pack) return
      const sessionId = screen.kind === 'play' ? screen.sessionId : ''
      setScreen({ kind: 'ending', pack, sessionId, blocks, endingId, endingTitle })
    },
    [screen],
  )

  if (screen.kind === 'booting') {
    return (
      <main className="gal-app boot-screen">
        <p className="busy-hint" role="status">正在连接…</p>
      </main>
    )
  }

  if (screen.kind === 'boot-error') {
    return (
      <main className="gal-app boot-screen">
        <p className="error-message">{screen.message}</p>
        <button className="secondary-button" onClick={() => void boot()}>重试</button>
        <button
          className="secondary-button"
          onClick={() => {
            clearSessionId()
            const pack = packRef.current
            if (pack) setScreen({ kind: 'start', pack })
          }}
        >
          开始新游戏
        </button>
      </main>
    )
  }

  if (screen.kind === 'start') {
    return (
      <main className="gal-app start-screen">
        <h1 className="start-title">{screen.pack.title}</h1>
        <p className="start-subtitle">一段由 AI 驱动的故事，等待你的选择。</p>
        <button className="primary-button" onClick={() => void startNewGame()}>
          开始新游戏
        </button>
      </main>
    )
  }

  const { pack } = screen

  if (screen.kind === 'play') {
    return (
      <main className="gal-app">
        <header className="scene-header">
          <span className="scene-location">{pack.title}</span>
        </header>
        <Playback
          key={`${screen.sessionId}-${screen.revision}`}
          pack={pack}
          sessionId={screen.sessionId}
          expectedRevision={screen.revision}
          onChoices={(choices, rev) => handleChoices(choices, rev)}
          onEnding={(eid, etitle, blocks, _rev) =>
            handleEnding(eid, etitle, blocks)
          }
          onError={(msg) =>
            setScreen({
              kind: 'error',
              pack,
              sessionId: screen.sessionId,
              revision: screen.revision,
              message: msg,
            })
          }
        />
      </main>
    )
  }

  if (screen.kind === 'choices') {
    return (
      <main className="gal-app">
        <header className="scene-header">
          <span className="scene-location">{pack.title}</span>
        </header>
        <section className="dialogue-box" aria-label="选项">
          <ol className="choice-list">
            {screen.choices.map((choice, i) => (
              <li key={choice.id}>
                <button
                  className="choice-button"
                  aria-label={`${CHOICE_LETTERS[i] ?? i + 1} ${choice.label}`}
                  onClick={() => void handleChoice(screen.sessionId, choice.id, screen.revision)}
                >
                  <span className="choice-letter">{CHOICE_LETTERS[i] ?? i + 1}</span>
                  <span className="choice-label">{choice.label}</span>
                  {choice.preview && <span className="choice-preview">{choice.preview}</span>}
                </button>
              </li>
            ))}
          </ol>
        </section>
      </main>
    )
  }

  if (screen.kind === 'error') {
    return (
      <main className="gal-app">
        <div className="error-banner" role="alert">
          <p className="error-message">{screen.message}</p>
        </div>
        <button
          className="primary-button"
          onClick={() =>
            setScreen({
              kind: 'play',
              pack,
              sessionId: screen.sessionId,
              revision: screen.revision,
            })
          }
        >
          重试
        </button>
      </main>
    )
  }

  // Ending
  return (
    <main className="gal-app">
      <header className="scene-header">
        <span className="ending-eyebrow">END</span>
      </header>
      <section className="dialogue-box ending-box" aria-label="结局">
        <h2 className="ending-title">{screen.endingTitle}</h2>
        {screen.blocks.map((block, i) => (
          <p key={i} className={block.kind === 'dialogue' ? 'dialogue-text ending-block' : 'narration-line'}>
            {block.kind === 'dialogue'
              ? `${characterName(screen.pack, block.character_id)}：${block.text}`
              : block.text}
          </p>
        ))}
      </section>
      <div className="action-bar">
        <button
          className="primary-button"
          onClick={() => {
            clearSessionId()
            const p = packRef.current
            if (p) setScreen({ kind: 'start', pack: p })
          }}
        >
          重新开始
        </button>
      </div>
    </main>
  )
}
