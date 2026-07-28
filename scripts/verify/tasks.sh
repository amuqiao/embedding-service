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
    "$ROOT_DIR/scripts/run.sh" \
    "$ROOT_DIR/scripts/verify.sh" \
    "$ROOT_DIR/scripts/lib/common.sh" \
    "$ROOT_DIR/scripts/lib/runtime.sh" \
    "$ROOT_DIR/scripts/lib/compose.sh" \
    "$ROOT_DIR/scripts/lib/modes.sh" \
    "$ROOT_DIR/scripts/dev/services.sh" \
    "$ROOT_DIR/scripts/deploy.sh" \
    "$ROOT_DIR/scripts/k8s.sh" \
    "$ROOT_DIR/scripts/redis.sh" \
    "$ROOT_DIR/scripts/load.sh" \
    "$ROOT_DIR/scripts/triton-bench.sh" \
    "$ROOT_DIR/scripts/jobs.sh" \
    "$ROOT_DIR/scripts/real-flow.sh" \
    "$ROOT_DIR/scripts/models.sh" \
    "$ROOT_DIR/scripts/media.sh" \
    "$ROOT_DIR/scripts/tools.sh" \
    "$ROOT_DIR/scripts/verify/tasks.sh" \
    "$ROOT_DIR/scripts/verify/release_flow_smoke.sh" \
    "$ROOT_DIR/deploy/release-test.sh" \
    "$ROOT_DIR/deploy/release-master.sh" \
    "$ROOT_DIR/deploy/lib/release-flow.sh"
  do
    bash -n "$script"
    event "OK" "${script#$ROOT_DIR/}" "syntax"
  done
}

assert_generated_commands_help() {
  local name="$1"
  local entry="$2"
  shift 2
  local command
  local duplicate_count=0
  local manual_section
  local output

  output="$("$entry" --help)"
  if [[ "$output" != *"Commands:"* ]]; then
    echo "ERROR: $name help must include generated Commands." >&2
    return 1
  fi
  if [[ "$output" == *"命令说明："* ]]; then
    echo "ERROR: $name help must not duplicate generated Commands with 手写命令说明。" >&2
    return 1
  fi
  manual_section="$(printf '%s\n' "$output" | awk '
    /^  (作用域|默认行为|环境变量|配置与环境变量|输出|关键概念|常用示例|进阶用法|保护边界|副作用与保护边界)：/ || /^  Exit Codes:/ { in_manual = 1 }
    in_manual { print }
  ')"
  for command in "$@"; do
    if printf '%s\n' "$manual_section" | grep -Eq "^[[:space:]]+${command}[[:space:]]{2,}"; then
      duplicate_count=$((duplicate_count + 1))
    fi
  done
  if (( duplicate_count >= 2 )); then
    echo "ERROR: $name help must not repeat generated command catalog in manual sections." >&2
    return 1
  fi
}

