#!/usr/bin/env bash
# verify.sh - 本地验证入口
#
# 运行环境：Bash；需要 Python venv，check 会运行 pytest。
# 作用域：承接测试和模板级一次性验证任务；业务 smoke/E2E 使用 scripts/smoke.sh。
# 本地服务生命周期不属于本入口。
# 约束：入口脚本只做参数分发和帮助说明，具体实现下沉到 scripts/verify/ 原子脚本。
# 输出：每个验证任务先打印稳定 section；pytest 和 workflow-smoke 这类用户需要看的工具结果可透传。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

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
  workflow-smoke      使用内置 example_sleep 验证本地 Job 创建、Taskiq 执行和状态轮询流程。
  workflow-modes-smoke 使用内置 workflow 测试 job_type 验证 single/chain/group/chord/map/starmap/chunks 的真实 Job e2e。
  migration-roundtrip 使用临时本地 PostgreSQL 数据库验证 Alembic upgrade/downgrade/re-upgrade。
  env-config          校验 env 文件键名；可用 --env-file/--app-env 提前验证启动配置安全规则。
  oss-config          校验阿里云 OSS 配置；默认只检查本地配置，--remote 才访问 OSS，--upload-image 可上传图片。
  image-inspect       检测本地路径或 http(s) URL 图片类型、尺寸、alpha 通道和透明背景。
  check               执行脚本语法、入口 help、Python 语法、env 配置、Alembic revision、registry consistency 和 pytest。
  help                显示帮助。

配置与环境变量：
  API_HOST / API_PORT        可选，workflow-smoke 使用的本地 API 地址来源。
  ENV_FILE                   可选，覆盖配置文件路径，默认 .env。

输出：
  stdout: 阶段化验证结果；pytest 和 workflow-smoke 输出可透传。
  stderr: 非法命令、不可用验证任务、Python 或测试失败详情。

成功标准：
  check 成功 = 脚本语法、入口 help、Python 语法、env 配置、Alembic revision、registry consistency 和 pytest 均通过。

副作用与保护边界：
  test/check/env-config 不修改服务状态。
  oss-config 默认不修改服务状态；--remote 会写入并删除一个临时 OSS 对象；--upload-image 会上传指定本地图片。
  image-inspect 默认只读取入参图片；URL 入参会发起 HTTP GET。
  workflow-smoke 会向已运行的本地 API 创建一个内置 example_sleep 测试 Job。
  workflow-modes-smoke 会向已运行的本地 API 创建多个内置 workflow 测试 Job。
  migration-roundtrip 会创建并删除一个临时本地 PostgreSQL 数据库，不修改当前应用数据库。

常用示例：
  ./scripts/verify.sh check
  ./scripts/verify.sh test
  ./scripts/verify.sh env-config --env-file .env.test --app-env test
  ./scripts/verify.sh oss-config --remote
  ./scripts/verify.sh image-inspect .data/title.png --require-transparent-background
  ./scripts/verify.sh workflow-smoke

Exit Codes:
  0  成功
  2  缺少 command、非法命令或当前验证任务不可用
  其他非 0 由 pytest、Python 语法检查或验证子任务返回
EOF
}

command_usage() {
  local name="$1"
  case "$name" in
    test)
      cat <<EOF
用法：
  ./scripts/verify.sh test
  ./scripts/verify.sh test -h|--help

作用域：
  运行当前仓库 pytest。

输出：
  stdout/stderr: pytest 输出。

副作用与保护边界：
  不修改服务状态。

常用示例：
  ./scripts/verify.sh test

Exit Codes:
  0  成功
  其他非 0 由 pytest 返回
EOF
      ;;
    workflow-smoke)
      require_project_python
      "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/job_workflow_smoke.py" -h
      ;;
    workflow-modes-smoke)
      require_project_python
      "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/workflow_modes_smoke.py" -h
      ;;
    migration-roundtrip)
      cat <<EOF
用法：
  ./scripts/verify.sh migration-roundtrip
  ./scripts/verify.sh migration-roundtrip -h|--help

作用域：
  使用临时本地 PostgreSQL 数据库验证 Alembic upgrade/downgrade/re-upgrade。

配置与环境变量：
  使用本地验证数据库配置；不修改当前应用数据库。

输出：
  stdout/stderr: Alembic roundtrip 阶段和失败详情。

副作用与保护边界：
  会创建并删除一个临时本地 PostgreSQL 数据库。
  -h/--help 只显示本帮助，不连接数据库，不执行迁移。

常用示例：
  ./scripts/verify.sh migration-roundtrip

Exit Codes:
  0  成功
  2  配置或前置条件错误
  其他非 0 由 PostgreSQL/Alembic 子任务返回
EOF
      ;;
    env-config)
      require_project_python
      "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/env_config_check.py" -h
      ;;
    oss-config)
      require_project_python
      "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/oss_config_check.py" -h
      ;;
    image-inspect)
      require_project_python
      "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/image_inspect.py" -h
      ;;
    check)
      cat <<EOF
用法：
  ./scripts/verify.sh check
  ./scripts/verify.sh check -h|--help

作用域：
  执行脚本语法、入口 help、Python 语法、env 配置、Alembic revision、registry consistency 和 pytest。

输出：
  stdout: 阶段化验证结果；pytest 输出透传。
  stderr: 任一阶段失败详情。

副作用与保护边界：
  不启动或停止服务。
  会运行 pytest；耗时取决于测试集。

常用示例：
  ./scripts/verify.sh check

Exit Codes:
  0  成功
  其他非 0 由失败阶段返回
EOF
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
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
    shift
    if args_include_help "$@"; then
      command_usage "$command"
      exit $?
    fi
    case "$command" in
  test)
    run_tests
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
  env-config)
    run_env_config_check "$@"
    ;;
  oss-config)
    run_oss_config_check "$@"
    ;;
  image-inspect)
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
