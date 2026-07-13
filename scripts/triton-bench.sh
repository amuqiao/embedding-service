#!/usr/bin/env bash
# triton-bench.sh - audio stem Triton direct benchmark entrypoint
#
# 作用域：直连 audio_stem_separation_triton 使用的 Triton endpoint，测模型服务推理并发、延迟和错误率。
# 约束：不创建 Job，不访问 FastAPI API，不访问 DB/Redis/OSS，不触发 callback。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  python_bin="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
else
  echo "ERROR: python is not available; run: ./scripts/dev.sh bootstrap" >&2
  exit 2
fi

PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONUNBUFFERED=1 \
  exec "$python_bin" -m scripts.triton_bench.cli "$@"
