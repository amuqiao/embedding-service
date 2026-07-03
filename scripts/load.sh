#!/usr/bin/env bash
# load.sh - 压测入口
#
# 运行环境：Bash；需要项目 Python 环境、uv 和 Locust load dependency group。
# 作用域：本地或显式允许的远端 API 压测，统一场景、输出目录和压后诊断入口。
# 约束：shell 只负责定位 Python 并转发参数；帮助和业务逻辑集中维护在 scripts/load/cli.py。

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
  exec "$python_bin" -m scripts.load.cli "$@"
