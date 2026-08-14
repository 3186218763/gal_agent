import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Playback from './Playback'
import { SegmentPlayer, type EndingMeta } from './segmentPlayer'
import type { PackProjection, NarrativeBlock, PresentedChoice } from './api'
import type { StreamEvent } from './stream'

const { streamTurnMock, setStreamEvents } = vi.hoisted(() => {
  let events: StreamEvent[] = []
  return {
    streamTurnMock: vi.fn(async function* (): AsyncGenerator<StreamEvent> {
      for (const evt of events) {
        await new Promise((r) => setTimeout(r, 1))
        yield evt
      }
    }),
    setStreamEvents: (evts: StreamEvent[]) => {
      events = evts
    },
  }
})

vi.mock('./stream', () => ({
  streamTurn: streamTurnMock,
}))

const MOCK_PACK: PackProjection = {
  pack_id: 'test-pack',
  title: 'Test Pack',
  language: 'zh-CN',
  characters: [{ character_id: 'alice', name: 'Alice', public_profile: '' }],
  locations: [{ location_id: 'cafe', name: 'Cafe' }],
}

const TWO_CHOICES: PresentedChoice[] = [
  { id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: 'alice', preview: 'Ask Alice' },
  { id: 'c2', action_id: 'observe', label: 'Observe', intent: 'observe', target_character_id: null, preview: null },
]

beforeEach(() => {
  streamTurnMock.mockClear()
})

describe('Playback streaming integration', () => {
  it('buffers blocks until segment_ready, then drains queue', async () => {
    const { result } = renderHook(() => {
      const player = new SegmentPlayer()
      player.start()
      player.onSegmentStarted('seg-1', 0)
      player.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'First.' })
      player.onBlock({ segment_id: 'seg-1', index: 1, kind: 'dialogue', character_id: 'alice', text: 'Second.' })
      player.onSegmentReady({
        segment_id: 'seg-1',
        revision: 1,
        terminal: 'decision',
        choices: TWO_CHOICES,
      })
      return player
    })

    expect(result.current.playableBlocks).toHaveLength(2)
    expect(result.current.state).toBe('playing') // Not notified until drained
    expect(result.current.choices).toEqual(TWO_CHOICES)

    result.current.dequeueBlock()
    expect(result.current.isDrained()).toBe(false)
    result.current.dequeueBlock()
    expect(result.current.isDrained()).toBe(true)

    result.current.onDrained()
    expect(result.current.state).toBe('waiting_choice')
    expect(result.current.choices).toEqual(TWO_CHOICES)
  })

  it('shows error and discards provisional buffer on error event before segment_ready', async () => {
    const { result } = renderHook(() => {
      const player = new SegmentPlayer()
      player.start()
      player.onSegmentStarted('seg-1', 0)
      player.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Provisional.' })
      player.onError('generation_failed')
      return player
    })

    expect(result.current.state).toBe('error')
    expect(result.current.provisionalCount).toBe(0)
    expect(result.current.playableBlocks).toEqual([])
  })

  it('shows buffering overlay until segment_ready, then auto-dequeues first block', async () => {
    setStreamEvents([
      { event: 'segment_started', data: { segment_id: 'seg-1', expected_revision: 0 } },
      { event: 'block', data: { segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Hello.' } },
      { event: 'segment_ready', data: { segment_id: 'seg-1', revision: 1, terminal: 'decision', choices: TWO_CHOICES } },
    ])

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={0}
        choiceId={null}
        onChoices={vi.fn()}
        onEnding={vi.fn()}
        onError={vi.fn()}
      />
    )

    // Buffering overlay is visible while the segment is generating.
    expect(screen.getByRole('status')).toHaveTextContent('正在生成…')

    // segment_ready arrives -> overlay disappears and the block is auto-dequeued.
    expect(await screen.findByText('Hello.', {}, { timeout: 3000 })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
  })

  it('shows pipeline stages and elapsed time in the buffering overlay', async () => {
    setStreamEvents([
      { event: 'progress', data: { stage: 'generating', elapsed_ms: 5 } },
      { event: 'progress', data: { stage: 'validating', elapsed_ms: 900 } },
      { event: 'segment_started', data: { segment_id: 'seg-1', expected_revision: 0 } },
      { event: 'block', data: { segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Scene.' } },
      { event: 'segment_ready', data: { segment_id: 'seg-1', revision: 1, terminal: 'decision', choices: TWO_CHOICES } },
    ])

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={0}
        choiceId={null}
        onChoices={vi.fn()}
        onEnding={vi.fn()}
        onError={vi.fn()}
      />
    )

    // Stage labels appear as the backend reports transitions; the first
    // stage stays visible as a completed step.
    expect(await screen.findByText('撰写故事段落')).toBeInTheDocument()
    expect(await screen.findByText('校验一致性')).toBeInTheDocument()
    expect(screen.getByText(/已用时/)).toBeInTheDocument()

    // Overlay clears once the segment is ready.
    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
  })

  it('maps error events to zh-CN messages via onError', async () => {
    const onError = vi.fn()
    setStreamEvents([
      { event: 'segment_started', data: { segment_id: 'seg-1', expected_revision: 0 } },
      { event: 'block', data: { segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Provisional.' } },
      { event: 'error', data: { code: 'generation_failed' } },
    ])

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={0}
        choiceId={null}
        onChoices={vi.fn()}
        onEnding={vi.fn()}
        onError={onError}
      />
    )

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith('生成失败，请重试')
    })
  })

  it('reports disconnect when the stream ends before segment_ready', async () => {
    const onError = vi.fn()
    setStreamEvents([
      { event: 'segment_started', data: { segment_id: 'seg-1', expected_revision: 0 } },
      { event: 'block', data: { segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Provisional.' } },
    ])

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={0}
        choiceId={null}
        onChoices={vi.fn()}
        onEnding={vi.fn()}
        onError={onError}
      />
    )

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith('连接中断，请重试')
    })
  })
})

