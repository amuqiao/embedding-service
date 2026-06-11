#!/bin/sh
set -e
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-4}"
exec celery -A app.tasks.celery_app.celery_app worker \
  --loglevel=info \
  --pool=threads \
  --concurrency="$WORKER_CONCURRENCY" \
  --max-tasks-per-child=100
