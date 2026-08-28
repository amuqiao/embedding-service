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
  ENABLED_JOB_TYPES= "$PYTHON_BIN" -m pytest -q
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
    "$ROOT_DIR/scripts/k8s/ops.sh" \
    "$ROOT_DIR/scripts/redis.sh" \
    "$ROOT_DIR/scripts/oss.sh" \
    "$ROOT_DIR/scripts/load.sh" \
    "$ROOT_DIR/scripts/triton-bench.sh" \
    "$ROOT_DIR/scripts/jobs.sh" \
    "$ROOT_DIR/scripts/smoke.sh" \
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

assert_generated_entrypoint_contract() {
  local name="$1"
  local entry="$2"
  shift 2
  local command
  local duplicate_count=0
  local forbidden_title
  local manual_section
  local output

  output="$("$entry" --help)"
  if [[ "$output" != *"Commands:"* ]]; then
    echo "ERROR: $name help must expose the generated Commands section required by the current script contract." >&2
    return 1
  fi
  for forbidden_title in "命令说明：" "命令列表：" "子命令索引："; do
    if [[ "$output" == *"$forbidden_title"* ]]; then
      echo "ERROR: $name help must not keep a manual command catalog titled $forbidden_title; use generated Commands plus examples." >&2
      return 1
    fi
  done
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
    echo "ERROR: $name help must not repeat the generated command catalog in manual sections." >&2
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
  assert_generated_entrypoint_contract "redis.sh" "$ROOT_DIR/scripts/redis.sh" \
    check broker memory keyspace top-keys capability
  event "OK" "redis.sh" "help"
  "$ROOT_DIR/scripts/oss.sh" --help >/dev/null
  event "OK" "oss.sh" "help"
  assert_generated_entrypoint_contract "jobs.sh" "$ROOT_DIR/scripts/jobs.sh" \
    guide dashboard overview observe broker runtime list show job inspect trace payload diagnose workflow timeline attempts ai-calls callbacks callbacks-summary stuck drain pressure summary doctor failures latency ingress capacity types
  event "OK" "jobs.sh" "help"
  "$ROOT_DIR/scripts/smoke.sh" --help >/dev/null
  event "OK" "smoke.sh" "help"
  assert_generated_entrypoint_contract "load.sh" "$ROOT_DIR/scripts/load.sh" \
    guide cases list profiles init smoke run ui report pressure drain
  event "OK" "load.sh" "help"
  assert_generated_entrypoint_contract "triton-bench.sh" "$ROOT_DIR/scripts/triton-bench.sh" \
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
  "$ROOT_DIR/scripts/run.sh" restart --help >/dev/null
  "$ROOT_DIR/scripts/run.sh" restart dev --help >/dev/null
  "$ROOT_DIR/scripts/deploy.sh" up --help >/dev/null
  "$ROOT_DIR/scripts/deploy.sh" up compose-full --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" check --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" check dashboard --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" check oss --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" check oss --confirm --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" current --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" heads --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" history --help >/dev/null
  "$ROOT_DIR/scripts/k8s.sh" migrate --confirm --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" check --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" broker --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" memory --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" keyspace --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" capability --help >/dev/null
  "$ROOT_DIR/scripts/redis.sh" top-keys --help >/dev/null
  "$ROOT_DIR/scripts/oss.sh" check --help >/dev/null
  "$ROOT_DIR/scripts/oss.sh" upload-image --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" test --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" check --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" workflow-smoke --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" workflow-modes-smoke --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" migration-roundtrip --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" migration-roundtrip ignored --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" env-config --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" oss-config --help >/dev/null
  "$ROOT_DIR/scripts/verify.sh" image-inspect --help >/dev/null
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
  "$ROOT_DIR/scripts/smoke.sh" health --help >/dev/null
  "$ROOT_DIR/scripts/smoke.sh" ready --help >/dev/null
  "$ROOT_DIR/scripts/smoke.sh" list --help >/dev/null
  "$ROOT_DIR/scripts/smoke.sh" llm-job-billing --confirm-cost --help >/dev/null
  "$ROOT_DIR/scripts/smoke.sh" tagged-text-translation --confirm-cost --help >/dev/null
  "$ROOT_DIR/scripts/smoke.sh" oss-upload-image --confirm-upload --help >/dev/null
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
    "$ROOT_DIR/scripts/dev/launch_service.py" \
    "$ROOT_DIR/scripts/verify/alembic_revision_check.py" \
    "$ROOT_DIR/scripts/verify/env_config_check.py" \
    "$ROOT_DIR/scripts/verify/job_workflow_smoke.py" \
    "$ROOT_DIR/scripts/verify/image_inspect.py" \
    "$ROOT_DIR/scripts/verify/migration_roundtrip.py" \
    "$ROOT_DIR/scripts/verify/registry_check.py" \
    "$ROOT_DIR/scripts/verify/workflow_modes_smoke.py" \
    "$ROOT_DIR/scripts/oss/__init__.py" \
    "$ROOT_DIR/scripts/oss/cli.py" \
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
    "$ROOT_DIR/smoke/__init__.py" \
    "$ROOT_DIR/smoke/__main__.py" \
    "$ROOT_DIR/smoke/cli.py" \
    "$ROOT_DIR/smoke/scenarios.py" \
    "$ROOT_DIR/smoke/flows/__init__.py" \
    "$ROOT_DIR/smoke/flows/audio/__init__.py" \
    "$ROOT_DIR/smoke/flows/audio/stem_separation.py" \
    "$ROOT_DIR/smoke/flows/examples/__init__.py" \
    "$ROOT_DIR/smoke/flows/examples/lifecycle_probe.py" \
    "$ROOT_DIR/smoke/flows/image/__init__.py" \
    "$ROOT_DIR/smoke/flows/image/adapter_probe.py" \
    "$ROOT_DIR/smoke/flows/image/poster_title_image.py" \
    "$ROOT_DIR/smoke/flows/llm/__init__.py" \
    "$ROOT_DIR/smoke/flows/llm/billing.py" \
    "$ROOT_DIR/smoke/flows/oss/__init__.py" \
    "$ROOT_DIR/smoke/flows/oss/image_upload.py" \
    "$ROOT_DIR/smoke/flows/translation/__init__.py" \
    "$ROOT_DIR/smoke/flows/translation/tagged_text_translation.py" \
    "$ROOT_DIR/smoke/harness/__init__.py" \
    "$ROOT_DIR/smoke/harness/callback_capture.py" \
    "$ROOT_DIR/smoke/harness/cli_contract.py" \
    "$ROOT_DIR/smoke/harness/env_runtime.py" \
    "$ROOT_DIR/smoke/harness/errors.py" \
    "$ROOT_DIR/smoke/harness/formatters.py" \
    "$ROOT_DIR/smoke/harness/http_runtime.py" \
    "$ROOT_DIR/smoke/harness/service_runtime.py" \
    "$ROOT_DIR/smoke/jobs/__init__.py" \
    "$ROOT_DIR/smoke/jobs/callback.py" \
    "$ROOT_DIR/smoke/jobs/cli_contract.py" \
    "$ROOT_DIR/smoke/jobs/runtime.py" \
    "$ROOT_DIR/scripts/media/__init__.py" \
    "$ROOT_DIR/scripts/media/audio.py" \
    "$ROOT_DIR/scripts/media/video.py" \
    "$ROOT_DIR/scripts/tools/env_url.py" \
    "$ROOT_DIR/scripts/tools/registry.py"
  event "OK" "dev/*.py" "py_compile"
  event "OK" "verify/*.py" "py_compile"
  event "OK" "oss/*.py" "py_compile"
  event "OK" "load/*.py" "py_compile"
  event "OK" "triton_bench/*.py" "py_compile"
  event "OK" "jobs/*.py" "py_compile"
  event "OK" "redis_diag/*.py" "py_compile"
  event "OK" "models/*.py" "py_compile"
  event "OK" "smoke/*.py" "py_compile"
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
  local arg
  local json_output=false
  for arg in "$@"; do
    if [[ "$arg" == "--json" ]]; then
      json_output=true
    fi
  done
  if [[ "$json_output" != "true" ]]; then
    section "OSS Config"
  fi
  "$ROOT_DIR/scripts/oss.sh" check "$@"
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

run_check() (
  export ENABLED_JOB_TYPES=
  run_script_syntax
  run_cli_smoke
  run_release_flow_smoke
  run_python_syntax
  run_env_config_check
  run_alembic_revision_check
  run_registry_check
  run_tests
)
