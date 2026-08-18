# Galgame AI Backend (V2)

V2-only FastAPI + CLI runtime. Script packs compile offline; live scene generation uses the EveryGPT OpenAI-compatible **Chat Completions** endpoint (`gemini-3.7-flash`) through a direct structured-output client (`src/story/runtime/model.py:LLMClient` — no agent framework).

## Features

- Event-sourced sessions (`StoryEventStore`, revisioned append)
- Idempotent mutations via atomic SQLite command receipts (events + result commit together)
- One authoritative command flow (`TurnOrchestrator`, exposed as `POST /turns`) for opening, choice selection, Pending Consequence recovery, and endings
- A selected Choice Meaning is committed before generation; a failed consequence leaves a durable Pending Consequence that only `choice_id: null` may resume
- Strict-schema Planner/Writer outputs; Validator + deterministic Guard + Simulator gate model proposals before the reducer commits; invalid turns fail closed without committing
- Safe public pack/session projections for the browser player (never expose fact truth, knowledge, seeds, or pack hashes)
- Offline pack validation and session inspect without a model key
- Opt-in live tests and `play-live` autoplay when a key is configured

## Setup

```bash
cd backend
uv sync --extra dev
cp .env.example .env
```

Edit `.env` (leave secrets out of git):

```dotenv
GAL_LLM_PROVIDER=everygpt
EVERYGPT_API_KEY=
EVERYGPT_BASE_URL=https://api.everygpt.site/v1
GAL_LLM_MODEL=gemini-3.7-flash
GAL_LLM_API=chat_completions
GAL_LLM_TIMEOUT_SECONDS=45
GAL_LLM_MAX_RETRIES=1
```

The alternative provider is `opencode_go` (OpenCode Go **Responses**, `deepseek-v4-flash`, `GAL_LLM_API=responses`, key `OPENCODE_GO_API_KEY`).

**Security:** any key previously exposed in chat must be revoked. Never put a real key in `.env.example`. `OPENAI_API_KEY` is an optional equal-value alias for the provider key.

## Commands

```bash
cd backend
uv sync --extra dev
uv run python -m src.story.cli validate script_packs/yokai_after_school
uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
uv run python -m src.story.cli play-live script_packs/yokai_after_school \
  --database data/live.db --session-id demo --seed 17
```

| Command | Needs model key? |
|---------|------------------|
| `validate` | No |
| `init-session` | No |
| `inspect-session` | No |
| API (`uvicorn` / `python -m src.main`) | Yes (settings loaded at process start) |
| `play-live` | Yes |
| `init-pack` | Yes |

Offline helpers:

```bash
uv run python -m src.story.cli init-session script_packs/yokai_after_school \
  --database data/story.db --session-id local_demo --seed 17
uv run python -m src.story.cli inspect-session local_demo --database data/story.db
```

## API

| Method | Path | Body / notes |
|--------|------|----------------|
| `GET` | `/health` | `runtime: v2` |
| `POST` | `/api/v2/sessions` | `{ "pack_id", "session_seed" }` → 201, public session projection |
| `GET` | `/api/v2/sessions/{session_id}` | public session projection |
| `GET` | `/api/v2/packs/{pack_id}` | public pack metadata projection |
| `POST` | `/api/v2/sessions/{session_id}/turns` | `{ "expected_revision", "idempotency_key", "choice_id" }` — SSE |

`/turns` is the **only** production mutation interface (the legacy `/advance` and `/choices/{id}` routes are removed). Every mutation requires an `idempotency_key`; replaying a completed command with the same key returns the stored result without new events. `choice_id` semantics:

- `choice_id: null` on a fresh session — generate the opening segment;
- `choice_id: <offered id>` — commit that Choice Meaning, then generate its consequence;
- `choice_id: null` while a consequence is pending — resume exactly the one pending consequence (`pending_consequence_status: "awaiting_resolution"` in the projection), never re-offer the old choices.

SSE event types: `segment_started`, `block`, `segment_ready`, `heartbeat`, `error`, `retry_after`. Only `segment_ready` marks an atomically committed, playable segment; failure emits an `error` frame (`generation_unavailable`, `revision_conflict`, `decision_required`, `invalid_choice`, …) and the durable GET projection shows exactly what happened: nothing committed, or a Pending Consequence awaiting recovery.

`GET` responses are safe public projections: internal state (fact truth values, character knowledge, beliefs, suspicions, goals, seeds, pack hashes) is never exposed.

Common error codes in `detail.code`: `pack_not_found`, `session_not_found`, `invalid_choice`, `command_conflict`, `invalid_script_pack`, `model_provider_unavailable`, `generation_unavailable`.

`generation_unavailable` (503) means the real model could not produce a valid, committable segment; if the turn had already committed its Choice Meaning, the session now has a durable Pending Consequence and the request is safe to retry with `choice_id: null` (same or new key — a stable consequence command is shared across retries and appends at most once). `model_provider_unavailable` (503) covers provider outages and is equally non-committing. No default-success result, placeholder story, speculative block, or incomplete segment is ever committed or made playable.

Swagger: `http://127.0.0.1:8000/docs`

## Project structure

```text
backend/
├── src/
│   ├── main.py                 # app = create_app()
│   └── story/
│       ├── api.py              # REST surface (sessions, projections, POST /turns)
│       ├── cli.py              # validate / init-session / inspect-session / play-live / init-pack
│       ├── conditions.py
│       ├── projection.py        # public pack/session projections
│       ├── runtime/            # config, model, planner, segment writer, validator, orchestrator, …
│       ├── script_pack/
│       ├── state/
│       └── storage/
├── script_packs/
│   └── yokai_after_school/pack.yaml
├── tests/
│   └── live/                   # opt-in; RUN_LIVE_ZEN_TEST=1
├── .env.example
├── pyproject.toml
└── uv.lock
```

## Script packs

```text
script_packs/<pack_id>/pack.yaml
```

Validate:

```bash
uv run python -m src.story.cli validate script_packs/yokai_after_school
```

Expect JSON with `pack_id`, `pack_hash`, character/fact/goal counts, and `completion_requirements` (v2 packs).

`init-pack script_packs/yokai_after_school` (model key required) generates and caches only the validated opening segment under `data/pack_cache/<pack_hash>/`; `play-live` and the HTTP server reuse it to start instantly. It never pre-generates choices or writes consequences — the authoritative `/turns` flow is the only way a consequence can be committed.

## Development

```bash
# offline suite (live tests skipped by default)
uv run pytest tests/ -q

# scoped lint
uv run ruff check src/story src/main.py tests

# live capability (rotated key required)
RUN_LIVE_ZEN_TEST=1 uv run pytest -m live tests/live/test_everygpt_chat_runtime.py -v
```

The live command reads the ignored `backend/.env` with `override=False`, so an explicitly exported CI secret wins. If the provider answers slowly, raise the timeout (default 45s):

```bash
cd backend
RUN_LIVE_ZEN_TEST=1 GAL_LLM_TIMEOUT_SECONDS=120 uv run pytest -m live tests/live/test_everygpt_chat_runtime.py -v
```

The live test drives the real model through the strict planner/writer contract; the model is nondeterministic, so an occasional run fails closed (`ProposalRejected`) or times out without committing anything — a pass proves the roundtrip works, and the session stays unmodified on failure.

## License

MIT
