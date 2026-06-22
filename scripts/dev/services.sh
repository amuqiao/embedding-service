#!/usr/bin/env bash

DEV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$DEV_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/lib/runtime.sh"
source "$ROOT_DIR/scripts/lib/compose.sh"

APP_SERVICES=(api worker)
DEP_SERVICES=(postgres redis)

mkdir -p "$RUN_DIR" "$LOG_DIR"
cd "$ROOT_DIR"

service_pid_file() {
  case "$1" in
    api) printf "%s/api.pid" "$RUN_DIR" ;;
    worker) printf "%s/worker.pid" "$RUN_DIR" ;;
    *) die "unknown service: $1" 2 ;;
  esac
}

service_log_file() {
  case "$1" in
    api) printf "%s/api.log" "$LOG_DIR" ;;
    worker) printf "%s/worker.log" "$LOG_DIR" ;;
    *) die "unknown service: $1" 2 ;;
  esac
}

service_url() {
  case "$1" in
    api) printf "%s" "$API_URL" ;;
    worker) printf "-" ;;
    *) die "unknown service: $1" 2 ;;
  esac
}

api_reload_enabled() {
  bool_enabled DEV_API_RELOAD "$DEV_API_RELOAD"
}

service_command() {
  case "$1" in
    api)
      if api_reload_enabled; then
        printf "env API_HOST=%q API_PORT=%q WATCHFILES_FORCE_POLLING=%q %q -m uvicorn app.main:app --host %q --port %q --reload --reload-dir %q " \
          "$API_HOST" \
          "$API_PORT" \
          "$WATCHFILES_FORCE_POLLING" \
          "$PYTHON_BIN" \
          "$API_HOST" \
          "$API_PORT" \
          "$ROOT_DIR/app"
      else
        printf "env API_HOST=%q API_PORT=%q %q " "$API_HOST" "$API_PORT" "$ROOT_DIR/start-api.sh"
      fi
      ;;
    worker)
      printf "env WORKER_CONCURRENCY=%q WORKER_LOGLEVEL=%q WORKER_RECOVERY_LOOP=%q %q " \
        "$WORKER_CONCURRENCY" \
        "$WORKER_LOGLEVEL" \
        "$WORKER_RECOVERY_LOOP" \
        "$ROOT_DIR/start-worker.sh"
      ;;
    *)
      die "unknown service: $1" 2
      ;;
  esac
}

is_app_service() {
  local target="$1"
  local service
  for service in "${APP_SERVICES[@]}"; do
    [[ "$service" == "$target" ]] && return 0
  done
  return 1
}

require_app_service() {
  local target="$1"
  is_app_service "$target" || die "unknown service: $target; expected api or worker" 2
}

pid_of() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] && cat "$pid_file" 2>/dev/null || true
}

is_running_pid_file() {
  local pid_file="$1"
  local pid
  pid="$(pid_of "$pid_file")"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

port_owner_pid() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true
  fi
}

assert_api_port_free() {
  local api_pid_file
  local running_pid
  local owner_pid

  api_pid_file="$(service_pid_file api)"
  running_pid="$(pid_of "$api_pid_file")"
  owner_pid="$(port_owner_pid "$API_PORT")"

  [[ -z "$owner_pid" ]] && return 0
  [[ -n "$running_pid" && "$owner_pid" == "$running_pid" ]] && return 0

  die "api port ${API_PORT} is already used by pid=${owner_pid}; stop that process or set API_PORT" 4
}

wait_for_pid_exit() {
  local pid="$1"
  local timeout_seconds="$2"
  local elapsed=0

  while kill -0 "$pid" 2>/dev/null; do
    if (( elapsed >= timeout_seconds )); then
      return 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
}

wait_for_container_health() {
  local service="$1"
  local timeout_seconds="$2"
  local elapsed=0

  while true; do
    if compose ps "$service" 2>/dev/null | grep -qi "healthy"; then
      event "READY" "$service" "healthy"
      return 0
    fi

    if (( elapsed >= timeout_seconds )); then
      compose ps "$service" >&2 || true
      die "$service did not become healthy within ${timeout_seconds}s"
    fi

    sleep 2
    elapsed=$((elapsed + 2))
  done
}

