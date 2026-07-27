#!/usr/bin/env bash
# modes.sh - local / compose-full runtime boundary checks
#
# These helpers make script ownership explicit. They never stop processes
# implicitly; conflicting runtime modes fail fast with a command the user can run.

MODES_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$MODES_LIB_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/lib/common.sh"
source "$ROOT_DIR/scripts/lib/compose.sh"

mode_pid_file() {
  case "$1" in
    api) printf "%s/api.pid" "$RUN_DIR" ;;
    worker) printf "%s/worker.pid" "$RUN_DIR" ;;
    *) die "unknown service: $1" 2 ;;
  esac
}

mode_log_file() {
  case "$1" in
    api) printf "%s/api.log" "$LOG_DIR" ;;
    worker) printf "%s/worker.log" "$LOG_DIR" ;;
    *) die "unknown service: $1" 2 ;;
  esac
}

mode_pid_of() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] && cat "$pid_file" 2>/dev/null || true
}

mode_pid_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

log_writer_pids() {
  local log_file="$1"
  local lsof_output
  [[ -f "$log_file" ]] || return 0
  command -v lsof >/dev/null 2>&1 || die "lsof is required for local process residual detection" 2
  lsof_output="$(lsof -nP "$log_file" 2>/dev/null || true)"
  [[ -n "$lsof_output" ]] || return 0
  printf "%s\n" "$lsof_output" | awk 'NR > 1 && $4 ~ /w/ && !seen[$2]++ {print $2}'
}

pid_in_lines() {
  local needle="$1"
  local lines="$2"
  case "$lines" in
    "$needle"|"$needle"$'\n'*|*$'\n'"$needle"|*$'\n'"$needle"$'\n'*) return 0 ;;
    *) return 1 ;;
  esac
}

canonical_existing_dir() {
  local path="$1"
  if [[ -d "$path" ]]; then
    (cd "$path" >/dev/null 2>&1 && pwd -P) || printf "%s" "$path"
    return
  fi
  printf "%s" "$path"
}

assert_no_compose_project_name_conflict() {
  local project_name
  local current_working_dir
  local working_dirs
  local working_dir
  local normalized_working_dir
  local conflicting_working_dirs=""

  project_name="$(compose_project_name)"
  current_working_dir="$(canonical_existing_dir "$ROOT_DIR")"
  command -v docker >/dev/null 2>&1 || die "docker is required for COMPOSE_PROJECT_NAME conflict check" 2
  working_dirs="$(docker ps -a \
    --filter "label=com.docker.compose.project=$project_name" \
    --format '{{.Label "com.docker.compose.project.working_dir"}}')" \
    || die "docker ps failed while checking COMPOSE_PROJECT_NAME conflict" 2

  while IFS= read -r working_dir; do
    [[ -n "$working_dir" ]] || continue
    normalized_working_dir="$(canonical_existing_dir "$working_dir")"
    [[ "$normalized_working_dir" != "$current_working_dir" ]] || continue

    if [[ -z "$conflicting_working_dirs" ]]; then
      conflicting_working_dirs="$working_dir"
    elif ! pid_in_lines "$working_dir" "$conflicting_working_dirs"; then
      conflicting_working_dirs="${conflicting_working_dirs}"$'\n'"${working_dir}"
    fi
  done <<< "$working_dirs"

  [[ -z "$conflicting_working_dirs" ]] && return 0

  die "COMPOSE_PROJECT_NAME conflict: project name '$project_name' has existing working_dir '${conflicting_working_dirs//$'\n'/, }' but current ROOT_DIR is '$ROOT_DIR'" 4
}

local_service_pids() {
  local service="$1"
  local pid_file
  local log_file
  local pid
  local writer_pids
  local writer_pid
  local pids=""

  pid_file="$(mode_pid_file "$service")"
  log_file="$(mode_log_file "$service")"
  pid="$(mode_pid_of "$pid_file")"

  if mode_pid_running "$pid"; then
    pids="$pid"
  fi

  writer_pids="$(log_writer_pids "$log_file")" || return "$?"
  while IFS= read -r writer_pid; do
    [[ -n "$writer_pid" ]] || continue
    if [[ -z "$pids" ]]; then
      pids="$writer_pid"
    elif ! pid_in_lines "$writer_pid" "$pids"; then
      pids="${pids}"$'\n'"${writer_pid}"
    fi
  done <<< "$writer_pids"

  [[ -n "$pids" ]] && printf "%s\n" "$pids"
  return 0
}

local_app_running_summary() {
  local service
  local pids
  for service in api worker; do
    pids="$(local_service_pids "$service")" || return "$?"
    pids="${pids//$'\n'/,}"
    [[ -n "$pids" ]] && printf "%s pid=%s\n" "$service" "$pids"
  done
  return 0
}

assert_no_local_app_running_for_compose_full() {
  local summary
  summary="$(local_app_running_summary)" || return "$?"
  [[ -z "$summary" ]] && return 0

  die "local app processes are running: ${summary//$'\n'/; }. Stop them before compose-full with: ./scripts/run.sh down dev" 4
}

warn_if_local_app_running() {
  local summary
  summary="$(local_app_running_summary)" || return "$?"
  [[ -z "$summary" ]] && return 0

  section "Mode Guard"
  event "WARN" "local" "${summary//$'\n'/; }; compose-full and local app must not run together"
}

compose_app_running_services() {
  local service
  local state
  local names
  local services=""

  if ! compose_available; then
    return 0
  fi

  for service in api worker; do
    names="$(docker ps \
      --filter "label=com.docker.compose.project.working_dir=$ROOT_DIR" \
      --filter "label=com.docker.compose.service=$service" \
      --format '{{.Names}}' 2>/dev/null || true)"
    if [[ -n "$names" ]] && ! pid_in_lines "$service" "$services"; then
      services="${services:+${services}$'\n'}${service}"
      continue
    fi

    state="$(compose --profile app ps "$service" --format '{{.State}}' 2>/dev/null | head -n 1 || true)"
    case "$state" in
      running|*running*)
        if ! pid_in_lines "$service" "$services"; then
          services="${services:+${services}$'\n'}${service}"
        fi
        ;;
    esac
  done
  [[ -n "$services" ]] && printf "%s\n" "$services"
  return 0
}

assert_no_compose_full_app_running_for_local() {
  local services
  services="$(compose_app_running_services | paste -sd, -)"
  [[ -z "$services" ]] && return 0

  die "compose-full app services are running: ${services}. Stop them before local dev with: ./scripts/deploy.sh down compose-full" 4
}

warn_if_compose_full_app_running() {
  local services
  services="$(compose_app_running_services | paste -sd, -)"
  [[ -z "$services" ]] && return 0

  section "Mode Guard"
  event "WARN" "compose-full" "app services running: ${services}; run ./scripts/deploy.sh down compose-full before ./scripts/run.sh up dev"
}
