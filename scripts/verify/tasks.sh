#!/usr/bin/env bash
# tasks.sh - verify.sh 的一次性验证任务编排
#
# 输出原则：
#   check 类阶段先打印 section，再把可归纳的检查压缩为 OK 事件。
#   pytest 和 workflow-smoke 的输出是验证结果本身，允许在对应 section 下透传。
#   子任务失败时保留工具错误，由 set -e 终止，调用者根据 section 定位失败阶段。

VERIFY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$VERIFY_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/lib/runtime.sh"

run_tests() {
  section "Test"
  require_project_python
  "$PYTHON_BIN" -m pytest -q
}

run_workflow_smoke() {
  section "Workflow Smoke"
  require_project_python
  "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/job_workflow_smoke.py" --api-url "$API_URL"
}

run_workflow_modes_smoke() {
  section "Workflow Modes Smoke"
  require_project_python
  "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/workflow_modes_smoke.py" --api-url "$API_URL"
}

run_migration_roundtrip() {
  section "Migration Roundtrip"
  require_project_python
  "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/migration_roundtrip.py"
}

run_script_syntax() {
  local script
  section "Script"
  # 语法检查不透传 bash -n 的成功输出；每个脚本成功后输出一行 OK。
  for script in \
    "$ROOT_DIR/scripts/dev.sh" \
    "$ROOT_DIR/scripts/verify.sh" \
    "$ROOT_DIR/scripts/lib/common.sh" \
    "$ROOT_DIR/scripts/lib/runtime.sh" \
    "$ROOT_DIR/scripts/lib/compose.sh" \
    "$ROOT_DIR/scripts/dev/services.sh" \
    "$ROOT_DIR/scripts/deploy.sh" \
    "$ROOT_DIR/scripts/jobs.sh" \
    "$ROOT_DIR/scripts/real-flow.sh" \
    "$ROOT_DIR/scripts/verify/tasks.sh"
  do
    bash -n "$script"
    event "OK" "${script#$ROOT_DIR/}" "syntax"
  done
}

run_cli_smoke() {
  section "CLI"
  # help smoke 只验证入口可用，不重复打印完整 help，避免 check 输出噪声。
  "$ROOT_DIR/scripts/dev.sh" --help >/dev/null
  event "OK" "dev.sh" "help"
  "$ROOT_DIR/scripts/verify.sh" --help >/dev/null
  event "OK" "verify.sh" "help"
  "$ROOT_DIR/scripts/deploy.sh" --help >/dev/null
  event "OK" "deploy.sh" "help"
  "$ROOT_DIR/scripts/jobs.sh" --help >/dev/null
  event "OK" "jobs.sh" "help"
  "$ROOT_DIR/scripts/real-flow.sh" --help >/dev/null
  event "OK" "real-flow.sh" "help"
}

run_python_syntax() {
  section "Python"
  require_project_python
  # py_compile 成功时汇总为脚本事件；失败时保留 Python 原始错误。
  "$PYTHON_BIN" -m py_compile \
    "$ROOT_DIR/scripts/dev/check_ports.py" \
    "$ROOT_DIR/scripts/verify/env_config_check.py" \
    "$ROOT_DIR/scripts/verify/job_workflow_smoke.py" \
    "$ROOT_DIR/scripts/verify/migration_roundtrip.py" \
    "$ROOT_DIR/scripts/verify/registry_check.py" \
    "$ROOT_DIR/scripts/verify/workflow_modes_smoke.py" \
    "$ROOT_DIR/scripts/jobs/__init__.py" \
    "$ROOT_DIR/scripts/jobs/cli.py" \
    "$ROOT_DIR/scripts/jobs/db.py" \
    "$ROOT_DIR/scripts/jobs/formatters.py" \
    "$ROOT_DIR/scripts/jobs/queries.py" \
    "$ROOT_DIR/scripts/real_flow/__init__.py" \
    "$ROOT_DIR/scripts/real_flow/cli.py" \
    "$ROOT_DIR/scripts/real_flow/flows/__init__.py" \
    "$ROOT_DIR/scripts/real_flow/flows/llm_job_billing.py"
  event "OK" "dev/check_ports.py" "py_compile"
  event "OK" "verify/*.py" "py_compile"
  event "OK" "jobs/*.py" "py_compile"
  event "OK" "real_flow/*.py" "py_compile"
}

run_registry_check() {
  section "Registry"
  require_project_python
  "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/registry_check.py"
}

run_env_config_check() {
  section "Env Config"
  require_project_python
  "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/env_config_check.py" "$@"
}

run_check() {
  run_script_syntax
  run_cli_smoke
  run_python_syntax
  run_env_config_check
  run_registry_check
  run_tests
}