wait_for_api() {
  local timeout_seconds="$1"
  local elapsed=0

  while true; do
    if curl -fsS "$API_HEALTH_URL" >/dev/null 2>&1; then
      event "READY" "api" "$API_HEALTH_URL"
      return 0
    fi

    if (( elapsed >= timeout_seconds )); then
      tail -n 60 "$(service_log_file api)" >&2 2>/dev/null || true
      die "api health check failed after ${timeout_seconds}s; inspect: ./scripts/dev.sh logs api"
    fi

    sleep 1
    elapsed=$((elapsed + 1))
  done
}

bootstrap() {
  section "Bootstrap"
  require_command uv "install uv first"

  if [[ -f "$ROOT_DIR/.env" ]]; then
    event "EXISTS" ".env" "kept"
  else
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    event "CREATED" ".env" "from .env.example"
  fi

  if [[ -f "$ROOT_DIR/scripts/.env" ]]; then
    event "EXISTS" "scripts/.env" "kept"
  else
    cp "$ROOT_DIR/scripts/.env.example" "$ROOT_DIR/scripts/.env"
    event "CREATED" "scripts/.env" "from scripts/.env.example"
  fi

  uv sync
}

start_dependencies() {
  section "Dependencies"
  compose up -d "${DEP_SERVICES[@]}"
  wait_for_container_health postgres 90
  wait_for_container_health redis 60
}

stop_dependencies() {
  section "Dependencies"
  if compose ps "${DEP_SERVICES[@]}" >/dev/null 2>&1; then
    compose stop "${DEP_SERVICES[@]}"
  else
    event "SKIP" "compose" "no local services found"
  fi
}

migrate() {
  guard_local_env
  section "Database"
  require_executable "$ALEMBIC_BIN" "run: ./scripts/dev.sh bootstrap"
  "$ALEMBIC_BIN" upgrade head
}

start_service() {
  local service="$1"
  local pid_file
  local log_file
  local pid
  local command

  require_app_service "$service"
  require_project_python
  if [[ "$service" == "api" ]]; then
    if api_reload_enabled; then
      require_executable "$UVICORN_BIN" "run: ./scripts/dev.sh bootstrap"
    else
      require_executable "$ROOT_DIR/start-api.sh" "missing start-api.sh"
    fi
  fi
  [[ "$service" == "worker" ]] && require_executable "$ROOT_DIR/start-worker.sh" "missing start-worker.sh"

  pid_file="$(service_pid_file "$service")"
  log_file="$(service_log_file "$service")"

  if is_running_pid_file "$pid_file"; then
    event "RUNNING" "$service" "pid=$(pid_of "$pid_file") url=$(service_url "$service")"
    return
  fi

  rm -f "$pid_file"
  [[ "$service" == "api" ]] && assert_api_port_free

  command="$(service_command "$service")"
  nohup bash -c "exec ${command}" > "$log_file" 2>&1 &
  echo $! > "$pid_file"
  pid="$(pid_of "$pid_file")"

  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    tail -n 60 "$log_file" >&2 2>/dev/null || true
    rm -f "$pid_file"
    die "$service failed to stay running; inspect: ./scripts/dev.sh logs $service"
  fi

  event "STARTED" "$service" "pid=$pid log=$log_file url=$(service_url "$service")"
}

start_application() {
  section "Application"
  start_service api
  start_service worker
  wait_for_api 30
}

stop_service() {
  local service="$1"
  local pid_file
  local pid

  require_app_service "$service"
  pid_file="$(service_pid_file "$service")"
  pid="$(pid_of "$pid_file")"

  if [[ -z "$pid" ]]; then
    event "STOPPED" "$service" "already stopped"
    return
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    event "STALE" "$service" "removed pid=$pid"
    rm -f "$pid_file"
    return
  fi

  event "STOPPING" "$service" "pid=$pid"
  kill "$pid" 2>/dev/null || true
  if ! wait_for_pid_exit "$pid" 10; then
    event "KILLING" "$service" "pid=$pid"
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
    if [[ "$service" == "api" ]]; then
      local owner_pid
      owner_pid="$(port_owner_pid "$API_PORT")"
      if [[ -n "$owner_pid" ]]; then
        die "api port ${API_PORT} is still used by pid=${owner_pid} after stopping api" 4
      fi
    fi
  fi
  rm -f "$pid_file"
  event "STOPPED" "$service" ""
}

