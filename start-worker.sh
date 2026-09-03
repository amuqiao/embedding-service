#!/bin/sh
set -e

ROOT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
WORKER_LOGLEVEL="${WORKER_LOGLEVEL:-INFO}"
WORKER_PROCESSES="${WORKER_PROCESSES:-1}"
WORKER_MAX_ASYNC_TASKS="${WORKER_MAX_ASYNC_TASKS:-1}"
WORKER_MAX_PREFETCH="${WORKER_MAX_PREFETCH:-1}"

require_positive_int() {
  name="$1"
  value="$2"
  case "$value" in
    ''|*[!0-9]*)
      echo "ERROR: $name must be a positive integer" >&2
      exit 2
      ;;
  esac
  if [ "$value" -lt 1 ]; then
    echo "ERROR: $name must be a positive integer" >&2
    exit 2
  fi
}

require_positive_int WORKER_PROCESSES "$WORKER_PROCESSES"
require_positive_int WORKER_MAX_ASYNC_TASKS "$WORKER_MAX_ASYNC_TASKS"
require_positive_int WORKER_MAX_PREFETCH "$WORKER_MAX_PREFETCH"

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
else
  echo "ERROR: python not found; cannot start worker" >&2
  exit 1
fi

cd "$ROOT_DIR"

exec "$PYTHON" -m taskiq worker app.tasks.taskiq_app:broker \
  --log-level "$WORKER_LOGLEVEL" \
  --workers "$WORKER_PROCESSES" \
  --max-async-tasks "$WORKER_MAX_ASYNC_TASKS" \
  --max-prefetch "$WORKER_MAX_PREFETCH"
