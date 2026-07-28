#!/usr/bin/env bash
# redis.sh - Redis 只读排障入口
#
# 运行环境：Bash；需要 Python、Typer 和 redis-py。
# 作用域：检查 Redis 连接、服务端能力、内存、keyspace、Stream 和 broker key 证据。
# 约束：只执行只读命令，不删除 key，不修复队列，不自动切换 broker kind。
# 帮助：本脚本只负责定位 Python 并转发参数；帮助信息集中维护在 scripts/redis_diag/cli.py。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  require_command "$PYTHON_BIN" "install Python 3 or set PYTHON_BIN"
  python_bin="$PYTHON_BIN"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  python_bin="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
else
  die "python is not available; run: ./scripts/dev.sh bootstrap" 2
fi

PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONUNBUFFERED=1 \
  exec "$python_bin" -m scripts.redis_diag.cli "$@"
