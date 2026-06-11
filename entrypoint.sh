#!/bin/sh
set -e

ROLE="${APP_ROLE:-api}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-4}"

case "$ROLE" in
  api)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8100
    ;;
  worker)
    exec celery -A app.tasks.celery_app.celery_app worker \
      --loglevel=info \
      --pool=threads \
      --concurrency="$WORKER_CONCURRENCY" \
      --max-tasks-per-child=100
    ;;
  *)
    echo "Unknown APP_ROLE: $ROLE (expected: api | worker)" >&2
    exit 1
    ;;
esac
