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

  it('preserves old revision on error', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onError('generation_failed')
    expect(p.committedRevision).toBeNull()
  })

  it('ignores blocks from wrong segment_id', () => {
    const p = new SegmentPlayer()
    p.start()
    p.onSegmentStarted('seg-1', 12)
    p.onBlock({ segment_id: 'seg-2', index: 0, kind: 'narration', text: 'Wrong segment.' })
    expect(p.provisionalCount).toBe(0)
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
})
