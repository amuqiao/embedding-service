#!/bin/sh
set -e

WORKER_LOGLEVEL="${WORKER_LOGLEVEL:-info}"
WORKER_POOL="${WORKER_POOL:-solo}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"

set -- -A app.tasks.celery_app.celery_app worker \
  --loglevel="$WORKER_LOGLEVEL" \
  --pool="$WORKER_POOL" \
  --concurrency="$WORKER_CONCURRENCY"

if [ -n "${WORKER_MAX_TASKS_PER_CHILD:-}" ]; then
  set -- "$@" --max-tasks-per-child="$WORKER_MAX_TASKS_PER_CHILD"
fi

exec celery "$@"