describe('Playback replay integration', () => {
  it('loads blocks from projection without streaming', async () => {
    const onChoices = vi.fn()
    const blocks: NarrativeBlock[] = [
      { kind: 'narration', text: 'Replay block.' },
    ]
    const choices: PresentedChoice[] = [{ id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: null, preview: null }]

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={1}
        choiceId={null}
        replayBlocks={blocks}
        replayRevision={1}
        replayChoices={choices}
        onChoices={onChoices}
        onEnding={vi.fn()}
        onError={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Replay block.')).toBeInTheDocument()
    })
  })

  it('calls onChoices after draining replay blocks', async () => {
    const onChoices = vi.fn()
    const blocks: NarrativeBlock[] = [
      { kind: 'narration', text: 'First.' },
      { kind: 'dialogue', character_id: 'alice', text: 'Second.' },
    ]
    const choices: PresentedChoice[] = [{ id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: 'alice', preview: 'Ask Alice' }]

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={1}
        choiceId={null}
        replayBlocks={blocks}
        replayRevision={1}
        replayChoices={choices}
        onChoices={onChoices}
        onEnding={vi.fn()}
        onError={vi.fn()}
      />
    )

    const log = screen.getByRole('button', { name: '对话框（点击继续）' })

    // Click through all blocks
    await waitFor(() => expect(screen.getByText('First.')).toBeInTheDocument())
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance

    await waitFor(() => expect(screen.getByText('Second.')).toBeInTheDocument())
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance - queue drains

    await waitFor(() => {
      expect(onChoices).toHaveBeenCalledWith(choices, 1)
    })
  })

  it('calls onEnding after draining replay blocks with ending metadata', async () => {
    const onEnding = vi.fn()
    const blocks: NarrativeBlock[] = [{ kind: 'narration', text: 'Final block.' }]
    const ending: EndingMeta = {
      ending_id: 'truth',
      title: '真相',
      tone: 'bittersweet',
      terminal_state_summary: 'They parted ways.',
    }

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={1}
        choiceId={null}
        replayBlocks={blocks}
        replayRevision={2}
        replayEnding={ending}
        onChoices={vi.fn()}
        onEnding={onEnding}
        onError={vi.fn()}
      />
    )

    const log = screen.getByRole('button', { name: '对话框（点击继续）' })
    await waitFor(() => expect(screen.getByText('Final block.')).toBeInTheDocument())
    fireEvent.click(log) // skip typewriter
    fireEvent.click(log) // advance - queue drains

    await waitFor(() => {
      expect(onEnding).toHaveBeenCalledWith(ending, [blocks[0]], 2, null)
    })
  })

  it('surfaces choices immediately when replaying a pending-decision projection with no blocks', async () => {
    const onChoices = vi.fn()
    const onError = vi.fn()

    render(
      <Playback
        pack={MOCK_PACK}
        sessionId="s1"
        expectedRevision={1}
        choiceId={null}
        replayBlocks={[]}
        replayRevision={1}
        replayChoices={TWO_CHOICES}
        onChoices={onChoices}
        onEnding={vi.fn()}
        onError={onError}
      />
    )

    await waitFor(() => {
      expect(onChoices).toHaveBeenCalledWith(TWO_CHOICES, 1)
    })
    expect(streamTurnMock).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })
})