run_cli_smoke() {
  section "CLI"
  # help smoke 只验证入口可用，不重复打印完整 help，避免 check 输出噪声。
  "$ROOT_DIR/scripts/dev.sh" --help >/dev/null
  event "OK" "dev.sh" "help"
  "$ROOT_DIR/scripts/run.sh" --help >/dev/null
  event "OK" "run.sh" "help"
  "$ROOT_DIR/scripts/verify.sh" --help >/dev/null
  event "OK" "verify.sh" "help"
  "$ROOT_DIR/scripts/deploy.sh" --help >/dev/null
  event "OK" "deploy.sh" "help"
  "$ROOT_DIR/scripts/k8s.sh" --help >/dev/null
  event "OK" "k8s.sh" "help"
  assert_generated_commands_help "redis.sh" "$ROOT_DIR/scripts/redis.sh" \
    check broker memory keyspace top-keys capability
  event "OK" "redis.sh" "help"
  assert_generated_commands_help "jobs.sh" "$ROOT_DIR/scripts/jobs.sh" \
    guide dashboard overview observe broker runtime list show job inspect trace payload diagnose workflow timeline attempts ai-calls callbacks callbacks-summary stuck drain pressure summary doctor failures latency ingress capacity types
  event "OK" "jobs.sh" "help"
  assert_generated_commands_help "real-flow.sh" "$ROOT_DIR/scripts/real-flow.sh" \
    doctor llm-job-billing llm-job-double-billing oss-upload-image poster-title-image
  event "OK" "real-flow.sh" "help"
  assert_generated_commands_help "load.sh" "$ROOT_DIR/scripts/load.sh" \
    guide cases list profiles init smoke run ui report pressure drain
  event "OK" "load.sh" "help"
  assert_generated_commands_help "triton-bench.sh" "$ROOT_DIR/scripts/triton-bench.sh" \
    doctor run
  event "OK" "triton-bench.sh" "help"
  "$ROOT_DIR/scripts/models.sh" --help >/dev/null
  event "OK" "models.sh" "help"
  "$ROOT_DIR/scripts/media.sh" --help >/dev/null
  event "OK" "media.sh" "help"
  "$ROOT_DIR/scripts/tools.sh" --help >/dev/null
  event "OK" "tools.sh" "help"
  "$ROOT_DIR/deploy/release-test.sh" --help >/dev/null
  event "OK" "release-test.sh" "help"
  "$ROOT_DIR/deploy/release-master.sh" --help >/dev/null
  event "OK" "release-master.sh" "help"

  "$ROOT_DIR/scripts/dev.sh" start --help >/dev/null
  "$ROOT_DIR/scripts/dev.sh" start api --help >/dev/null
  "$ROOT_DIR/scripts/dev.sh" migrate --help >/dev/null
  "$ROOT_DIR/scripts/run.sh" up --help >/dev/null
  "$ROOT_DIR/scripts/run.sh" up dev --help >/dev/null
  "$ROOT_DIR/scripts/run.sh" status --help >/dev/null
  "$ROOT_DIR/scripts/run.sh" status dev --help >/dev/null
  "$ROOT_DIR/scripts/run.sh" down --help >/dev/null
  "$ROOT_DIR/scripts/run.sh" down dev --help >/dev/null
  "$ROOT_DIR/scripts/deploy.sh" up --help >/dev/null
  "$ROOT_DIR/scripts/deploy.sh" up compose-full --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" check --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" check oss --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" check oss --confirm --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" migrate --confirm --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" check --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" broker --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" memory --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" keyspace --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" capability --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" top-keys --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" migration-roundtrip --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" migration-roundtrip ignored --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" env-config --help >/dev/null
  "$ROOT_DIR/scripts/models.sh" list --help >/dev/null
  "$ROOT_DIR/scripts/models.sh" status --help >/dev/null
  "$ROOT_DIR/scripts/models.sh" verify --help >/dev/null
  "$ROOT_DIR/scripts/models.sh" inspect --help >/dev/null
  "$ROOT_DIR/scripts/models.sh" download --help >/dev/null
  "$ROOT_DIR/scripts/media.sh" audio --help >/dev/null
  "$ROOT_DIR/scripts/media.sh" audio probe --help >/dev/null
  "$ROOT_DIR/scripts/media.sh" audio verify --help >/dev/null
  "$ROOT_DIR/scripts/media.sh" audio prepare --help >/dev/null
  "$ROOT_DIR/scripts/media.sh" video --help >/dev/null
  "$ROOT_DIR/scripts/tools.sh" secret --help >/dev/null
  "$ROOT_DIR/scripts/tools.sh" registry --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" guide --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" dashboard --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" observe --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" broker --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" runtime --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" failures --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" callbacks-summary --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" ingress --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" capacity --help >/dev/null
  "$ROOT_DIR/scripts/jobs.sh" list --help >/dev/null
  "$ROOT_DIR/scripts/real-flow.sh" doctor --help >/dev/null
  "$ROOT_DIR/scripts/real-flow.sh" llm-job-billing --confirm-cost --help >/dev/null
  "$ROOT_DIR/scripts/real-flow.sh" oss-upload-image --confirm-upload --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" guide --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" cases --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" profiles --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" init --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" smoke --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" run --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" ui --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" report --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" pressure --help >/dev/null
  "$ROOT_DIR/scripts/load.sh" drain --help >/dev/null
  "$ROOT_DIR/scripts/triton-bench.sh" doctor --help >/dev/null
  "$ROOT_DIR/scripts/triton-bench.sh" run --help >/dev/null
  "$ROOT_DIR/deploy/release-test.sh" prepare --help >/dev/null
  "$ROOT_DIR/deploy/release-test.sh" --push --help >/dev/null
  "$ROOT_DIR/deploy/release-master.sh" prepare --help >/dev/null
  "$ROOT_DIR/deploy/release-master.sh" --push --help >/dev/null
  event "OK" "subcommands" "help"
}

run_release_flow_smoke() {
  section "Release Flow"
  "$ROOT_DIR/scripts/verify/release_flow_smoke.sh"
  event "OK" "release-flow" "prepare/status/push and safety gates"
}

