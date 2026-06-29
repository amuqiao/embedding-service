#!/usr/bin/env bash
# services.sh - dev.sh 的本地服务生命周期原子实现
#
# 输出原则：
#   start/stop/restart/status 围绕服务对象输出，不打印后台服务完整日志。
#   启动类副作用必须给出 pid、log、url 或 health 证据。
#   等待超时前只输出相关诊断证据，失败由 die 给出下一步命令。

DEV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$DEV_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/lib/runtime.sh"
source "$ROOT_DIR/scripts/lib/compose.sh"
source "$ROOT_DIR/scripts/lib/modes.sh"

APP_SERVICES=(api worker)
DEP_SERVICES=(postgres redis)

mkdir -p "$RUN_DIR" "$LOG_DIR"
cd "$ROOT_DIR"

service_pid_file() {
  mode_pid_file "$1"
}

service_log_file() {
  mode_log_file "$1"
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

app_env_value() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "$value" ]]; then
    printf "%s" "$value"
  else
    env_value "$key"
  fi
}

bool_true() {
  case "$1" in
    true|True|TRUE) return 0 ;;
    *) return 1 ;;
  esac
}

is_loopback_host() {
  case "$1" in
    127.0.0.1|localhost|::1) return 0 ;;
    *) return 1 ;;
  esac
}

guard_api_insecure_header_flags() {
  if bool_true "$(app_env_value DISABLE_HTTP_AUTH_HEADER)" || bool_true "$(app_env_value DISABLE_CALLER_ID_HEADER)"; then
    is_loopback_host "$API_HOST" || die "API_HOST must be 127.0.0.1, localhost, or ::1 when auth header disable flags are enabled" 2
  fi
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
  mode_pid_of "$1"
}

is_running_pid_file() {
  local pid_file="$1"
  local pid
  pid="$(pid_of "$pid_file")"
  mode_pid_running "$pid"
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

  # 健康检查成功只输出 READY；失败时再透传 compose ps 作为相关证据。
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

extract_url_port() {
  local url="$1"
  "$PYTHON_BIN" -c 'from urllib.parse import urlparse; import sys; print(urlparse(sys.argv[1]).port or "")' "$url"
}

extract_database_name() {
  local url="$1"
  "$PYTHON_BIN" -c 'from urllib.parse import urlparse, unquote; import sys; print(unquote(urlparse(sys.argv[1]).path).lstrip("/"))' "$url"
}

assert_local_config_consistency() {
  local database_url redis_url database_name database_port redis_port

  require_project_python
  database_url="$(env_value DATABASE_URL)"
  redis_url="$(env_value REDIS_URL)"
  [[ -n "$database_url" ]] || return 0
  [[ -n "$redis_url" ]] || return 0

  database_name="$(extract_database_name "$database_url")"
  database_port="$(extract_url_port "$database_url")"
  redis_port="$(extract_url_port "$redis_url")"

  [[ "$database_name" == "$POSTGRES_DB" ]] ||
    die "DATABASE_URL database (${database_name}) must match POSTGRES_DB (${POSTGRES_DB})" 2
  [[ "$database_port" == "$POSTGRES_HOST_PORT" ]] ||
    die "DATABASE_URL port (${database_port}) must match POSTGRES_HOST_PORT (${POSTGRES_HOST_PORT})" 2
  [[ "$redis_port" == "$REDIS_HOST_PORT" ]] ||
    die "REDIS_URL port (${redis_port}) must match REDIS_HOST_PORT (${REDIS_HOST_PORT})" 2
}

assert_compose_port_mapping() {
  local service="$1"
  local host_port="$2"
  local container_port="$3"
  local ports

  ports="$(compose ps "$service" --format '{{.Ports}}' 2>/dev/null || true)"
  case "$ports" in
    *":${host_port}->${container_port}/tcp"*) return 0 ;;
  esac
  die "${service} compose port mapping must include ${host_port}->${container_port}; current ports=${ports:-none}. Recreate the service with ./scripts/dev.sh stop && docker compose up -d --force-recreate ${service}" 4
}

