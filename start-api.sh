#!/bin/sh
set -e

ROOT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8100}"

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  set -- "$ROOT_DIR/.venv/bin/python" -m uvicorn
else
  set -- python -m uvicorn
fi

cd "$ROOT_DIR"
exec "$@" app.main:app --host "$API_HOST" --port "$API_PORT"
