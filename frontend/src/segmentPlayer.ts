import type { NarrativeBlock, PresentedChoice } from './api'

// ── SSE Event Data Shapes (match Plan 2 protocol exactly) ──

export interface SegmentStartedData {
  segment_id: string
  expected_revision: number
}

export interface BlockEventData {
  segment_id: string
  index: number
  kind: 'narration' | 'dialogue'
  text: string
  character_id?: string | null
}

export interface SegmentReadyData {
  segment_id: string
  revision: number
  terminal: 'decision' | 'ending'
  choices?: PresentedChoice[]
  ending?: {
    ending_id: string
    title: string
    tone: string
    terminal_state_summary: string
  }
  blocks?: NarrativeBlock[]
}

export interface EndingMeta {
  ending_id: string
  title: string
  tone: string
  terminal_state_summary: string
}

// ── State Machine ──

export type SegmentPlayerState =
  | 'idle'
  | 'generating_after_choice'
  | 'buffering_segment'
  | 'playing'
  | 'waiting_choice'
  | 'playing_ending'
  | 'error'

interface ProvisionalBlock {
  index: number
  block: NarrativeBlock
}

export class SegmentPlayer {
  private _state: SegmentPlayerState = 'idle'
  private _segmentId: string | null = null
  private _provisional: ProvisionalBlock[] = []
  private _unlocked: NarrativeBlock[] = []
  private _drainIndex = 0
  private _choices: PresentedChoice[] | null = null
  private _ending: EndingMeta | null = null
  private _committedRevision: number | null = null
  private _errorCode: string | null = null
  private _isReplay = false

  get state(): SegmentPlayerState { return this._state }
  get provisionalCount(): number { return this._provisional.length }
  get playableBlocks(): NarrativeBlock[] { return [...this._unlocked] }
  get choices(): PresentedChoice[] | null { return this._choices }
  get ending(): EndingMeta | null { return this._ending }
  get committedRevision(): number | null { return this._committedRevision }
  get errorCode(): string | null { return this._errorCode }
  get isReplay(): boolean { return this._isReplay }

  /** Begin a new turn — player transitions to generating_after_choice. */
  start(): void {
    this._reset()
    this._state = 'generating_after_choice'
  }

  /** SSE segment_started — begin buffering provisional blocks. */
  onSegmentStarted(segmentId: string, _expectedRevision: number): void {
    if (this._state !== 'generating_after_choice') return
    this._segmentId = segmentId
    this._state = 'buffering_segment'
  }

  /** SSE block — buffer as provisional; ignored if wrong segment or not buffering. */
  onBlock(data: BlockEventData): void {
    if (this._state !== 'buffering_segment') return
    if (data.segment_id !== this._segmentId) return
    const block: NarrativeBlock = {
      kind: data.kind,
      text: data.text,
      character_id: data.character_id ?? undefined,
    }
    this._provisional.push({ index: data.index, block })
  }

  /** SSE segment_ready — unlock buffered blocks, sort by index, transition to playing. */
  onSegmentReady(data: SegmentReadyData): void {
    if (this._state !== 'buffering_segment') return
    if (data.segment_id !== this._segmentId) return

    // Sort by index, extract blocks
    this._provisional.sort((a, b) => a.index - b.index)
    this._unlocked = this._provisional.map((p) => p.block)
    this._provisional = []
    this._drainIndex = 0
    this._committedRevision = data.revision
    this._state = 'playing'

    if (data.terminal === 'decision') {
      this._choices = data.choices ?? []
    } else if (data.terminal === 'ending' && data.ending) {
      this._ending = data.ending
    }

    // If there are no blocks (edge case), immediately check drain
    if (this._unlocked.length === 0) {
      this.onDrained()
    }
  }

  /** SSE error — discard provisional buffer, transition to error. */
  onError(code: string): void {
    this._provisional = []
    this._errorCode = code
    this._state = 'error'
  }

  /** Dequeue next block for playback. Returns null when queue is empty. */
  dequeueBlock(): NarrativeBlock | null {
    if (this._drainIndex >= this._unlocked.length) return null
    const block = this._unlocked[this._drainIndex]
    this._drainIndex++
    return block
  }

  /** True when all unlocked blocks have been dequeued. */
  isDrained(): boolean {
    return this._drainIndex >= this._unlocked.length
  }

  /** Called when the playback component finishes its local queue. */
  onDrained(): void {
    if (!this.isDrained()) return
    if (this._state !== 'playing') return
    if (this._choices) {
      this._state = 'waiting_choice'
    } else if (this._ending) {
      this._state = 'playing_ending'
    }
  }

  /** Load committed blocks from a projection (replay after refresh). */
  loadFromProjection(
    blocks: NarrativeBlock[],
    revision: number,
    choices?: PresentedChoice[] | null,
    ending?: EndingMeta | null
  ): void {
    this._reset()
    this._unlocked = [...blocks]
    this._drainIndex = 0
    this._committedRevision = revision
    this._isReplay = true
    if (choices) this._choices = choices
    if (ending) this._ending = ending
    if (blocks.length === 0) {
      this._state = 'idle'
    } else {
      this._state = 'playing'
    }
  }

  /** Peek current block without advancing drain cursor. */
  peekBlock(): NarrativeBlock | null {
    if (this._drainIndex >= this._unlocked.length) return null
    return this._unlocked[this._drainIndex]
  }

  /** Total unlocked block count. */
  get totalBlocks(): number {
    return this._unlocked.length
  }

  /** Blocks remaining to be drained. */
  get remainingBlocks(): number {
    return this._unlocked.length - this._drainIndex
  }

  private _reset(): void {
    this._segmentId = null
    this._provisional = []
    this._unlocked = []
    this._drainIndex = 0
    this._choices = null
    this._ending = null
    this._committedRevision = null
    this._errorCode = null
    this._isReplay = false
  }
}
