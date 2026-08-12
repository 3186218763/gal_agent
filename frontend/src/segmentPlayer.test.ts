import { describe, expect, it } from 'vitest'
import { SegmentPlayer } from './segmentPlayer'
import type { NarrativeBlock, PresentedChoice } from './api'

const BLOCK_A: NarrativeBlock = { kind: 'narration', text: 'Scene opens.' }
const BLOCK_B: NarrativeBlock = { kind: 'dialogue', character_id: 'alice', text: 'Hello.' }
const CHOICES: PresentedChoice[] = [
  { id: 'c1', action_id: 'ask', label: 'Ask', intent: 'ask', target_character_id: null, preview: null },
]

describe('SegmentPlayer state machine', () => {
  it('starts in idle state', () => {
    const p = new SegmentPlayer()
    expect(p.state).toBe('idle')
    expect(p.playableBlocks).toEqual([])
  })

  it('transitions idle -> generating_after_choice on start()', () => {
    const p = new SegmentPlayer()
    p.start()
    expect(p.state).toBe('generating_after_choice')
  })

  it('transitions to buffering_segment on segment_started', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    expect(p.state).toBe('buffering_segment')
  })

  it('buffers blocks as provisional and is not playable before segment_ready', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Provisional.' })
    p.onBlock({ segment_id: 'seg-1', index: 1, kind: 'dialogue', character_id: 'alice', text: 'Hi.' })
    expect(p.state).toBe('buffering_segment')
    expect(p.playableBlocks).toEqual([])
    expect(p.provisionalCount).toBe(2)
  })

  it('unlocks blocks on segment_ready with terminal decision', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Scene opens.' })
    p.onBlock({ segment_id: 'seg-1', index: 1, kind: 'dialogue', character_id: 'alice', text: 'Hello.' })
    p.onSegmentReady({
      segment_id: 'seg-1',
      revision: 18,
      terminal: 'decision',
      choices: CHOICES,
    })
    expect(p.state).toBe('playing')
    expect(p.playableBlocks).toEqual([BLOCK_A, BLOCK_B])
  })

  it('unlocks blocks on segment_ready with terminal ending', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'The end.' })
    p.onSegmentReady({
      segment_id: 'seg-1',
      revision: 20,
      terminal: 'ending',
      ending: { ending_id: 'end-1', title: 'Truth', tone: 'bittersweet', terminal_state_summary: 'They parted ways.' },
    })
    expect(p.state).toBe('playing')
    expect(p.ending).toEqual({ ending_id: 'end-1', title: 'Truth', tone: 'bittersweet', terminal_state_summary: 'They parted ways.' })
  })

  it('handles zero-block segment_ready with decision terminal', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    expect(p.state).toBe('waiting_choice')
    expect(p.choices).toEqual(CHOICES)
  })

  it('handles zero-block segment_ready with ending terminal', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onSegmentReady({
      segment_id: 'seg-1', revision: 20, terminal: 'ending',
      ending: { ending_id: 'end-3', title: 'Zero', tone: 'quiet', terminal_state_summary: 'Nothing more.' },
    })
    expect(p.state).toBe('playing_ending')
    expect(p.ending).toEqual({ ending_id: 'end-3', title: 'Zero', tone: 'quiet', terminal_state_summary: 'Nothing more.' })
  })

  it('transitions to waiting_choice only after queue drains', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Scene.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    // Not drained yet — still playing
    expect(p.state).toBe('playing')
    expect(p.choices).toEqual(CHOICES)
    p.dequeueBlock()
    expect(p.isDrained()).toBe(true)
    p.onDrained()
    expect(p.state).toBe('waiting_choice')
    expect(p.choices).toEqual(CHOICES)
  })

  it('transitions to playing_ending only after queue drains', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Finale.' })
    p.onSegmentReady({
      segment_id: 'seg-1', revision: 20, terminal: 'ending',
      ending: { ending_id: 'end-2', title: 'Dawn', tone: 'hopeful', terminal_state_summary: 'A new day.' },
    })
    expect(p.state).toBe('playing')
    p.dequeueBlock()
    p.onDrained()
    expect(p.state).toBe('playing_ending')
  })

  it('discards provisional buffer on error before segment_ready', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Provisional.' })
    p.onError('generation_failed')
    expect(p.state).toBe('error')
    expect(p.provisionalCount).toBe(0)
    expect(p.playableBlocks).toEqual([])
  })

  it('onError does not set a revision', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onError('generation_failed')
    expect(p.committedRevision).toBeNull()
  })

  it('keeps committed blocks and revision when onError fires after segment_ready', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Committed.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    p.onError('transport_error')
    expect(p.state).toBe('error')
    expect(p.committedRevision).toBe(18)
    expect(p.playableBlocks).toEqual([{ kind: 'narration', text: 'Committed.' }])
  })

  it('ignores blocks from wrong segment_id', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-2', index: 0, kind: 'narration', text: 'Wrong segment.' })
    expect(p.provisionalCount).toBe(0)
  })

  it('ignores blocks received before segment_started', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Early.' })
    expect(p.provisionalCount).toBe(0)
    expect(p.state).toBe('generating_after_choice')
  })

  it('ignores a second segment_started while buffering', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'First.' })
    p.onSegmentStarted('seg-2', 13)
    p.onBlock({ segment_id: 'seg-2', index: 0, kind: 'narration', text: 'Second.' })
    expect(p.provisionalCount).toBe(1)
  })

  it('ignores segment_ready for a different segment_id', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onSegmentReady({ segment_id: 'seg-2', revision: 99, terminal: 'decision', choices: CHOICES })
    expect(p.state).toBe('buffering_segment')
    expect(p.committedRevision).toBeNull()
  })

  it('sorts provisional blocks by index on unlock', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 1, kind: 'dialogue', character_id: 'alice', text: 'Second.' })
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'First.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    expect(p.playableBlocks[0].text).toBe('First.')
    expect(p.playableBlocks[1].text).toBe('Second.')
  })

  it('loads blocks from projection for replay without turn', () => {
    const p = new SegmentPlayer()
    p.loadFromProjection([BLOCK_A, BLOCK_B], 18)
    expect(p.state).toBe('playing')
    expect(p.playableBlocks).toEqual([BLOCK_A, BLOCK_B])
    expect(p.isReplay).toBe(true)
  })

  it('onDrained is idempotent after a transition', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Scene.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    p.dequeueBlock()
    p.onDrained()
    expect(p.state).toBe('waiting_choice')
    p.onDrained()
    expect(p.state).toBe('waiting_choice')
  })

  it('onDrained is a no-op from idle, buffering_segment, and error states', () => {
    const idle = new SegmentPlayer()
    idle.onDrained()
    expect(idle.state).toBe('idle')

    const buffering = new SegmentPlayer()
    buffering.start()
    buffering.onSegmentStarted('seg-1', 12)
    buffering.onDrained()
    expect(buffering.state).toBe('buffering_segment')

    const errored = new SegmentPlayer()
    errored.start()
    errored.onError('generation_failed')
    errored.onDrained()
    expect(errored.state).toBe('error')
  })

  it('dequeueBlock returns null past the end of the queue', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'Only.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    expect(p.dequeueBlock()).toEqual({ kind: 'narration', text: 'Only.' })
    expect(p.dequeueBlock()).toBeNull()
  })

  it('peekBlock peeks without advancing the drain cursor', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'First.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    expect(p.peekBlock()).toEqual({ kind: 'narration', text: 'First.' })
    expect(p.remainingBlocks).toBe(1)
    expect(p.dequeueBlock()).toEqual({ kind: 'narration', text: 'First.' })
    expect(p.peekBlock()).toBeNull()
  })

  it('tracks totalBlocks and remainingBlocks consistently', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'A.' })
    p.onBlock({ segment_id: 'seg-1', index: 1, kind: 'dialogue', character_id: 'alice', text: 'B.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    expect(p.totalBlocks).toBe(2)
    expect(p.remainingBlocks).toBe(2)
    p.dequeueBlock()
    expect(p.totalBlocks).toBe(2)
    expect(p.remainingBlocks).toBe(1)
    p.dequeueBlock()
    expect(p.remainingBlocks).toBe(0)
  })

  it('start() after a completed turn clears choices, ending, and blocks', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-1', index: 0, kind: 'narration', text: 'One.' })
    p.onSegmentReady({ segment_id: 'seg-1', revision: 18, terminal: 'decision', choices: CHOICES })
    p.dequeueBlock()
    p.onDrained()
    expect(p.state).toBe('waiting_choice')
    expect(p.choices).toEqual(CHOICES)

    p.start()
    p.onSegmentStarted('seg-2', 18)
    p.onBlock({ segment_id: 'seg-2', index: 0, kind: 'narration', text: 'Two.' })
    p.onSegmentReady({
      segment_id: 'seg-2', revision: 20, terminal: 'ending',
      ending: { ending_id: 'end-9', title: 'Finale', tone: 'somber', terminal_state_summary: 'The end.' },
    })
    p.dequeueBlock()
    p.onDrained()
    expect(p.state).toBe('playing_ending')

    p.start()
    expect(p.state).toBe('generating_after_choice')
    expect(p.choices).toBeNull()
    expect(p.ending).toBeNull()
    expect(p.playableBlocks).toEqual([])
    expect(p.committedRevision).toBeNull()
    expect(p.isReplay).toBe(false)
  })
})