wait_for_api() {
  local timeout_seconds="$1"
  local elapsed=0

  # API ready 是 start 的成功标准；超时时只展示最近 API 日志并给 logs 入口。
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

  # bootstrap 有文件写入副作用，必须说明 created/existing 和来源模板。
  if [[ -f "$ROOT_DIR/.env" ]]; then
    event "EXISTS" ".env" "kept"
  else
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    event "CREATED" ".env" "from .env.example"
  fi

  uv sync
}

start_dependencies() {
  section "Dependencies"
  compose up -d "${DEP_SERVICES[@]}"
  wait_for_container_health postgres 90
  wait_for_container_health redis 60
  assert_compose_port_mapping postgres "$POSTGRES_HOST_PORT" "5432"
  assert_compose_port_mapping redis "$REDIS_HOST_PORT" "6379"
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
  assert_no_compose_full_app_running_for_local
  guard_local_env
  assert_local_config_consistency
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
    guard_api_insecure_header_flags
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
    # 重复 start 不视为失败；输出 RUNNING 和当前 pid，便于用户判断状态。
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
  local residual_pids

  require_app_service "$service"
  pid_file="$(service_pid_file "$service")"
  pid="$(pid_of "$pid_file")"

  if [[ -z "$pid" ]]; then
    residual_pids="$(local_service_pids "$service")" || return "$?"
    residual_pids="${residual_pids//$'\n'/,}"
    if [[ -n "$residual_pids" ]]; then
      die "$service residual local processes are still running: pid=${residual_pids}. Stop them manually before continuing." 4
    else
      event "STOPPED" "$service" "already stopped"
      return
    fi
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    event "STALE" "$service" "removed pid=$pid"
    rm -f "$pid_file"
    residual_pids="$(local_service_pids "$service")" || return "$?"
    residual_pids="${residual_pids//$'\n'/,}"
    if [[ -n "$residual_pids" ]]; then
      die "$service residual local processes are still running: pid=${residual_pids}. Stop them manually before continuing." 4
    else
      return
    fi
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
  residual_pids="$(local_service_pids "$service")" || return "$?"
  residual_pids="${residual_pids//$'\n'/,}"
  if [[ -n "$residual_pids" ]]; then
    die "$service residual local processes are still running after stop: pid=${residual_pids}. Stop them manually before continuing." 4
  fi
  event "STOPPED" "$service" ""
}

start_all() {
  assert_no_compose_full_app_running_for_local
  guard_local_env
  assert_local_config_consistency
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
  local residual_pids

  require_app_service "$service"
  pid_file="$(service_pid_file "$service")"
  log_file="$(service_log_file "$service")"
  display_log="${log_file#$ROOT_DIR/}"
  pid="$(pid_of "$pid_file")"
  residual_pids="$(local_service_pids "$service")" || return "$?"
  residual_pids="${residual_pids//$'\n'/,}"

  if [[ -z "$pid" ]]; then
    if [[ -n "$residual_pids" ]]; then
      state="residual"
      summary="pid=$residual_pids"
    else
      state="stopped"
      summary="pid=-"
    fi
  elif kill -0 "$pid" 2>/dev/null; then
    state="running"
    summary="pid=$pid"
  else
    if [[ -n "$residual_pids" ]]; then
      state="residual"
      summary="pid=$residual_pids stale_pid=$pid"
    else
      state="stale"
      summary="pid=$pid"
    fi
  fi

  # status 使用 row/detail：一行状态摘要加 URL、health、log 等可复制证据。
  row "$service" "$state" "$summary"
  if [[ "$service" == "api" ]]; then
    detail "app" "$API_URL"
    detail "docs" "$API_DOCS_URL"
    detail "openapi" "$API_OPENAPI_URL"
    detail "health" "$API_HEALTH_URL"
    detail "log" "$display_log"
  else
    detail "concurrency" "$WORKER_CONCURRENCY"
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
  warn_if_compose_full_app_running
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
  assert_no_compose_full_app_running_for_local
  guard_local_env
  assert_local_config_consistency
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
