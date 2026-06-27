#!/bin/sh
set -e

ROOT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8200}"

env_or_dotenv_value() {
  key="$1"
  value="$(printenv "$key" 2>/dev/null || true)"
  if [ -n "$value" ]; then
    printf "%s" "$value"
    return
  fi
  env_file="${ENV_FILE:-.env}"
  case "$env_file" in
    /*) ;;
    *) env_file="$ROOT_DIR/$env_file" ;;
  esac
  if [ -f "$env_file" ]; then
    grep -E "^${key}=" "$env_file" 2>/dev/null | tail -n 1 | cut -d= -f2-
  fi
}

flag_true() {
  case "$1" in
    true|True|TRUE) return 0 ;;
    *) return 1 ;;
  esac
}

loopback_host() {
  case "$1" in
    127.0.0.1|localhost|::1) return 0 ;;
    *) return 1 ;;
  esac
}

if flag_true "$(env_or_dotenv_value DISABLE_HTTP_AUTH_HEADER)" || flag_true "$(env_or_dotenv_value DISABLE_CALLER_ID_HEADER)"; then
  if ! loopback_host "$API_HOST"; then
    echo "ERROR: API_HOST must be 127.0.0.1, localhost, or ::1 when auth header disable flags are enabled" >&2
    exit 2
  fi
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  set -- "$ROOT_DIR/.venv/bin/python" -m uvicorn
else
  set -- python -m uvicorn
fi

cd "$ROOT_DIR"
exec "$@" app.main:app --host "$API_HOST" --port "$API_PORT"
