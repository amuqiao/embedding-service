#!/usr/bin/env bash
# verify.sh - 本地验证入口
#
# 作用域：承接测试、smoke、e2e、对象存储连通性等一次性验证任务。
# 本地服务生命周期不属于本入口。
# 约束：入口脚本只做参数分发和帮助说明，具体实现下沉到 scripts/verify/ 原子脚本。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/verify/tasks.sh"

usage() {
  cat <<EOF
用法：
  ./scripts/verify.sh <command> [args...]
  ./scripts/verify.sh --help

作用域：
  当前仓库的一次性验证入口。验证任务可以依赖已运行的本地 API/worker，但不负责完整本地服务生命周期。
  mock-smoke 是显式例外：它会临时接管 worker 作为验证夹具，并在结束时仅恢复原本处于运行状态的 worker。

命令：
  test                运行 pytest。
  smoke               对已运行 API 执行真实模型 Job 冒烟验证。
  mock-smoke          不调用真实模型，用 Mock OpenAI + 本地存储验证完整任务流程。
  workflow-smoke      使用真实模型和放大输入验证内部自动分块、canvas 和 merge。
  e2e                 从 .data 读取 .txt，验证 meta、jobs、轮询、callback 和三阶段链路。
  oss                 校验 Aliyun OSS 读写删除连通性，参数透传给 check_aliyun_oss.py。
  check               执行脚本语法检查和 pytest。
  help                显示帮助。

成功标准：
  smoke 成功 = 真实模型 localization job 进入 succeeded 状态。
  mock-smoke 成功 = Mock AI step1_localize job 进入 succeeded 状态，全程不调用真实模型。
  workflow-smoke 成功 = 真实模型长文本触发内部 workflow，localized.txt 和 translated.txt 存在且非空。
  e2e 成功 = meta 契约、错误请求预检、三个 Job、轮询结果、callback 和核心 artifact 均通过校验。
EOF
}

command="${1:-help}"
case "$command" in
  --help|-h|help)
    usage
    ;;
  test)
    run_tests
    ;;
  smoke)
    run_smoke
    ;;
  mock-smoke)
    run_mock_smoke
    ;;
  workflow-smoke)
    shift
    run_workflow_smoke "$@"
    ;;
  e2e)
    shift
    run_e2e "$@"
    ;;
  oss)
    shift
    run_oss_check "$@"
    ;;
  check)
    run_check
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
