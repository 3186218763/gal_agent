# Galgame AI — V2 Runtime (DeepSeek Responses)

Constrained dynamic galgame engine. Authors write a **script pack** (`pack.yaml`); the V2 runtime commits player choices, generates consequences through model agents, and gates every state change through deterministic validation, simulation, and a typed-event reducer. `TurnOrchestrator` is the one authoritative command flow, exposed as `POST /turns`.

Players choose only backend-presented choice IDs. No free text, no rewind, multi-ending.

## Architecture

```text
Script Pack (pack.yaml)
        |
        v
compile_script_pack → CompiledScriptPack
        |
        +---- offline: validate / init-session / inspect-session / init-pack (opening cache)
        |
        v  (requires OpenCode Go model config)
TurnOrchestrator  (the only mutation path)
├── Planner      (resolve the committed Choice Meaning → consequence)
├── UnifiedSegmentAgent / Director + SegmentWriter  (propose the next segment)
├── Validator + Deterministic Guard + Semantic Judge
├── Simulator    (pure, on copied state)
├── CompletionJudge (deterministic evidence evaluation at the ending)
└── StoryEventStore (SQLite, revisioned append-only, atomic command receipts)
        |
        v
REST API  POST /api/v2/sessions/{id}/turns  (SSE)
CLI       play-live (same TurnOrchestrator flow)
```

## Status

- V1 (Agents SDK Director/Character, WebSocket, `plot.md` beats) is **removed**; V2 is the only runtime and state authority. `tests/test_v2_only_layout.py` rejects legacy paths and routes structurally.
- The V2 runtime is implemented and offline-verified: a single authoritative `TurnOrchestrator` command flow with atomic command receipts; only deterministic validation → simulation → reducer → EventStore can mutate session state. Legacy mutation surfaces (`/advance`, `/choices/{id}`, `RuntimeService`, pregeneration with implicit-success results) are removed.
- Verification so far: backend `447 passed` (live tests opt-in via `RUN_LIVE_ZEN_TEST=1`), Ruff clean, `cafe_mystery` v2 pack valid with authored completion requirements; frontend `74 passed`, `npm run build` + `npm run lint` clean. Full gate results are reported in the handoff report after each change wave.
- **Not done yet:** real-model verification (live test and `play-live` autoplay need `OPENCODE_GO_API_KEY`) and the evaluation milestone (trace store, automated player policies, metrics, human playtest workflow).

## Design notes

Core invariants (archived design docs are superseded by this README):

- **Kernel is judge; agents propose and write.** Agents never mutate state directly; every irreversible change passes deterministic validation, simulation, guard, and typed-event reduction.
- **World truth ≠ character statements.** Facts, knowledge, beliefs, and spoken words are separate data; characters may lie or be mistaken, but never know unexplained facts.
- **No rewind, no free text.** Players send only backend-presented choice IDs with an `expected_revision`; committed facts are immutable.
- **A selected Choice Meaning is irreversible before generation begins.** The choice is committed atomically first; its consequence is resolved afterwards and carries the source choice event ID.
- **Failure preserves Pending Consequence.** If consequence generation fails, the session shows `pending_consequence_status: "awaiting_resolution"` and only `choice_id: null` resumes that exact consequence. One stable consequence command is shared across retries/devices and appends at most once.
- **Choices express intent, not pre-written outcomes.** Options describe what the player does; consequences are resolved and bounded by rules.
- **Endings are completion contracts.** The pack defines completion requirements; at the ending the deterministic CompletionJudge evaluates committed evidence (facts, relationship turning points, obligations, costs, stances) with citation chains.
- **No default-success result or placeholder fiction.** A failed generation never synthesizes an implicit success; an uncommitted or incomplete segment is never playable. Only atomically committed `segment_ready` data is presented.
- **Summaries are not fact sources.** Event Log and pack facts are authoritative; model history is only context cache.
- **Responses-only, no fallback protocol.** `GAL_LLM_API=responses` is the only accepted API; there is no Chat Completions path and no V1/stub fallback in production. Deterministic checks only keep a session safe or terminating.

