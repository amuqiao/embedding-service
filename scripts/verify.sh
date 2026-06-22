#!/usr/bin/env bash
# verify.sh - 本地验证入口
#
# 作用域：承接测试、smoke、e2e 等模板级一次性验证任务。
# 本地服务生命周期不属于本入口。
# 约束：入口脚本只做参数分发和帮助说明，具体实现下沉到 scripts/verify/ 原子脚本。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<EOF
用法：
  ./scripts/verify.sh <command> [args...]
  ./scripts/verify.sh --help

作用域：
  当前仓库的一次性验证入口。验证任务可以依赖已运行的本地 API/worker，但不负责完整本地服务生命周期。

命令：
  test                运行 pytest。
  smoke               无正式 job_type 时不可用；新增正式能力后再恢复。
  mock-smoke          无正式 job_type 时不可用；新增正式能力后再恢复。
  workflow-smoke      使用内置 job_test_echo 验证本地 Job 创建、Taskiq 执行和状态轮询流程。
  e2e                 无正式 job_type 时不可用；新增正式能力后再恢复。
  env-config          校验 env 文件键名；默认检查 .env.example 和已存在的本地/测试 env，可传文件路径。
  check               执行脚本语法、入口 help、Python 语法、env 配置、registry consistency 和 pytest。
  help                显示帮助。

成功标准：
  check 成功 = 脚本语法、入口 help、Python 语法、env 配置、registry consistency 和 pytest 均通过。
EOF
}

no_builtin_job_types() {
  echo "当前项目只有测试示例 job_type；该验证命令需要新增正式能力后再恢复。" >&2
  exit 2
}

command="${1:-help}"
case "$command" in
  --help|-h|help)
    usage
    ;;
  *)
    source "$ROOT_DIR/scripts/verify/tasks.sh"
    case "$command" in
  test)
    run_tests
    ;;
  smoke)
    no_builtin_job_types
    ;;
  mock-smoke)
    no_builtin_job_types
    ;;
  workflow-smoke)
    run_workflow_smoke
    ;;
  e2e)
    no_builtin_job_types
    ;;
  env-config)
    shift
    run_env_config_check "$@"
    ;;
  check)
    run_check
    ;;
  *)
    usage >&2
    exit 2
    ;;
    esac
    ;;
esac
