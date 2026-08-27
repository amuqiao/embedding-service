#!/usr/bin/env bash
# runtime.sh - 本地脚本运行时配置
#
# 维护本地脚本运行时派生值；配置真源是根目录 .env 或显式运行时环境变量。
# 本文件不主动输出；非法值通过 die fail-fast，避免 silent fallback。

RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$RUNTIME_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/lib/common.sh"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
ALEMBIC_BIN="${ALEMBIC_BIN:-$ROOT_DIR/.venv/bin/alembic}"
UVICORN_BIN="${UVICORN_BIN:-$ROOT_DIR/.venv/bin/uvicorn}"

API_HOST="${API_HOST:-$(env_value API_HOST)}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-$(env_value API_PORT)}"
API_PORT="${API_PORT:-8100}"
API_URL="${API_URL:-http://${API_HOST}:${API_PORT}}"
API_DOCS_URL="${API_DOCS_URL:-${API_URL}/docs}"
API_OPENAPI_URL="${API_OPENAPI_URL:-${API_URL}/openapi.json}"
API_HEALTH_URL="${API_HEALTH_URL:-${API_URL}/health}"

POSTGRES_DB="${POSTGRES_DB:-$(env_value POSTGRES_DB)}"
POSTGRES_DB="${POSTGRES_DB:-fastapi_best_ai_architecture}"
POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-$(env_value POSTGRES_HOST_PORT)}"
POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-25432}"
REDIS_HOST_PORT="${REDIS_HOST_PORT:-$(env_value REDIS_HOST_PORT)}"
REDIS_HOST_PORT="${REDIS_HOST_PORT:-26379}"

DEV_API_RELOAD="${DEV_API_RELOAD:-false}"
WATCHFILES_FORCE_POLLING="${WATCHFILES_FORCE_POLLING:-true}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-$(env_value WORKER_CONCURRENCY)}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"
WORKER_LOGLEVEL="${WORKER_LOGLEVEL:-$(env_value WORKER_LOGLEVEL)}"
WORKER_LOGLEVEL="${WORKER_LOGLEVEL:-INFO}"

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
