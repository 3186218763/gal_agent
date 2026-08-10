# Galgame AI — V2 Runtime (DeepSeek Responses)

Constrained dynamic galgame engine. Authors write a **script pack** (`pack.yaml`); the V2 runtime drives scenes through a shared Planner/Writer model pair, with Validator, Simulator, reducer, and EventStore as the only path that mutates session state.

Players choose only backend-presented choice IDs. No free text, no rewind, multi-ending.

## Architecture

```text
Script Pack (pack.yaml)
        |
        v
compile_script_pack → CompiledScriptPack
        |
        +---- offline: validate / init-session / inspect-session
        |
        v  (requires OpenCode Go model config)
RuntimeService
├── ContextAssembler
├── Planner  (OpenAIResponsesModel / deepseek-v4-flash)
├── Validator + Simulator
├── Writer   (same model bundle)
├── Ending evaluator + fallbacks
└── StoryEventStore (SQLite, revisioned append-only)
        |
        v
REST API  /api/v2/sessions...
CLI       play-live
```

Design docs:

- [V2 runtime + DeepSeek cutover design](docs/superpowers/specs/2026-08-10-v2-runtime-deepseek-cutover-design.md)
- [Constrained dynamic galgame design](docs/superpowers/specs/2026-08-10-constrained-dynamic-galgame-design.md)
- [Implementation plan](docs/superpowers/plans/2026-08-10-v2-runtime-deepseek-responses-cutover.md)

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

### Frontend shell

The React shell is intentionally disconnected from V1 WebSocket/session clients. It builds as a static “V2 Runtime 尚未连接” status page until a V2 client is wired.

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
│   │       ├── api.py           # /api/v2 REST
│   │       ├── cli.py           # validate / init / inspect / play-live
│   │       ├── conditions.py
│   │       ├── runtime/         # Planner, Writer, Validator, Simulator, service
│   │       ├── script_pack/     # pack models + compiler
│   │       ├── state/           # events, session models, reducer
│   │       └── storage/         # StoryEventStore
│   ├── script_packs/
│   │   └── cafe_mystery/
│   ├── tests/                   # offline suite + tests/live/ (opt-in)
│   └── .env.example
├── frontend/                    # Vite React shell (no V1 game client)
├── docs/superpowers/
└── README.md
```

## HTTP API (V2)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | `{"status":"ok","runtime":"v2"}` |
| `POST` | `/api/v2/sessions` | Create session (`pack_id`, `session_seed`) |
| `GET` | `/api/v2/sessions/{id}` | Load session snapshot |
| `POST` | `/api/v2/sessions/{id}/advance` | Generate next scene (`expected_revision`) |
| `POST` | `/api/v2/sessions/{id}/choices/{choice_id}` | Apply presented choice (`expected_revision`, `idempotency_key`) |

OpenAPI: `http://127.0.0.1:8000/docs` when the server is running.

There is no `/api/sessions` or WebSocket game channel.

## Testing

```bash
cd backend
uv run pytest tests/ -q
```

Default suite is offline. Live network tests are skipped unless:

```bash
RUN_LIVE_ZEN_TEST=1 GAL_LLM_PROVIDER=opencode_go \
  uv run pytest -m live tests/live/test_opencode_go_v2_runtime.py -v
```

## License

MIT
