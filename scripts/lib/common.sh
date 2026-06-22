#!/usr/bin/env bash

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$COMMON_DIR/../.." && pwd)}"
RUN_DIR="${RUN_DIR:-$ROOT_DIR/.run}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"

section() {
  printf "\n== %s ==\n" "$1"
}

row() {
  printf "  %-14s %-10s %s\n" "$1" "$2" "${3:-}"
}

detail() {
  printf "    %-9s %s\n" "${1}:" "$2"
}

event() {
  printf "%-9s %-10s %s\n" "$1" "$2" "${3:-}"
}

die() {
  printf "ERROR: %s\n" "$1" >&2
  exit "${2:-1}"
}

require_command() {
  local name="$1"
  local hint="$2"
  command -v "$name" >/dev/null 2>&1 || die "$name is not available; $hint" 2
}

require_executable() {
  local path="$1"
  local hint="$2"
  [[ -x "$path" ]] || die "$path not found or not executable; $hint" 2
}

resolve_repo_path() {
  local path="$1"
  case "$path" in
    /*) printf "%s" "$path" ;;
    *) printf "%s/%s" "$ROOT_DIR" "$path" ;;
  esac
}

env_value_from() {
  local key="$1"
  local path="$2"
  [[ -f "$path" ]] || return 0
  grep -E "^${key}=" "$path" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

env_value() {
  local key="$1"
  env_value_from "$key" "$ROOT_DIR/.env"
}

assert_local_url() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  [[ -n "$value" ]] || return 0

  case "$value" in
    *127.0.0.1*|*localhost*|*0.0.0.0*|*//postgres:*|*@postgres:*|*//redis:*|*@redis:*|*host.docker.internal*)
      return 0
      ;;
  esac

  die "$key in .env does not look local: $value" 3
}

guard_local_env() {
  [[ -f "$ROOT_DIR/.env" ]] || die ".env not found; run: ./scripts/dev.sh bootstrap" 2
  assert_local_url DATABASE_URL
  assert_local_url REDIS_URL
}