run_python_syntax() {
  section "Python"
  require_project_python
  # py_compile 成功时汇总为脚本事件；失败时保留 Python 原始错误。
  "$PYTHON_BIN" -m py_compile \
    "$ROOT_DIR/scripts/dev/check_ports.py" \
    "$ROOT_DIR/scripts/verify/alembic_revision_check.py" \
    "$ROOT_DIR/scripts/verify/env_config_check.py" \
    "$ROOT_DIR/scripts/verify/job_workflow_smoke.py" \
    "$ROOT_DIR/scripts/verify/image_inspect.py" \
    "$ROOT_DIR/scripts/verify/migration_roundtrip.py" \
    "$ROOT_DIR/scripts/verify/oss_config_check.py" \
    "$ROOT_DIR/scripts/verify/registry_check.py" \
    "$ROOT_DIR/scripts/verify/workflow_modes_smoke.py" \
    "$ROOT_DIR/scripts/load/__init__.py" \
    "$ROOT_DIR/scripts/load/cli.py" \
    "$ROOT_DIR/scripts/load/cases.py" \
    "$ROOT_DIR/scripts/load/locustfile.py" \
    "$ROOT_DIR/scripts/load/profiles.py" \
    "$ROOT_DIR/scripts/load/support.py" \
    "$ROOT_DIR/scripts/triton_bench/__init__.py" \
    "$ROOT_DIR/scripts/triton_bench/cli.py" \
    "$ROOT_DIR/scripts/jobs/__init__.py" \
    "$ROOT_DIR/scripts/jobs/cli.py" \
    "$ROOT_DIR/scripts/jobs/db.py" \
    "$ROOT_DIR/scripts/jobs/formatters.py" \
    "$ROOT_DIR/scripts/jobs/queries.py" \
    "$ROOT_DIR/scripts/redis_diag/__init__.py" \
    "$ROOT_DIR/scripts/redis_diag/cli.py" \
    "$ROOT_DIR/scripts/models/__init__.py" \
    "$ROOT_DIR/scripts/models/inspect_onnx.py" \
    "$ROOT_DIR/scripts/real_flow/__init__.py" \
    "$ROOT_DIR/scripts/real_flow/cli.py" \
    "$ROOT_DIR/scripts/real_flow/flows/__init__.py" \
    "$ROOT_DIR/scripts/real_flow/flows/audio_stem_separation.py" \
    "$ROOT_DIR/scripts/real_flow/flows/llm_job_billing.py" \
    "$ROOT_DIR/scripts/real_flow/flows/oss_image_upload.py" \
    "$ROOT_DIR/scripts/real_flow/flows/poster_title_image.py" \
    "$ROOT_DIR/scripts/media/__init__.py" \
    "$ROOT_DIR/scripts/media/audio.py" \
    "$ROOT_DIR/scripts/media/video.py" \
    "$ROOT_DIR/scripts/tools/env_url.py" \
    "$ROOT_DIR/scripts/tools/registry.py"
  event "OK" "dev/check_ports.py" "py_compile"
  event "OK" "verify/*.py" "py_compile"
  event "OK" "load/*.py" "py_compile"
  event "OK" "triton_bench/*.py" "py_compile"
  event "OK" "jobs/*.py" "py_compile"
  event "OK" "redis_diag/*.py" "py_compile"
  event "OK" "models/*.py" "py_compile"
  event "OK" "real_flow/*.py" "py_compile"
  event "OK" "media/*.py" "py_compile"
  event "OK" "tools/*.py" "py_compile"
}

run_alembic_revision_check() {
  section "Alembic"
  require_project_python
  "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/alembic_revision_check.py"
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

run_oss_config_check() {
  section "OSS Config"
  require_project_python
  "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/oss_config_check.py" "$@"
}

run_image_inspect() {
  local arg
  local json_output=false
  for arg in "$@"; do
    if [[ "$arg" == "--json" ]]; then
      json_output=true
    fi
  done
  if [[ "$json_output" != "true" ]]; then
    section "Image Inspect"
  fi
  require_project_python
  "$PYTHON_BIN" "$ROOT_DIR/scripts/verify/image_inspect.py" "$@"
}

run_check() {
  run_script_syntax
  run_cli_smoke
  run_release_flow_smoke
  run_python_syntax
  run_env_config_check
  run_alembic_revision_check
  run_registry_check
  run_tests
}
