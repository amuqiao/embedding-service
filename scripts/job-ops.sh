#!/usr/bin/env bash
# job-ops.sh - Job 写操作运维入口
#
# 运行环境：Bash；需要项目 Python 环境、PostgreSQL 和 Redis/Taskiq 可达。
# 作用域：在本地或 Pod 内执行明确确认过的 Job 恢复、删除和恢复写操作。
# 约束：所有写操作必须传 --confirm；只读排障继续使用 scripts/jobs.sh。
# 输出：人读模式默认；--json 输出机器可读 JSON。
# 帮助：本脚本只负责定位 Python 并转发参数；帮助信息集中维护在 scripts/job_ops/cli.py。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

exec_repo_python_module scripts.job_ops.cli "$@"
