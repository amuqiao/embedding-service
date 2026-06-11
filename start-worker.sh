#!/bin/sh
set -e

WORKER_LOGLEVEL="${WORKER_LOGLEVEL:-info}"
WORKER_POOL="${WORKER_POOL:-solo}"

set -- -A app.tasks.celery_app.celery_app worker \
  --loglevel="$WORKER_LOGLEVEL" \
  --pool="$WORKER_POOL"

# solo 模式不支持并发，--concurrency 仅对 threads/prefork 有效
if [ "$WORKER_POOL" != "solo" ] && [ -n "${WORKER_CONCURRENCY:-}" ]; then
  set -- "$@" --concurrency="$WORKER_CONCURRENCY"
fi

if [ -n "${WORKER_MAX_TASKS_PER_CHILD:-}" ]; then
  set -- "$@" --max-tasks-per-child="$WORKER_MAX_TASKS_PER_CHILD"
fi

exec celery "$@"
