#!/usr/bin/env bash

RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$RUNTIME_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/lib/common.sh"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
ALEMBIC_BIN="${ALEMBIC_BIN:-$ROOT_DIR/.venv/bin/alembic}"
UVICORN_BIN="${UVICORN_BIN:-$ROOT_DIR/.venv/bin/uvicorn}"

API_HOST="${API_HOST:-$(script_env_value API_HOST)}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-$(script_env_value API_PORT)}"
API_PORT="${API_PORT:-8100}"
API_URL="${API_URL:-http://${API_HOST}:${API_PORT}}"
API_DOCS_URL="${API_DOCS_URL:-${API_URL}/docs}"
API_OPENAPI_URL="${API_OPENAPI_URL:-${API_URL}/openapi.json}"
API_HEALTH_URL="${API_HEALTH_URL:-${API_URL}/health}"

DEV_API_RELOAD="${DEV_API_RELOAD:-false}"
WATCHFILES_FORCE_POLLING="${WATCHFILES_FORCE_POLLING:-true}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-$(script_env_value WORKER_CONCURRENCY)}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"
WORKER_LOGLEVEL="${WORKER_LOGLEVEL:-$(script_env_value WORKER_LOGLEVEL)}"
WORKER_LOGLEVEL="${WORKER_LOGLEVEL:-INFO}"
WORKER_RECOVERY_LOOP="${WORKER_RECOVERY_LOOP:-$(script_env_value WORKER_RECOVERY_LOOP)}"
WORKER_RECOVERY_LOOP="${WORKER_RECOVERY_LOOP:-true}"

require_project_python() {
  require_executable "$PYTHON_BIN" "run: ./scripts/dev.sh bootstrap"
}

bool_enabled() {
  local name="$1"
  local value="$2"
  case "$value" in
    true|True|TRUE) return 0 ;;
    false|False|FALSE) return 1 ;;
    *) die "$name must be true or false" 2 ;;
  esac
}