### Round flow

```text
POST /turns  {expected_revision, idempotency_key, choice_id}
  → TurnOrchestrator
  → choice_id given?  commit Choice Meaning atomically (PlayerActionSelected)
  → consequence pending?  resume the exact pending consequence (same command)
  → Planner resolves the committed choice → consequence events
  → Director/UnifiedAgent proposes the next segment (plan + draft)
  → Validator → deterministic Guard → Semantic Judge → Simulator (copied state)
  → atomic commit (consequence + segment events + result) with expected_revision
  → SSE: segment_started → block* → segment_ready
```

The opening turn (`choice_id: null` on a fresh session) may reuse a validated cached opening from `init-pack`; every other segment is generated fresh and goes through the same deterministic gate chain.

Failure rules: on timeout/network error nothing is committed and the revision is unchanged — except that an already-committed Choice Meaning stays durable as a Pending Consequence. Invalid proposals are rejected (fail closed, `generation_unavailable`); there is no "standard-action fallback" and no implicit success. Revision conflicts discard the stale command and the lease is released.

### Security rules

- Keys must be rotated and never committed; the repo only ships an empty `.env.example`.
- `OPENAI_API_KEY` may alias `OPENCODE_GO_API_KEY` only when identical; mismatched values fail startup.
- Logs show variable names, model, and host only; OpenAI tracing is disabled by default.

## Deferred / next steps

- SSE segment streaming is live via `POST /api/v2/sessions/{id}/turns` (see HTTP API); optional WebSocket channel remains a future concern.
- Evaluation milestone: trace store, automated player policies, deterministic/model-backed runs, metrics, human playtest workflow.
- Optional visual assets (sprites, backgrounds, BGM) — reserved in pack/event contracts but out of scope.

## Prerequisites

