#!/usr/bin/env bash
# real-flow.sh - 真实业务流程验证入口
#
# 运行环境：Bash；需要 Python venv、Typer、本地 API/worker 和真实模型配置。
# 作用域：手动触发真实业务流程验证，可调用真实 LLM 并查询 billing 证据。
# 约束：shell 只负责定位 Python 并转发参数；帮助和业务逻辑集中维护在 scripts/real_flow/cli.py。

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
  exec "$python_bin" -m scripts.real_flow.cli "$@"
