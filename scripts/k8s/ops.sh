#!/usr/bin/env bash
# k8s/ops.sh - K8s Pod 内手动运维原子能力
#
# 本文件只承载 k8s.sh 的功能实现；顶层 k8s.sh 负责 help 和命令分发。

K8S_OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$K8S_OPS_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/lib/common.sh"

require_k8s_pod() {
  [[ -n "${KUBERNETES_SERVICE_HOST:-}" ]] || die "k8s.sh must run inside a K8s Pod; KUBERNETES_SERVICE_HOST is not set" 2
}

load_k8s_env_file_defaults() {
  require_k8s_pod
  export_env_file_defaults
}

require_database_url() {
  [[ -n "${DATABASE_URL:-}" ]] || die "DATABASE_URL is required" 2
}

require_redis_url() {
  [[ -n "${REDIS_URL:-}" ]] || die "REDIS_URL is required" 2
}

resolve_python_bin() {
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    printf "%s" "$ROOT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    die "python is not available in this Pod image" 2
  fi
}

resolve_alembic_bin() {
  if [[ -x "$ROOT_DIR/.venv/bin/alembic" ]]; then
    printf "%s" "$ROOT_DIR/.venv/bin/alembic"
  elif command -v alembic >/dev/null 2>&1; then
    command -v alembic
  else
    die "alembic is not available in this Pod image" 2
  fi
}

print_database_target() {
  local python_bin="$1"
  "$python_bin" <<'PY'
import os
from urllib.parse import unquote, urlsplit

raw_url = os.environ["DATABASE_URL"]
url = urlsplit(raw_url)
if not url.scheme or not url.hostname:
    raise SystemExit("invalid DATABASE_URL: missing scheme or host")

database = "-"
if url.path and url.path != "/":
    database = unquote(url.path.lstrip("/"))

user = unquote(url.username) if url.username else "-"
password = unquote(url.password) if url.password else "-"
port = str(url.port) if url.port else "-"

print(f"url={raw_url} scheme={url.scheme} host={url.hostname} port={port} database={database} user={user} password={password}")
PY
}

prepare_migration_runtime() {
  load_k8s_env_file_defaults
  require_database_url
  PYTHON_BIN="$(resolve_python_bin)"
  ALEMBIC_BIN="$(resolve_alembic_bin)"
  export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
}

prepare_check_runtime() {
  require_k8s_pod
  PYTHON_BIN="$(resolve_python_bin)"
  export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
}

print_target_section() {
  section "K8s Database"
  event "TARGET" "DATABASE_URL" "$(print_database_target "$PYTHON_BIN")"
}

require_no_args() {
  local command_name="$1"
  shift
  [[ "$#" -eq 0 ]] || die "$command_name does not accept arguments" 2
}

run_check_postgres() {
  require_no_args "check postgres" "$@"
  load_k8s_env_file_defaults
  prepare_check_runtime
  section "PostgreSQL"
  "$PYTHON_BIN" <<'PY'
import os
import sys

import psycopg2

from scripts.jobs.db import normalize_database_url

from urllib.parse import unquote, urlsplit


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def print_url_detail(name: str, raw_url: str) -> None:
    print(f"{name}={raw_url}")
    parsed = urlsplit(raw_url)
    print(f"{name}_scheme={parsed.scheme}")
    print(f"{name}_username_encoded={parsed.username or '-'}")
    print(f"{name}_username_decoded={unquote(parsed.username or '') or '-'}")
    print(f"{name}_password_encoded={parsed.password or '-'}")
    print(f"{name}_password_decoded={unquote(parsed.password or '') or '-'}")
    print(f"{name}_hostname={parsed.hostname or '-'}")
    try:
        port = parsed.port
    except ValueError as exc:
        print(f"{name}_port_error={exc}")
        sys.stdout.flush()
        raise SystemExit(f"invalid {name}: {exc}") from exc
    print(f"{name}_port={port if port is not None else '-'}")
    print(f"{name}_path={unquote(parsed.path) or '-'}")
    print(f"{name}_query={parsed.query or '-'}")
    print(f"{name}_fragment={parsed.fragment or '-'}")
    if not parsed.scheme or not parsed.hostname:
        raise SystemExit(f"invalid {name}: missing scheme or host")


raw_database_url = require_env("DATABASE_URL")
db_ssl = os.getenv("DB_SSL")
print_url_detail("DATABASE_URL", raw_database_url)
print(f"DB_SSL={db_ssl if db_ssl is not None else '-'}")
connect_url = normalize_database_url(raw_database_url, db_ssl=db_ssl)
print(f"POSTGRES_CONNECT_URL={connect_url}")
sys.stdout.flush()

connection = psycopg2.connect(connect_url, connect_timeout=5)
try:
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        value = cursor.fetchone()[0]
finally:
    connection.close()

print(f"OK postgres select_1={value}")
PY
}

