#!/usr/bin/env bash

COMPOSE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$COMPOSE_LIB_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/lib/common.sh"

compose() {
  local env_file
  local template_name
  local compose_project_name
  local env_args=()

  env_file="$(resolve_repo_path "${ENV_FILE:-.env}")"
  template_name="${TEMPLATE_NAME:-$(env_value_from TEMPLATE_NAME "$env_file")}"
  template_name="${template_name:-fastapi-best-ai-architecture}"
  compose_project_name="${COMPOSE_PROJECT_NAME:-${PROJECT_NAME:-$template_name}}"
  [[ -n "${ENV_FILE:-}" ]] && env_args+=(ENV_FILE="$ENV_FILE")
  env_args+=(COMPOSE_PROJECT_NAME="$compose_project_name")

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
