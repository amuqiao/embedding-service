#!/usr/bin/env bash
# ai-providers.sh - 云模型 provider 和 AI catalog/resolver 诊断入口
#
# 帮助：本脚本只负责定位 Python 并转发参数；帮助信息集中维护在 scripts/ai_providers/cli.py。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

exec_project_python_module scripts.ai_providers.cli "$@"
