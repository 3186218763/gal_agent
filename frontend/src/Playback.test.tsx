import { describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Playback from './Playback'
import { SegmentPlayer, type EndingMeta } from './segmentPlayer'
import type { PackProjection, NarrativeBlock, PresentedChoice } from './api'

const MOCK_PACK: PackProjection = {
  pack_id: 'test-pack',
  title: 'Test Pack',
  language: 'zh-CN',
  characters: [{ character_id: 'alice', name: 'Alice', public_profile: '' }],
  locations: [{ location_id: 'cafe', name: 'Cafe' }],
}

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
        choices: [{ id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: 'alice', preview: 'Ask Alice about the mystery' }],
      })
      return player
    })

    expect(result.current.playableBlocks).toHaveLength(2)
    expect(result.current.state).toBe('playing') // Not notified until drained
    expect(result.current.choices).toEqual([
      { id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: 'alice', preview: 'Ask Alice about the mystery' },
    ])

    result.current.dequeueBlock()
    expect(result.current.isDrained()).toBe(false)
    result.current.dequeueBlock()
    expect(result.current.isDrained()).toBe(true)

    result.current.onDrained()
    expect(result.current.state).toBe('waiting_choice')
    expect(result.current.choices).toEqual([{ id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: 'alice', preview: 'Ask Alice about the mystery' }])
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
      expect(onEnding).toHaveBeenCalledWith(ending, [blocks[0]], 2)
    })
  })
})
