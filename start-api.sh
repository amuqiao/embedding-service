#!/bin/sh
set -e

ROOT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8100}"

if [ -x "$ROOT_DIR/.venv/bin/uvicorn" ]; then
  UVICORN="$ROOT_DIR/.venv/bin/uvicorn"
else
  UVICORN="uvicorn"
fi

cd "$ROOT_DIR"
exec "$UVICORN" app.main:app --host "$API_HOST" --port "$API_PORT"
