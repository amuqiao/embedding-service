#!/usr/bin/env bash
# k8s.sh - K8s Pod 内手动运维入口
#
# 运行环境：Bash；需要在已注入应用环境变量的 K8s Pod 内执行。
# 作用域：只提供 Pod 内连接检查、OSS 连通性检查、dashboard read model 检查、Alembic 迁移和迁移状态查询。
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
  进入已部署的 api 或 worker Pod 后，使用同一份应用代码和同一组环境变量连接外部依赖。
  只负责 PostgreSQL / Redis / OSS 连接检查、dashboard read model 检查、Alembic 迁移状态查询和手动执行迁移。

不负责：
  不创建 Job、Pod、Deployment、Secret、ConfigMap。
  不调用 kubectl、helm、docker compose。
  不管理 API/worker 生命周期。
  不替代 CI/CD 发布编排。

运行环境：
  Requires: Bash, Python；check、current、heads、history 和 migrate 还需要 Alembic。
  必须在 K8s Pod 内执行，且环境变量 KUBERNETES_SERVICE_HOST 必须存在。
  check 会打印完整 DATABASE_URL / REDIS_URL、编码密码和解码密码，输出包含敏感信息。
  check oss 会向 OSS 写入、读取、HEAD 一个临时对象并打印 URL Ref；默认不打印 OSS secret。
  check / current / heads / history / migrate 必须注入应用 DATABASE_URL。

命令：
  check               聚合执行 PostgreSQL、Redis、Alembic current 和 heads 无副作用检查。
  check postgres      检查 DATABASE_URL 解析结果，并执行 PostgreSQL SELECT 1。
  check redis         检查 REDIS_URL 解析结果，并执行 Redis PING。
  check dashboard     检查 ops_dashboard 有效配置，并执行 dashboard read model。
  check oss --confirm 检查 OSS 配置，并执行临时对象 PUT / GET / HEAD。
  current             查看当前数据库 Alembic revision。
  heads               查看代码中的 Alembic head revision。
  history             查看 Alembic revision 历史。
  migrate --confirm   对当前 DATABASE_URL 执行 alembic upgrade head。
  help                显示帮助。

输出：
  stdout: 连接串解析结果、连通性证据、dashboard read model 结果、Alembic 输出和 OSS URL Ref。
  stderr: 非 Pod 环境、缺少依赖、缺少配置、连接失败或迁移失败详情。

副作用与保护边界：
  migrate 是写库动作，必须显式传入 --confirm。
  生产多副本部署时，只应在一个 Pod 内执行一次 migrate。
  执行迁移前应确认当前 Pod 运行的是要发布的代码版本。
  check 和 check postgres / redis 会输出明文连接串和密码，只应在受控终端中执行。
  check 是无副作用一键检查，不包含 check dashboard / check oss，也不会执行 migrate。
  check oss 是远程写入动作，必须显式传入 --confirm；会留下对象，需要按输出 key 手动清理或配置生命周期清理。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check postgres
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check redis
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check dashboard
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check oss --confirm
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh current
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh migrate --confirm

Exit Codes:
  0  成功
  1  check 连接串解析、认证、连通性或 Alembic 查询失败。
  2  缺少 command、非法参数、未在 K8s Pod 内执行或缺少 Python/Alembic。
EOF
}

command_usage() {
  local name="$1"
  local target="${2:-}"
  case "$name:$target" in
    check:)
      cat <<EOF
用法：
  ./scripts/k8s.sh check
  ./scripts/k8s.sh check <postgres|redis|dashboard|oss> [--confirm]
  ./scripts/k8s.sh check -h|--help

作用域：
  聚合执行 PostgreSQL、Redis、Alembic current 和 heads 无副作用检查。

副作用与保护边界：
  check 不包含 check dashboard / check oss，也不会执行 migrate。
  check postgres / redis 会输出明文连接串和密码。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check postgres
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check redis
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check dashboard
EOF
      ;;
    check:postgres|check:redis)
      cat <<EOF
用法：
  ./scripts/k8s.sh check ${target}
  ./scripts/k8s.sh check ${target} -h|--help

作用域：
  检查 ${target} 连接串解析结果和连通性。

副作用与保护边界：
  会输出明文连接串和密码，只应在受控终端中执行。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check ${target}
EOF
      ;;
    check:dashboard)
      cat <<EOF
用法：
  ./scripts/k8s.sh check dashboard
  ./scripts/k8s.sh check dashboard -h|--help

作用域：
  检查 ops_dashboard 有效配置，并直接执行 overview / failures read model。
  适合定位 dashboard 页面 500、SQL prepare、配置加载和数据库读模型问题。

副作用与保护边界：
  只读 DB 查询；不调用 Job 写路径，不 replay dispatch / callback。
  不包含在默认 check 中，必须显式执行。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check dashboard
EOF
      ;;
    check:oss)
      cat <<EOF
用法：
  ./scripts/k8s.sh check oss --confirm
  ./scripts/k8s.sh check oss -h|--help

作用域：
  检查 OSS 配置，并执行临时对象 PUT / GET / HEAD。

副作用与保护边界：
  远程写入动作，必须显式传入 --confirm。
  会留下对象，需要按输出 key 手动清理或配置生命周期清理。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check oss --confirm
EOF
      ;;
    current:|heads:|history:)
      cat <<EOF
