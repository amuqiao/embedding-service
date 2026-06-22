#!/usr/bin/env bash
# jobs.sh - Job 只读查询与排障入口
#
# 运行环境：Bash；需要 Python、Typer、psycopg2；DB 查询需要 PostgreSQL 可达。
# 作用域：在本地或 Pod 内查询 Job、attempt、callback 和 timeline 证据。
# 约束：只执行只读查询，不提供创建、取消、重试、补偿或任何状态修改能力。
# 输出：人读模式遵循 section/event/table；--json 输出纯 JSON。
# 帮助：本脚本只负责定位 Python 并转发参数；帮助信息集中维护在 scripts/jobs/cli.py。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  python_bin="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
else
  die "python is not available; run: ./scripts/dev.sh bootstrap" 2
fi

PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONUNBUFFERED=1 \
  exec "$python_bin" -m scripts.jobs.cli "$@"
