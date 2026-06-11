#!/bin/sh
set -e

API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8100}"

exec uvicorn app.main:app --host "$API_HOST" --port "$API_PORT"
