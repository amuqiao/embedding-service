#!/usr/bin/env bash
# k8s.sh - K8s Pod 内手动运维入口
#
# 运行环境：Bash；需要在已注入应用环境变量的 K8s Pod 内执行。
# 作用域：只提供 Pod 内连接检查、Alembic 迁移和迁移状态查询。
# 约束：不创建或管理 Kubernetes 资源，不调用 kubectl，不管理 API/worker 生命周期。
# 输出：按生产排障需要打印完整连接串和解析结果；Alembic 输出透传。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

cd "$ROOT_DIR"

usage() {
  cat <<EOF
用法：
  ./scripts/k8s.sh <command> [args...]
  ./scripts/k8s.sh -h|--help

作用域：
  本脚本是 K8s Pod 内手动运维入口。
  进入已部署的 api 或 worker Pod 后，使用同一份应用代码和同一组环境变量连接外部数据库。
  只负责 PostgreSQL / Redis 连接检查、Alembic 迁移状态查询和手动执行迁移。

不负责：
  不创建 Job、Pod、Deployment、Secret、ConfigMap。
  不调用 kubectl、helm、docker compose。
  不管理 API/worker 生命周期。
  不替代 CI/CD 发布编排。

运行环境：
  Requires: Bash, Python；Alembic 状态和迁移命令还需要 Alembic。
  必须在 K8s Pod 内执行，且环境变量 KUBERNETES_SERVICE_HOST 必须存在。
  check 会打印完整 DATABASE_URL / REDIS_URL、编码密码和解码密码，输出包含敏感信息。
  current / heads / history / migrate 必须注入应用 DATABASE_URL。

命令：
  check               聚合执行 check postgres 和 check redis。
  check postgres      检查 DATABASE_URL 解析结果，并执行 PostgreSQL SELECT 1。
  check redis         检查 REDIS_URL 解析结果，并执行 Redis PING。
  current             查看当前数据库 Alembic revision。
  heads               查看代码中的 Alembic head revision。
  history             查看 Alembic revision 历史。
  migrate --confirm   对当前 DATABASE_URL 执行 alembic upgrade head。
  help                显示帮助。

安全约束：
  migrate 是写库动作，必须显式传入 --confirm。
  生产多副本部署时，只应在一个 Pod 内执行一次 migrate。
  执行迁移前应确认当前 Pod 运行的是要发布的代码版本。
  check 会输出明文连接串和密码，只应在受控终端中执行。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check postgres
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check redis
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh current
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh heads
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh migrate --confirm

Exit Codes:
  0  成功
  1  check 连接串解析、认证或连通性失败。
  2  缺少 command、非法参数、未在 K8s Pod 内执行或缺少 Python/Alembic。
EOF
}

require_k8s_pod() {
  [[ -n "${KUBERNETES_SERVICE_HOST:-}" ]] || die "k8s.sh must run inside a K8s Pod; KUBERNETES_SERVICE_HOST is not set" 2
}

require_database_url() {
  [[ -n "${DATABASE_URL:-}" ]] || die "DATABASE_URL is required" 2
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
  require_k8s_pod
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
  prepare_check_runtime
  section "Redis"
  "$PYTHON_BIN" <<'PY'
import os
import sys
from urllib.parse import unquote, urlsplit

from redis import Redis


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


raw_redis_url = require_env("REDIS_URL")
print_url_detail("REDIS_URL", raw_redis_url)
sys.stdout.flush()

client = Redis.from_url(raw_redis_url, socket_connect_timeout=5, socket_timeout=5)
try:
    ping = client.ping()
finally:
    client.connection_pool.disconnect()

print(f"OK redis ping={ping}")
PY
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
    *)
      die "check target must be postgres or redis" 2
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

command="${1:-}"
case "$command" in
  --help|-h|help)
    usage
    ;;
  "")
    usage >&2
    exit 2
    ;;
  check)
    shift
    run_check "$@"
    ;;
  current)
    shift
    run_current "$@"
    ;;
  heads)
    shift
    run_heads "$@"
    ;;
  history)
    shift
    run_history "$@"
    ;;
  migrate)
    shift
    run_migrate "$@"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