run_check_redis() {
  require_no_args "check redis" "$@"
  load_k8s_env_file_defaults
  prepare_check_runtime
  require_redis_url
  "$ROOT_DIR/scripts/redis.sh" check --show-url --no-broker-key --redis-url "$REDIS_URL"
}

run_check_dashboard() {
  require_no_args "check dashboard" "$@"
  prepare_check_runtime
  section "Ops Dashboard"
  "$PYTHON_BIN" <<'PY'
import asyncio
import traceback

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import ROOT_DIR, settings
from app.ops_dashboard import read_model
from app.ops_dashboard.schemas import DashboardFilters


def print_setting(name: str, value: object) -> None:
    print(f"{name}={value}")


async def main() -> int:
    dotenv_path = ROOT_DIR / ".env"
    print_setting("ROOT_DIR", ROOT_DIR)
    print_setting("DOTENV", dotenv_path)
    print_setting("DOTENV_EXISTS", dotenv_path.exists())
    print_setting("OPS_DASHBOARD_ENABLED", settings.ops_dashboard.enabled)
    print_setting("OPS_DASHBOARD_REQUIRE_AUTH", settings.ops_dashboard.require_auth)
    print_setting("OPS_DASHBOARD_REFRESH_SECONDS", settings.ops_dashboard.refresh_seconds)
    print_setting("OPS_DASHBOARD_MAX_WINDOW_SECONDS", settings.ops_dashboard.max_window_seconds)
    print_setting("OPS_DASHBOARD_QUERY_TIMEOUT_SECONDS", settings.ops_dashboard.query_timeout_seconds)

    if not settings.ops_dashboard.enabled:
        print("SKIP dashboard disabled")
        return 0

    engine = create_async_engine(settings.database.url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            filters = DashboardFilters(window="1h")
            checks = [
                (
                    "overview",
                    lambda: read_model.overview_data(
                        db,
                        filters,
                        max_active_jobs=settings.job.max_active_jobs,
                    ),
                ),
                ("failures", lambda: read_model.failures_data(db, filters)),
            ]
            for name, call in checks:
                try:
                    payload = await call()
                except Exception:
                    print(f"ERROR dashboard {name} read_model")
                    traceback.print_exc()
                    return 1
                keys = ",".join(sorted(payload.keys())) if isinstance(payload, dict) else type(payload).__name__
                print(f"OK dashboard {name} read_model keys={keys}")
    finally:
        await engine.dispose()
    return 0


raise SystemExit(asyncio.run(main()))
PY
}

run_check_oss() {
  local arg
  local json_output=false
  [[ "${1:-}" == "--confirm" ]] || die "check oss requires --confirm because it writes a temporary OSS object" 2
  shift
  for arg in "$@"; do
    [[ "$arg" != "--env-file" && "$arg" != --env-file=* ]] || die "check oss in a Pod uses current Pod environment; --env-file is not allowed" 2
    if [[ "$arg" == "--json" ]]; then
      json_output=true
    fi
  done
  prepare_check_runtime
  if [[ "$json_output" != "true" ]]; then
    section "OSS"
  fi
  PYTHON_BIN="$PYTHON_BIN" "$ROOT_DIR/scripts/oss.sh" check --remote --confirm "$@"
}

run_check() {
  local target="${1:-}"
  case "$target" in
    "")
      local status=0
      if ! run_check_postgres; then
        status=1
      fi
      if ! run_check_redis; then
        status=1
      fi
      if ! run_current; then
        status=1
      fi
      if ! run_heads; then
        status=1
      fi
      return "$status"
      ;;
    postgres)
      shift
      run_check_postgres "$@"
      ;;
    redis)
      shift
      run_check_redis "$@"
      ;;
    dashboard)
      shift
      run_check_dashboard "$@"
      ;;
    oss)
      shift
      run_check_oss "$@"
      ;;
    *)
      die "check target must be postgres, redis, dashboard, or oss" 2
      ;;
  esac
}

run_current() {
  require_no_args current "$@"
  prepare_migration_runtime
  print_target_section
  section "Alembic"
  "$ALEMBIC_BIN" current
}

run_heads() {
  require_no_args heads "$@"
  prepare_migration_runtime
  print_target_section
  section "Alembic"
  "$ALEMBIC_BIN" heads
}

run_history() {
  require_no_args history "$@"
  prepare_migration_runtime
  print_target_section
  section "Alembic"
  "$ALEMBIC_BIN" history
}

run_migrate() {
  [[ "${1:-}" == "--confirm" ]] || die "migrate requires --confirm because it writes to the configured database" 2
  shift
  require_no_args migrate "$@"
  prepare_migration_runtime
  print_target_section
  section "Alembic"
  event "RUN" "upgrade" "head"
  "$ALEMBIC_BIN" upgrade head
}
