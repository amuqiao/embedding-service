#!/usr/bin/env bash
# oss.sh - 对象存储配置、连通性和显式上传检查入口
#
# 运行环境：Bash；需要 Python 和对象存储运行依赖。
# 作用域：检查对象存储配置、显式远端连通性和显式图片上传。
# 约束：默认只读配置；远端 PUT/GET/HEAD 必须 --confirm；上传必须 --confirm-upload。
# 帮助：本脚本只负责定位 Python 并转发参数；帮助信息集中维护在 scripts/oss/cli.py。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

exec_project_python_module scripts.oss.cli "$@"
