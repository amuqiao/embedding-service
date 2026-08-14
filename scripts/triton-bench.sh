#!/usr/bin/env bash
# triton-bench.sh - audio stem Triton direct benchmark entrypoint
#
# 作用域：直连 audio_stem_separation_triton 使用的 Triton endpoint，测模型服务推理并发、延迟和错误率。
# 约束：不创建 Job，不访问 FastAPI API，不访问 DB/Redis/OSS，不触发 callback。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

exec_repo_python_module scripts.triton_bench.cli "$@"