用法：
  ./scripts/k8s.sh ${name}
  ./scripts/k8s.sh ${name} -h|--help

作用域：
  查看 Alembic ${name} 信息。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh ${name}
EOF
      ;;
    migrate:)
      cat <<EOF
用法：
  ./scripts/k8s.sh migrate --confirm
  ./scripts/k8s.sh migrate -h|--help

作用域：
  对当前 DATABASE_URL 执行 alembic upgrade head。

副作用与保护边界：
  写库动作，必须显式传入 --confirm。
  生产多副本部署时，只应在一个 Pod 内执行一次。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh migrate --confirm
EOF
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
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
  [[ "${1:-}" == "--confirm" ]] || die "check oss requires --confirm because it writes a temporary OSS object" 2
  shift
  require_no_args "check oss" "$@"
  prepare_check_runtime
  section "OSS"
  "$PYTHON_BIN" <<'PY'
import os
import time

from app.integrations.aliyun_oss import AliyunOSSClient, AliyunOSSConfig, AliyunOSSError
from app.integrations.object_storage import sha256_digest
from app.jobs.adapters.oss_url_ref import oss_url_ref_from_output_object


TEST_CONTENT = b"fastapi-best-ai-architecture k8s oss connectivity check\n"
TEST_CONTENT_TYPE = "text/plain; charset=utf-8"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


storage_backend = require_env("STORAGE_BACKEND")
if storage_backend != "aliyun_oss":
    raise SystemExit("STORAGE_BACKEND must be aliyun_oss for check oss")

bucket = require_env("OSS_BUCKET")
region = require_env("OSS_REGION")
access_key_id = require_env("OSS_ACCESS_KEY_ID")
access_key_secret = require_env("OSS_ACCESS_KEY_SECRET")
project_root = require_env("OSS_PROJECT_ROOT")
public_endpoint = os.getenv("OSS_PUBLIC_ENDPOINT", "")
endpoint = os.getenv("OSS_ENDPOINT", "") or public_endpoint or f"oss-{region}.aliyuncs.com"
endpoint_style = "custom_domain" if public_endpoint and endpoint == public_endpoint else "virtual_host"

client = AliyunOSSClient(
    AliyunOSSConfig(
        bucket=bucket,
        region=region,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        project_root=project_root,
        endpoint=endpoint,
        endpoint_style=endpoint_style,
        scheme="https",
    )
)

config = client.config
print(f"OSS_BACKEND={storage_backend}")
print(f"OSS_BUCKET={config.bucket}")
print(f"OSS_REGION={config.region}")
print(f"OSS_PROJECT_ROOT={config.normalized_project_root}")
output_prefix = os.getenv("OSS_OUTPUT_PREFIX", "ai-jobs").strip().strip("/")
print(f"OSS_OUTPUT_PREFIX={output_prefix or '-'}")
print(f"OSS_ENDPOINT={config.normalized_endpoint}")
print(f"OSS_ENDPOINT_STYLE={config.endpoint_style}")
print(f"OSS_PUBLIC_ENDPOINT={public_endpoint or '-'}")
print(f"OSS_ACCESS_KEY_ID_present={'true' if access_key_id else 'false'}")
print(f"OSS_ACCESS_KEY_SECRET_present={'true' if access_key_secret else 'false'}")

key = "/".join(part for part in (output_prefix, "k8s-check", f"check-{int(time.time())}.txt") if part)
object_key = client.object_key(key)
print(f"OSS_TEST_KEY={object_key}")
content_hash = sha256_digest(TEST_CONTENT)
url_ref = oss_url_ref_from_output_object(
    bucket=config.bucket,
    region=config.region,
    key=object_key,
    content_type=TEST_CONTENT_TYPE,
    content_hash=content_hash,
    public_endpoint=public_endpoint or None,
)
print(f"OSS_TEST_PUBLIC_URL={url_ref['public_url']}")
print(f"OSS_TEST_INTERNAL_URL={url_ref['internal_url']}")
print(f"OSS_TEST_CONTENT_TYPE={url_ref['content_type']}")
print(f"OSS_TEST_SHA256={url_ref['sha256']}")

try:
    client.put_object(key, TEST_CONTENT, content_type=TEST_CONTENT_TYPE)
    body = client.get_object(key)
    if body != TEST_CONTENT:
        raise RuntimeError("GET body does not match uploaded content")
    headers = client.head_object(key)
except (AliyunOSSError, RuntimeError) as exc:
    raise SystemExit(f"OSS check failed: {exc}") from exc

print(f"OK oss key={object_key} bytes={len(body)} content_length={headers.get('Content-Length', '-')} delete_checked=false")
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
    if args_include_help "$@"; then
      case "${1:-}" in
        -h|--help) command_usage check ;;
        *) command_usage check "${1:-}" ;;
      esac
      exit $?
    fi
    run_check "$@"
    ;;
  current)
    shift
    if args_include_help "$@"; then command_usage current; exit $?; fi
    run_current "$@"
    ;;
  heads)
    shift
    if args_include_help "$@"; then command_usage heads; exit $?; fi
    run_heads "$@"
    ;;
  history)
    shift
    if args_include_help "$@"; then command_usage history; exit $?; fi
    run_history "$@"
    ;;
  migrate)
    shift
    if args_include_help "$@"; then command_usage migrate; exit $?; fi
    run_migrate "$@"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
