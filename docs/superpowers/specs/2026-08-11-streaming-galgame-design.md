# Streaming Galgame Experience Design

**Date:** 2026-08-11
**Status:** Approved (pending spec review)

## Problem

The current game generates an entire scene (10+ text blocks + 4 choices) in one batch, displays everything at once, then waits for the player to pick a choice. This feels like "read a document, take a quiz" — not like playing a galgame.

The player wants a traditional galgame experience:
- Text appears progressively (typewriter effect)
- Click / Enter to advance through lines, like reading a novel
- No lag during reading; the only acceptable loading is after selecting a choice
- The engine generates ahead in the background while the player reads

## Solution: Producer-Consumer Streaming Architecture

The engine (producer) generates text blocks and pushes them to the frontend via SSE as they complete. The player (consumer) reads at their own pace from a buffer. As long as generation outpaces reading (~200 chars/min human vs. much faster LLM), the player never feels waiting.

### Data Flow

```
Player selects choice
    │
    ▼
Backend starts streaming generation (SSE)
    │
    ├─ block 1 ready ──→ SSE push ──→ frontend buffer
    ├─ block 2 ready ──→ SSE push ──→ frontend buffer
    ├─ block 3 ready ──→ SSE push ──→ frontend buffer ✅ threshold reached, unlock playback
    │                                   │
    │                                   ▼ player starts reading (typewriter)
    ├─ block 4 ready ──→ SSE push ──→ frontend buffer   ← player reading block 1-2
    ├─ block 5 ready ───→ SSE push ──→ frontend buffer   ← player reading block 3
    ├─ ...
    ├─ choices ready ──→ SSE push
    │                                   │
    │                                   ▼ player finishes reading, choices ready
    ▼                               player selects → next cycle
```

## Backend Changes

### Pipeline: Merge Planner + Writer into Single Streaming Call

**Current** (two serial calls, ~75s to first text):

```
Planner(~50s) → Validator → Writer(~25s) → Simulator → EventStore
```

**New** (one streaming call, ~5s to first token):

```
StreamWriter(stream=True) → incremental parse → SSE push → EventStore
```

One LLM call with `stream=True`. The model generates narration/dialogue blocks followed by choices, streaming tokens directly. Input includes current game state, character profiles, and recent dialogue history.

### Incremental Block Extraction

As tokens stream in, a parser detects completed block boundaries and emits each block as an SSE event immediately. No waiting for the full response.

### Drop Simulator Post-Check

The current Simulator requires complete output to run, which conflicts with streaming. Replace with:
- Lightweight per-block validation (valid `character_id`, non-empty text)
- Trust model + prompt engineering for narrative consistency
- Atomic event commit at stream end (all-or-nothing)

### API Changes

| Endpoint | Current | New |
|----------|---------|-----|
| `POST /advance` | Returns JSON `RuntimeScene` (full scene) | Returns `text/event-stream` (blocks + choices + done) |
| `POST /choices/{id}` | Returns JSON `ActionResult` | Returns `text/event-stream` (blocks + choices + done) |

### SSE Event Format

```
event: block
data: {"kind":"dialogue","character_id":"alice","text":"你来了……太好了"}

event: block
data: {"kind":"narration","text":"夕阳把靠窗的座位染成蜂蜜色"}

event: choices
data: [{"id":"c1","label":"问艾丽丝笔记本内容","intent":"investigate"}, ...]

event: done
data: {"session_id":"...","revision":11}
```

Error case:

```
event: error
data: {"code":"generation_unavailable"}
```

No events committed on error; the session is unmodified and the request is safe to retry.

## Frontend Changes

### Screen Layout

```
┌─────────────────────────────────────┐
│  ☕ 咖啡馆 · 傍晚                     │  ← scene info bar
├─────────────────────────────────────┤
│                                     │
│  傍晚六点差一刻，咖啡馆里的客人已经     │
│  走得七七八八。夕阳把靠窗的座位染成     │
│  蜂蜜色…                             │  ← read text (scrolling log)
│                                     │
│  艾丽丝：你来了……太好了。我把事情      │  ← current block (typing)
│  搞砸了|                             │
│                                     │
├─────────────────────────────────────┤
│  ▼ 点击继续 / Enter                  │  ← interaction hint / choices
└─────────────────────────────────────┘
```

Text area is a **scrolling log**: previously-read content stays above, new content appears at the bottom. Like reading a novel, not clearing the screen each click.

### Playback State Machine

```
                    SSE stream connected
                        │
                        ▼
                 ┌─────────────┐
                 │  buffering  │  waiting for 2-3 blocks
                 └──────┬──────┘
                        │ threshold reached
                        ▼
                 ┌─────────────┐
          ┌──────│   playing   │──────┐
          │      └─────────────┘      │
          │ buffer empty,               │ all blocks played + choices received
          │ stream active               │
          ▼                             ▼
   ┌─────────────┐            ┌──────────────┐
   │  buffering  │            │ waiting_choice│
   └──────┬──────┘            └──────┬───────┘
          │ new block arrived        │ player selects
          └──→ playing ←─────────────┘
                                      │
                                      ▼
                                 connect new SSE stream
```

### Input: Click and Enter

Both are equivalent at all times:

| State | Input | Effect |
|-------|-------|--------|
| Typewriter animating | Click / Enter | Skip animation, show full text immediately |
| Current block complete | Click / Enter | Advance to next block, start typewriter |
| Buffer temporarily empty | Click / Enter | No effect (show `···` loading hint) |
| All blocks played, choices shown | Click option / Enter on focused option | Start next SSE stream |

### Block Display Rules

- **Narration block**: no character name, muted/italic style, plain text
- **Dialogue block**: bold character name + text, galgame dialogue-box style
- Advance **1 block** per click (one narration paragraph or one dialogue line)
- Typewriter speed: ~30 chars/sec default

### Loading and Error Handling

- **After choice selection**: bottom shows "思考中…" animation; switches to `···` once SSE connected
- **Generation failure**: bottom shows "生成失败，点击重试"; session unmodified
- **Network disconnect**: show "连接断开" with "重连" button; already-received blocks preserved in the log

## Key Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Buffer threshold | 2-3 blocks | Unlock playback once this many blocks arrive |
| Blocks per click | 1 | One narration paragraph or dialogue line |
| Typewriter speed | ~30 chars/sec | Future: player-configurable |
| Input methods | Click + Enter | Equivalent at all times |
| SSE event types | `block`, `choices`, `done`, `error` | |

## What Changes vs What Stays

**Changes:**
- Backend: merge Planner+Writer into single streaming call, new SSE endpoints, drop Simulator
- Frontend: complete rewrite of playback UI (scrolling log, typewriter, click/Enter advance)
- API client: streaming functions replacing batch request/response

**Stays:**
- Event store (SQLite, append-only, revisioned)
- Script pack compilation and validation
- Session lifecycle management
- Game state model (world state, character state, threads)
- Idempotency keys for mutations

## Out of Scope (YAGNI)

- Character portrait art / CG images
- Background music / sound effects
- Save/load slots (beyond current session restore)
- Auto-mode / skip-mode
- Backlog search
- Free-text player input
