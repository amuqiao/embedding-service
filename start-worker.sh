#!/bin/sh
set -e

ROOT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
WORKER_LOGLEVEL="${WORKER_LOGLEVEL:-INFO}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"
WORKER_RECOVERY_LOOP="${WORKER_RECOVERY_LOOP:-true}"

if [ -x "$ROOT_DIR/.venv/bin/taskiq" ]; then
  TASKIQ="$ROOT_DIR/.venv/bin/taskiq"
else
  TASKIQ="taskiq"
fi

cd "$ROOT_DIR"

RECOVERY_PID=""
TASKIQ_PID=""

cleanup() {
  if [ -n "$TASKIQ_PID" ]; then
    kill "$TASKIQ_PID" 2>/dev/null || true
  fi
  if [ -n "$RECOVERY_PID" ]; then
    kill "$RECOVERY_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

if [ "$WORKER_RECOVERY_LOOP" = "true" ]; then
  "$ROOT_DIR/.venv/bin/python" -m app.tasks.recovery_loop &
  RECOVERY_PID="$!"
fi

"$TASKIQ" worker app.tasks.taskiq_app:broker \
  --log-level "$WORKER_LOGLEVEL" \
  --workers "$WORKER_CONCURRENCY" &
TASKIQ_PID="$!"

wait "$TASKIQ_PID"
