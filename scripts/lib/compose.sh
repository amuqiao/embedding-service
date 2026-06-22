#!/usr/bin/env bash

COMPOSE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$COMPOSE_LIB_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/lib/common.sh"

compose() {
  local compose_project_name
  local env_args=()

  compose_project_name="${COMPOSE_PROJECT_NAME:-${PROJECT_NAME:-}}"
  [[ -n "${ENV_FILE:-}" ]] && env_args+=(ENV_FILE="$ENV_FILE")
  [[ -n "$compose_project_name" ]] && env_args+=(COMPOSE_PROJECT_NAME="$compose_project_name")

  if docker compose version >/dev/null 2>&1; then
    env "${env_args[@]}" docker compose "$@"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    env "${env_args[@]}" docker-compose "$@"
    return
  fi
  die "Docker Compose is not available. Install Docker Desktop or docker-compose." 2
}
