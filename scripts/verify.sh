#!/usr/bin/env bash
# verify.sh - 本地验证入口
#
# 运行环境：Bash；需要 Python venv，check 会运行 pytest。
# 作用域：承接测试、smoke、e2e 等模板级一次性验证任务。
# 本地服务生命周期不属于本入口。
# 约束：入口脚本只做参数分发和帮助说明，具体实现下沉到 scripts/verify/ 原子脚本。
# 输出：每个验证任务先打印稳定 section；pytest/smoke 这类用户需要看的工具结果可透传。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<EOF
用法：
  ./scripts/verify.sh <command> [args...]
  ./scripts/verify.sh -h|--help

作用域：
  当前仓库的一次性验证入口。验证任务可以依赖已运行的本地 API/worker，但不负责完整本地服务生命周期。

运行环境：
  Requires: Bash
  Dependencies: Python venv；check 会运行 pytest。

命令：
  test                运行 pytest。
  smoke               无正式 job_type 时不可用；新增正式能力后再恢复。
  mock-smoke          无正式 job_type 时不可用；新增正式能力后再恢复。
  workflow-smoke      使用内置 job_test_echo 验证本地 Job 创建、Taskiq 执行和状态轮询流程。
  workflow-modes-smoke 使用 6 个内置 workflow 测试 job_type 验证 chain/group/chord/map/starmap/chunks 的真实 Job e2e。
  migration-roundtrip 使用临时本地 PostgreSQL 数据库验证 Alembic upgrade/downgrade/re-upgrade。
  e2e                 无正式 job_type 时不可用；新增正式能力后再恢复。
  env-config          校验 env 文件键名；默认检查 .env.example 和已存在的本地/测试 env，可传文件路径。
  image-inspect       检测本地路径或 http(s) URL 图片类型、尺寸、alpha 通道和透明背景。
  check               执行脚本语法、入口 help、Python 语法、env 配置、registry consistency 和 pytest。
  help                显示帮助。

成功标准：
  check 成功 = 脚本语法、入口 help、Python 语法、env 配置、registry consistency 和 pytest 均通过。

环境变量：
  API_HOST / API_PORT        可选，workflow-smoke 使用的本地 API 地址来源。
  SCRIPT_ENV_FILE            可选，覆盖脚本配置文件路径，默认 scripts/.env。

输出：
  stdout: 阶段化验证结果；pytest 和 workflow-smoke 输出可透传。
  stderr: 非法命令、不可用验证任务、Python 或测试失败详情。

幂等性和副作用：
  test/check/env-config 不修改服务状态。
  image-inspect 默认只读取入参图片；URL 入参会发起 HTTP GET。
  workflow-smoke 会向已运行的本地 API 创建一个内置 job_test_echo 测试 Job。
  workflow-modes-smoke 会向已运行的本地 API 创建 6 个内置 workflow 测试 Job。
  migration-roundtrip 会创建并删除一个临时本地 PostgreSQL 数据库，不修改当前应用数据库。

常用示例：
  ./scripts/verify.sh check
  ./scripts/verify.sh test
  ./scripts/verify.sh env-config
  ./scripts/verify.sh image-inspect .data/title.png --require-transparent-background
  ./scripts/verify.sh image-inspect https://example.com/title.png --json
  ./scripts/verify.sh workflow-smoke
  ./scripts/verify.sh workflow-modes-smoke
  ./scripts/verify.sh migration-roundtrip

Exit Codes:
  0  成功
  2  缺少 command、非法命令或当前验证任务不可用
  其他非 0 由 pytest、Python 语法检查或验证子任务返回
EOF
}

no_builtin_job_types() {
  echo "当前项目只有测试示例 job_type；该验证命令需要新增正式能力后再恢复。" >&2
  exit 2
}

command="${1:-}"
case "$command" in
  --help|-h|help)
    usage
    ;;
  "")
    usage >&2
    exit 2
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
  workflow-modes-smoke)
    run_workflow_modes_smoke
    ;;
  migration-roundtrip)
    run_migration_roundtrip
    ;;
  e2e)
    no_builtin_job_types
    ;;
  env-config)
    shift
    run_env_config_check "$@"
    ;;
  image-inspect)
    shift
    run_image_inspect "$@"
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
