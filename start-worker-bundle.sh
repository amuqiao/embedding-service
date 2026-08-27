#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKER_LOGLEVEL="${WORKER_LOGLEVEL:-INFO}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
else
  echo "ERROR: python not found; cannot start worker bundle" >&2
  exit 1
fi

cd "$ROOT_DIR"

ROLE_NAMES=()
ROLE_PIDS=()
SHUTTING_DOWN=false

start_role() {
  local name="$1"
  local pid
  shift
  "$@" &
  pid="$!"
  ROLE_NAMES+=("$name")
  ROLE_PIDS+=("$pid")
  echo "started ${name} pid=${pid}"
}

stop_children() {
  local pid
  for pid in "${ROLE_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${ROLE_PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
}

shutdown() {
  SHUTTING_DOWN=true
  stop_children
}

trap 'shutdown; exit 143' INT TERM
trap 'if [[ "$SHUTTING_DOWN" != "true" ]]; then shutdown; fi' EXIT

start_role dispatcher "$PYTHON" -m app.runtime.dispatcher loop
start_role callbacker "$PYTHON" -m app.runtime.callbacker loop
start_role reconciler "$PYTHON" -m app.runtime.reconciler loop
start_role taskiq-worker "$PYTHON" -m taskiq worker app.tasks.taskiq_app:broker \
  --log-level "$WORKER_LOGLEVEL" \
  --workers "$WORKER_CONCURRENCY"

while true; do
  for i in "${!ROLE_PIDS[@]}"; do
    pid="${ROLE_PIDS[$i]}"
    if ! kill -0 "$pid" 2>/dev/null; then
      status=0
      wait "$pid" || status="$?"
      echo "role ${ROLE_NAMES[$i]} exited status=${status}" >&2
      SHUTTING_DOWN=true
      stop_children
      if [[ "$status" -eq 0 ]]; then
        exit 1
      fi
      exit "$status"
    fi
  done
  sleep 1
done
