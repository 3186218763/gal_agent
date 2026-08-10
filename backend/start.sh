#!/usr/bin/env bash
# V2 API process entry (uv + uvicorn). Mirrors backend/README without embedding secrets.
set -euo pipefail

if [[ ! -f "pyproject.toml" || ! -f "uv.lock" ]]; then
  echo "error: run this script from the backend/ directory" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required (https://docs.astral.sh/uv/)" >&2
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo "error: missing .env — copy .env.example and set OPENCODE_GO_API_KEY (or OPENAI_API_KEY alias)" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

if [[ -z "${OPENCODE_GO_API_KEY:-}${OPENAI_API_KEY:-}" ]]; then
  echo "error: set OPENCODE_GO_API_KEY (or OPENAI_API_KEY) in .env" >&2
  exit 1
fi

export GAL_LLM_PROVIDER="${GAL_LLM_PROVIDER:-opencode_go}"
export GAL_LLM_API="${GAL_LLM_API:-responses}"
export GAL_LLM_MODEL="${GAL_LLM_MODEL:-deepseek-v4-flash}"
export GAL_DATABASE_PATH="${GAL_DATABASE_PATH:-data/story-v2.db}"
export GAL_SCRIPT_PACK_ROOT="${GAL_SCRIPT_PACK_ROOT:-script_packs}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"

echo "syncing dependencies with uv..."
uv sync --extra dev

mkdir -p "$(dirname "${GAL_DATABASE_PATH}")"

echo "starting V2 API on ${HOST}:${PORT}..."
exec uv run uvicorn src.main:app --host "${HOST}" --port "${PORT}" --reload
