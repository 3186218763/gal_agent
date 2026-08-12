import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import './demo.css'
import {
  type DemoBlock,
  type DemoCharacter,
  type DemoChoice,
  type DemoEnding,
  type DemoNode,
  demoScript,
} from './script'

// ── 常量 ──

const TYPEWRITER_MS = 30
const BUFFER_DELAY_MS = 350 // 每个 block 出现前的短暂"生成"等待,模拟后端流式

// ── 类型 ──

type Phase = 'title' | 'play' | 'choice' | 'ending'

// ── 工具函数 ──

function findCharacter(id: string): DemoCharacter | undefined {
  return demoScript.characters.find((c) => c.id === id)
}

/** 当前说话的角色决定场景中高亮的立绘。 */
function speakerId(block: DemoBlock | null): string | null {
  if (!block || block.kind !== 'dialogue') return null
  return block.characterId ?? null
}

// ── 主组件 ──

export default function DemoApp() {
  const [phase, setPhase] = useState<Phase>('title')
  const [node, setNode] = useState<DemoNode | null>(null)
  const [blockIndex, setBlockIndex] = useState(0)
  const [typedText, setTypedText] = useState('')
  const [typing, setTyping] = useState(false)
  const [buffering, setBuffering] = useState(false)

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const bufferTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── 打字机 ──

  const stopTypewriter = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const startTypewriter = useCallback(
    (fullText: string) => {
      stopTypewriter()
      setTyping(true)
      let i = 0
      timerRef.current = setInterval(() => {
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

  // ── 进入一个 node 的指定 block ──

  const enterNode = useCallback(
    (targetNode: DemoNode) => {
      setNode(targetNode)
      setBlockIndex(0)
      setTypedText('')
      setPhase('play')
      // 模拟"后端生成"的短暂等待
      setBuffering(true)
      bufferTimerRef.current = setTimeout(() => {
        setBuffering(false)
        startTypewriter(targetNode.blocks[0].text)
      }, BUFFER_DELAY_MS)
    },
    [startTypewriter],
  )

  // ── 推进逻辑 ──

  const advance = useCallback(() => {
    if (!node) return

    // 正在打字 → 点击立刻完成全文
    if (typing) {
      stopTypewriter()
      setTypedText(node.blocks[blockIndex].text)
      setTyping(false)
      return
    }

    // 正在缓冲 → 忽略
    if (buffering) return

    const nextIndex = blockIndex + 1
    if (nextIndex < node.blocks.length) {
      // 下一段 block
      setBlockIndex(nextIndex)
      setTypedText('')
      setBuffering(true)
      bufferTimerRef.current = setTimeout(() => {
        setBuffering(false)
        startTypewriter(node.blocks[nextIndex].text)
      }, BUFFER_DELAY_MS)
      return
    }

    // 所有 block 播完
    if (node.choices && node.choices.length > 0) {
      setPhase('choice')
    } else if (node.ending) {
      setPhase('ending')
    } else if (node.goto) {
      const next = demoScript.nodes[node.goto]
      if (next) enterNode(next)
    }
  }, [node, typing, buffering, blockIndex, startTypewriter, stopTypewriter, enterNode])

  // ── 选择选项 ──

  const handleChoice = useCallback(
    (choice: DemoChoice) => {
      const next = demoScript.nodes[choice.goto]
      if (next) enterNode(next)
    },
    [enterNode],
  )

  // ── 开始 / 重开 ──

  const startGame = useCallback(() => {
    const start = demoScript.nodes[demoScript.startNode]
    if (start) enterNode(start)
  }, [enterNode])

  const restart = useCallback(() => {
    stopTypewriter()
    if (bufferTimerRef.current) clearTimeout(bufferTimerRef.current)
    setNode(null)
    setBlockIndex(0)
    setTypedText('')
    setTyping(false)
    setBuffering(false)
    setPhase('title')
  }, [stopTypewriter])

  // ── 键盘: Enter / Space 推进 ──

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        // 选项和结局界面不拦截空格/回车(需要点按钮)
        if (phase === 'play') {
          e.preventDefault()
          advance()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [phase, advance])

  // ── 清理 ──

  useEffect(() => {
    return () => {
      stopTypewriter()
      if (bufferTimerRef.current) clearTimeout(bufferTimerRef.current)
    }
  }, [stopTypewriter])

  // ── 渲染 ──

  const currentBlock: DemoBlock | null = node ? node.blocks[blockIndex] : null
  const activeSpeaker = speakerId(currentBlock)
  const sceneId = node?.scene ?? 'cafe'

  if (phase === 'title') {
    return (
      <div className="demo-stage demo-bg-cafe">
        <div className="demo-title-overlay">
          <h1 className="demo-title">{demoScript.title}</h1>
          <p className="demo-subtitle">{demoScript.subtitle}</p>
          <button className="demo-btn-primary" onClick={startGame}>
            开始游戏
          </button>
        </div>
      </div>
    )
  }

  if (phase === 'ending' && node?.ending) {
    return <EndingScreen ending={node.ending} onRestart={restart} />
  }

  return (
    <div className={`demo-stage demo-bg-${sceneId}`} onClick={advance}>
      {/* ── 立绘区域 ── */}
      <div className="demo-standee-area">
        {demoScript.characters
          .filter((c) => c.id !== 'protagonist')
          .map((c) => {
            const present = isPresent(node, c.id)
            if (!present) return null
            const isActive = activeSpeaker === c.id
            return (
              <CharacterStandee
                key={c.id}
                character={c}
                active={isActive}
              />
            )
          })}
      </div>

      {/* ── 选项层 ── */}
      {phase === 'choice' && node?.choices && (
        <div className="demo-choice-layer" onClick={(e) => e.stopPropagation()}>
          <div className="demo-choice-panel">
            {node.choices.map((choice, i) => (
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

      {/* ── 缓冲指示 ── */}
      {buffering && phase === 'play' && (
        <div className="demo-buffering">
          <span className="demo-buffering-dot" />
          <span className="demo-buffering-dot" />
          <span className="demo-buffering-dot" />
        </div>
      )}

      {/* ── 底部对话框 ── */}
      {phase === 'play' && currentBlock && (
        <div className="demo-dialogue-box">
          {currentBlock.kind === 'dialogue' && currentBlock.characterId && (
            <div
              className="demo-speaker-name"
              style={{ '--speaker-color': findCharacter(currentBlock.characterId)?.color ?? '#fff' } as CSSProperties}
            >
              {findCharacter(currentBlock.characterId)?.name ?? '???'}
            </div>
          )}
          <p className={`demo-dialogue-text ${currentBlock.kind === 'narration' ? 'is-narration' : ''}`}>
            {typedText}
            {typing && <span className="demo-cursor">▌</span>}
          </p>
          {!typing && !buffering && (
            <div className="demo-advance-hint">▼</div>
          )}
        </div>
      )}

      {/* ── 底部菜单栏 ── */}
      <div className="demo-menu-bar" onClick={(e) => e.stopPropagation()}>
        <button className="demo-menu-button" onClick={restart}>
          标题
        </button>
      </div>
    </div>
  )
}

// ── 子组件: 角色立绘占位 ──

function CharacterStandee({
  character,
  active,
}: {
  character: DemoCharacter
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
      {active && <div className="demo-standee-name" style={{ color: character.color }}>{character.name}</div>}
    </div>
  )
}

// ── 子组件: 结局画面 ──

function EndingScreen({
  ending,
  onRestart,
}: {
  ending: DemoEnding
  onRestart: () => void
}) {
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
        <button className="demo-btn-primary" onClick={onRestart}>
          回到标题
        </button>
      </div>
    </div>
  )
}

// ── 辅助: 判断角色是否在场 ──

function isPresent(node: DemoNode | null, characterId: string): boolean {
  if (!node) return false
  // 只要这个 node 的任何一个 block 里有该角色的对话,就算在场
  return node.blocks.some(
    (b) => b.kind === 'dialogue' && b.characterId === characterId,
  )
}
