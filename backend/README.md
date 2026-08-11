# Galgame AI Backend (V2)

V2-only FastAPI + CLI runtime. Script packs compile offline; live scene generation uses OpenCode Go **Responses** (`deepseek-v4-flash`) via the OpenAI Agents SDK (`OpenAIResponsesModel`).

## Features

- Event-sourced sessions (`StoryEventStore`, revisioned append)
- Shared Planner / Writer model bundle (Responses API only)
- Validator + Simulator gate model proposals before reducer commits
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
GAL_LLM_PROVIDER=opencode_go
OPENCODE_GO_API_KEY=
OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1
GAL_LLM_MODEL=deepseek-v4-flash
GAL_LLM_API=responses
GAL_LLM_TIMEOUT_SECONDS=45
GAL_LLM_MAX_RETRIES=1
```

**Security:** any key previously exposed in chat must be revoked. Never put a real key in `.env.example`. `OPENAI_API_KEY` is an optional equal-value alias for `OPENCODE_GO_API_KEY`.

## Commands

```bash
cd backend
uv sync --extra dev
uv run python -m src.story.cli validate script_packs/cafe_mystery
uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
uv run python -m src.story.cli play-live script_packs/cafe_mystery \
  --database data/live.db --session-id demo --seed 17
```

| Command | Needs model key? |
|---------|------------------|
| `validate` | No |
| `init-session` | No |
| `inspect-session` | No |
| API (`uvicorn` / `python -m src.main`) | Yes (settings loaded at process start) |
| `play-live` | Yes |

Offline helpers:

```bash
uv run python -m src.story.cli init-session script_packs/cafe_mystery \
  --database data/story.db --session-id local_demo --seed 17
uv run python -m src.story.cli inspect-session local_demo --database data/story.db
```

## API

| Method | Path | Body / notes |
|--------|------|----------------|
| `GET` | `/health` | `runtime: v2` |
| `POST` | `/api/v2/sessions` | `{ "pack_id", "session_seed" }` → 201 |
| `GET` | `/api/v2/sessions/{session_id}` | snapshot |
| `POST` | `/api/v2/sessions/{session_id}/advance` | `{ "expected_revision" }` |
| `POST` | `/api/v2/sessions/{session_id}/choices/{choice_id}` | `{ "expected_revision", "idempotency_key" }` |

Common error codes in `detail.code`: `pack_not_found`, `session_not_found`, `invalid_choice`, `command_conflict`, `invalid_script_pack`, `model_provider_unavailable`.

Swagger: `http://127.0.0.1:8000/docs`

## Project structure

```text
backend/
├── src/
│   ├── main.py                 # app = create_app()
│   └── story/
│       ├── api.py              # REST surface
│       ├── cli.py
│       ├── conditions.py
│       ├── runtime/            # config, model, planner, writer, validator, …
│       ├── script_pack/
│       ├── state/
│       └── storage/
├── script_packs/
│   └── cafe_mystery/pack.yaml
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
uv run python -m src.story.cli validate script_packs/cafe_mystery
```

Expect JSON with `pack_id`, `pack_hash`, character/fact/goal counts, and ending tallies (`normal_endings` ≥ 3 and `fallback_endings` ≥ 1 for `cafe_mystery`).

## Development

```bash
# offline suite (live tests skipped by default)
uv run pytest tests/ -q

# scoped lint
uv run ruff check src/story src/main.py tests

# live capability (rotated key required)
RUN_LIVE_ZEN_TEST=1 uv run pytest -m live tests/live/test_opencode_go_v2_runtime.py -v
```

The live command reads the ignored `backend/.env` with `override=False`, so an explicitly exported CI secret wins.

## License

MIT