start_all() {
  guard_local_env
  start_dependencies
  migrate
  start_application
  status_application
}

stop_all() {
  section "Application"
  stop_service api
  stop_service worker
  stop_dependencies
}

status_service() {
  local service="$1"
  local pid_file
  local log_file
  local pid
  local state
  local summary
  local display_log

  require_app_service "$service"
  pid_file="$(service_pid_file "$service")"
  log_file="$(service_log_file "$service")"
  display_log="${log_file#$ROOT_DIR/}"
  pid="$(pid_of "$pid_file")"

  if [[ -z "$pid" ]]; then
    state="stopped"
    summary="pid=-"
  elif kill -0 "$pid" 2>/dev/null; then
    state="running"
    summary="pid=$pid"
  else
    state="stale"
    summary="pid=$pid"
  fi

  row "$service" "$state" "$summary"
  if [[ "$service" == "api" ]]; then
    detail "app" "$API_URL"
    detail "docs" "$API_DOCS_URL"
    detail "openapi" "$API_OPENAPI_URL"
    detail "health" "$API_HEALTH_URL"
    detail "log" "$display_log"
  else
    detail "log" "$display_log"
  fi
}

status_dependencies() {
  local service
  local line
  local name
  local state
  local health
  local ports

  section "Dependencies"
  if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
    row docker "missing" "Docker Compose is unavailable"
    return
  fi

  for service in "${DEP_SERVICES[@]}"; do
    line="$(compose ps "$service" --format '{{.Service}}|{{.State}}|{{.Health}}|{{.Ports}}' 2>/dev/null || true)"
    if [[ -z "$line" ]]; then
      row "$service" "missing" "container not found"
      continue
    fi

    IFS='|' read -r name state health ports <<< "$line"
    [[ -n "$health" ]] || health="-"
    ports="${ports%%, *}"
    row "$name" "$health" "state=$state ports=${ports:-none}"
  done
}

status_application() {
  section "Application"
  status_service api
  status_service worker

  if curl -fsS "$API_URL/health" >/dev/null 2>&1; then
    row health "ok" "$API_HEALTH_URL"
  else
    row health "down" "$API_HEALTH_URL"
  fi
}

follow_logs() {
  local service="${1:-}"
  [[ -n "$service" ]] || die "logs requires service: api or worker" 2
  require_app_service "$service"
  tail -f "$(service_log_file "$service")"
}

start_target() {
  local service="${1:-}"
  if [[ -z "$service" ]]; then
    start_all
    return
  fi
  guard_local_env
  section "Application"
  start_service "$service"
  [[ "$service" == "api" ]] && wait_for_api 30
}

stop_target() {
  local service="${1:-}"
  if [[ -z "$service" ]]; then
    stop_all
    return
  fi
  section "Application"
  stop_service "$service"
}

restart_target() {
  local service="${1:-}"
  if [[ -z "$service" ]]; then
    stop_all
    start_all
    return
  fi
  stop_target "$service"
  start_target "$service"
}

status_target() {
  local service="${1:-}"
  if [[ -z "$service" ]]; then
    status_dependencies
    status_application
    return
  fi
  section "Application"
  status_service "$service"
}

scan_ports() {
  if [[ -x "$PYTHON_BIN" ]]; then
    "$PYTHON_BIN" "$ROOT_DIR/scripts/dev/check_ports.py" "$@"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 "$ROOT_DIR/scripts/dev/check_ports.py" "$@"
    return
  fi
  die "python3 is not available; run ./scripts/dev.sh bootstrap or install Python 3" 2
}
