import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import {
  ApiError,
  DEFAULT_PACK_ID,
  type NarrativeBlock,
  type PackProjection,
  type PresentedChoice,
  type SegmentEndingMeta,
  createSession,
  fetchPack,
  fetchSession,
  newSessionSeed,
} from './api'
import { clearSessionId, loadSessionId, saveSessionId } from './storage'
import Playback, { type PlaybackProps } from './Playback'

const CHOICE_LETTERS = ['A', 'B', 'C', 'D']

type Screen =
  | { kind: 'booting' }
  | { kind: 'boot-error'; message: string }
  | { kind: 'start'; pack: PackProjection }
  | {
      kind: 'play'
      pack: PackProjection
      sessionId: string
      revision: number
      choiceId: string | null
      // Replay fields — when set, Playback uses projection instead of streaming
      replayBlocks?: NarrativeBlock[]
      replayChoices?: PresentedChoice[]
      replayEnding?: SegmentEndingMeta | null
    }
  | { kind: 'choices'; pack: PackProjection; sessionId: string; revision: number; choices: PresentedChoice[] }
  | { kind: 'ending'; pack: PackProjection; sessionId: string; ending: SegmentEndingMeta; cleared: boolean | null }
  | { kind: 'error'; pack: PackProjection; sessionId: string; revision: number; message: string }

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
          // Session is over — show ending screen if we have ending metadata
          if (session.segment_ending) {
            setScreen({
              kind: 'ending',
              pack,
              sessionId: storedId,
              ending: session.segment_ending,
              cleared: session.cleared,
            })
          } else if (session.ending_id) {
            setScreen({
              kind: 'ending',
              pack,
              sessionId: storedId,
              ending: {
                ending_id: session.ending_id,
                title: session.ending_title ?? '',
                tone: '',
                terminal_state_summary: '',
              },
              cleared: session.cleared,
            })
          } else {
            setScreen({ kind: 'start', pack })
          }
        } else if (session.pending_consequence_status === 'awaiting_resolution') {
          setScreen({
            kind: 'play',
            pack,
            sessionId: storedId,
            revision: session.revision,
            choiceId: null,
          })
        } else if (session.choices.length > 0 || (session.segment_choices && session.segment_choices.length > 0)) {
          // Has unplayed choices — replay segment blocks then show choices
          const choices = session.segment_choices.length > 0 ? session.segment_choices : session.choices
          setScreen({
            kind: 'play',
            pack,
            sessionId: storedId,
            revision: session.revision,
            choiceId: null,
            replayBlocks: session.segment_blocks ?? session.blocks,
            replayChoices: choices,
          })
        } else if (session.segment_blocks && session.segment_blocks.length > 0) {
          // Has committed segment blocks to replay (no choices yet — mid-segment)
          setScreen({
            kind: 'play',
            pack,
            sessionId: storedId,
            revision: session.revision,
            choiceId: null,
            replayBlocks: session.segment_blocks,
          })
        } else {
          // Active session, no buffered segment — start opening turn
          setScreen({
            kind: 'play',
            pack,
            sessionId: storedId,
            revision: session.revision,
            choiceId: null,
          })
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
      setScreen({
        kind: 'play',
        pack,
        sessionId: session.session_id,
        revision: session.revision,
        choiceId: null,
      })
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
      const sessionId =
        screen.kind === 'play' || screen.kind === 'choices' ? screen.sessionId : ''
      if (choices.length > 0) {
        setScreen({ kind: 'choices', pack, sessionId, revision, choices })
      } else {
        // Empty choices is a backend anomaly (DecisionRequired dead-end).
        // Surface an explicit error instead of re-streaming into a loop.
        setScreen({
          kind: 'error',
          pack,
          sessionId,
          revision,
          message: '未收到可选项，请重试',
        })
      }
    },
    [screen],
  )

  const handleChoice = useCallback(
    async (sessionId: string, choiceId: string, revision: number) => {
      const pack = packRef.current
      if (!pack) return
      // Transition to play screen — Playback will issue streamTurn
      setScreen({
        kind: 'play',
        pack,
        sessionId,
        revision,
        choiceId,
      })
    },
    [],
  )

  const handleEnding = useCallback(
    (ending: SegmentEndingMeta, _blocks: NarrativeBlock[], _revision: number, cleared?: boolean | null) => {
      const pack = packRef.current
      if (!pack) return
      const sessionId = screen.kind === 'play' ? screen.sessionId : ''
      setScreen({
        kind: 'ending',
        pack,
        sessionId,
        ending,
        cleared: cleared ?? null,
      })
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
    const playbackProps: PlaybackProps = {
      pack,
      sessionId: screen.sessionId,
      expectedRevision: screen.revision,
      choiceId: screen.choiceId,
      onChoices: (choices, rev) => handleChoices(choices, rev),
      onEnding: (ending, blocks, rev) => handleEnding(ending, blocks, rev),
      onError: (msg) =>
        setScreen({
          kind: 'error',
          pack,
          sessionId: screen.sessionId,
          revision: screen.revision,
          message: msg,
        }),
    }
    if (screen.replayBlocks !== undefined) {
      playbackProps.replayBlocks = screen.replayBlocks
      playbackProps.replayRevision = screen.revision
      playbackProps.replayChoices = screen.replayChoices
      playbackProps.replayEnding = screen.replayEnding ?? null
    }
    return (
      <main className="gal-app">
        <header className="scene-header">
          <span className="scene-location">{pack.title}</span>
        </header>
        <Playback
          key={`${screen.sessionId}-${screen.revision}-${screen.choiceId ?? 'open'}`}
          {...playbackProps}
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
          onClick={() => {
            // Retry: re-fetch projection and resume
            void boot()
          }}
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
        <h2 className="ending-title">{screen.ending.title}</h2>
        <p className="ending-tone">{screen.ending.tone}</p>
        {screen.ending.terminal_state_summary && (
          <p className="ending-summary">{screen.ending.terminal_state_summary}</p>
        )}
        {screen.cleared === true && <p className="ending-cleared">已通过</p>}
        {screen.cleared === false && <p className="ending-cleared">未通过</p>}
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