- Python **3.11+** and [uv](https://github.com/astral-sh/uv)
- Node.js **18+** (frontend shell)
- OpenCode Go API key for live model calls (API server and `play-live`)

Offline pack validation and unit tests do **not** need a key.

## Configuration

Copy the example env file and fill in a **rotated** secret (never commit real keys):

```bash
cd backend
cp .env.example .env
```

Required V2 settings:

```dotenv
GAL_LLM_PROVIDER=opencode_go
OPENCODE_GO_API_KEY=
OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1
GAL_LLM_MODEL=deepseek-v4-flash
GAL_LLM_API=responses
GAL_LLM_TIMEOUT_SECONDS=45
GAL_LLM_MAX_RETRIES=1
```

Notes:

- Only `GAL_LLM_API=responses` is accepted. There is no Chat Completions path.
- `OPENAI_API_KEY` may alias `OPENCODE_GO_API_KEY` if both are equal; mismatched values fail startup.
- Any key previously exposed in chat must be **revoked**. Do not put secrets in `.env.example` or the repo.

Optional paths for the API process:

| Variable | Default | Meaning |
|----------|---------|---------|
| `GAL_DATABASE_PATH` | `data/story-v2.db` | SQLite event store |
| `GAL_SCRIPT_PACK_ROOT` | `script_packs` | Script pack root |

## Quick start

### Backend

```bash
cd backend
uv sync --extra dev
uv run python -m src.story.cli validate script_packs/cafe_mystery
uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

- `validate` / `init-session` / `inspect-session` are **offline** (no model key).
- API startup and `play-live` **require** the env config above.

Live autoplay (needs key):

```bash
cd backend
uv run python -m src.story.cli play-live script_packs/cafe_mystery \
  --database data/live.db --session-id demo --seed 17
```

### Frontend player

The React player is a real V2 client: it creates sessions, streams turns over SSE (`segment_started` → provisional `block` → `segment_ready`), buffers provisional blocks, plays them with a click/Enter typewriter, presents the 2-4 backend choices only after the local queue drains, shows endings with completion status, and replays committed segments from the projection after refresh (never issuing a duplicate turn). After a failed consequence turn it shows a retryable error; after refresh, `pending_consequence_status: "awaiting_resolution"` triggers exactly one recovery turn with `choiceId: null` — the committed choice is not re-offered and no provisional text is replayed. See `frontend/src/segmentPlayer.ts`, `frontend/src/Playback.tsx`, and `frontend/src/App.tsx`.

```bash
cd frontend
npm install
npm run dev
```

Dev URL is typically `http://127.0.0.1:5173`. Production build:

```bash
cd frontend
npm run build
```

## Script packs

Production pack path:

```text
backend/script_packs/<pack_id>/pack.yaml
```

Example: [`backend/script_packs/cafe_mystery/pack.yaml`](backend/script_packs/cafe_mystery/pack.yaml)

Packs define identity, experience bounds, protagonist, world, characters, facts, goals, and endings (including at least one `fallback`). There is no `plot.md` or beat script.

## Project layout

```text
gal_agent/
├── backend/
│   ├── src/
│   │   ├── main.py              # FastAPI app entry (V2-only)
│   │   └── story/
│   │       ├── api.py           # /api/v2 REST (sessions, projections, POST /turns)
│   │       ├── cli.py           # validate / init-session / inspect-session / play-live / init-pack
│   │       ├── conditions.py
│   │       ├── runtime/         # planner, segment writer, validator, guard, simulator, orchestrator
│   │       ├── script_pack/     # pack models + compiler
│   │       ├── state/           # events, session models, reducer
│   │       └── storage/         # StoryEventStore
│   ├── script_packs/
│   │   └── cafe_mystery/
│   ├── tests/                   # offline suite + tests/live/ (opt-in)
│   └── .env.example
├── frontend/                    # Vite React segment-aware player (SSE /turns)
└── README.md
```

## HTTP API (V2)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | `{"status":"ok","runtime":"v2"}` |
| `POST` | `/api/v2/sessions` | Create session (`pack_id`, `session_seed`) |
| `GET` | `/api/v2/sessions/{id}` | Public session projection |
| `GET` | `/api/v2/packs/{id}` | Public pack metadata projection |
| `POST` | `/api/v2/sessions/{id}/turns` | Stream one segment turn as SSE (`expected_revision`, `idempotency_key`, `choice_id`) — **the only mutation route** |

`/turns` is the one production mutation interface; the legacy `/advance` and `/choices/{id}` routes are removed and return 404. Every mutation requires an `idempotency_key`; retrying a completed command with the same key replays its stored result and appends no new events. Reads return safe public projections — internal state (fact truth values, character knowledge, beliefs, suspicions, goals, seeds, pack hashes) never crosses the API boundary.

The `/turns` endpoint streams `segment_started`, `block`, `segment_ready`, `heartbeat`, `retry_after`, and `error` SSE events; `segment_ready` carries `terminal`, `revision`, committed `blocks`, and `choices` or `ending` (one null). The frontend treats blocks as provisional until `segment_ready`.

`choice_id` semantics: `null` on a fresh session generates the opening; an offered `id` commits that choice and generates its consequence; `null` while `pending_consequence_status: "awaiting_resolution"` resumes exactly the pending consequence (the old choices are not re-offered). A failed generation yields an `error` frame (`generation_unavailable`); the durable GET projection then shows either an unmodified session or a Pending Consequence to recover — never a placeholder success.

OpenAPI: `http://127.0.0.1:8000/docs` when the server is running.

There is no `/api/sessions` or WebSocket game channel.

## Testing

```bash
cd backend
uv run pytest tests/ -q
```

Default suite is offline. Live network tests are skipped unless:

```bash
cd backend
RUN_LIVE_ZEN_TEST=1 uv run pytest -m live tests/live/test_opencode_go_v2_runtime.py -v
```

The live command reads the ignored `backend/.env` with `override=False`, so an explicitly exported CI secret wins. If the provider answers slowly, raise the timeout: `RUN_LIVE_ZEN_TEST=1 GAL_LLM_TIMEOUT_SECONDS=120 uv run pytest -m live tests/live/test_opencode_go_v2_runtime.py -v`. The real model is nondeterministic; failures fail closed and never modify the session.

## License

MIT
