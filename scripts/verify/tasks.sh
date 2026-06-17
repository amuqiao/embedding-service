#!/usr/bin/env bash

VERIFY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$VERIFY_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/dev/services.sh"

run_tests() {
  section "Test"
  require_executable "$ROOT_DIR/.venv/bin/pytest" "run: ./scripts/dev.sh bootstrap"
  "$ROOT_DIR/.venv/bin/pytest" -q
}

run_smoke() {
  guard_local_env
  section "Smoke"
  require_executable "$ROOT_DIR/.venv/bin/python" "run: ./scripts/dev.sh bootstrap"
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/verify/smoke_job.py"
}

run_rs_translation_smoke() {
  guard_local_env
  section "RS Translation Smoke"
  require_executable "$ROOT_DIR/.venv/bin/python" "run: ./scripts/dev.sh bootstrap"
  PYTHONUNBUFFERED=1 "$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/scripts/verify/rs_tag_schema_translation_job.py" \
    "$@"
}

run_e2e() {
  guard_local_env
  section "E2E"
  require_executable "$ROOT_DIR/.venv/bin/python" "run: ./scripts/dev.sh bootstrap"
  PYTHONUNBUFFERED=1 "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/verify/e2e_backend_call.py" "${@:1}"
}

run_workflow_smoke() {
  guard_local_env
  section "Workflow Smoke"
  require_executable "$ROOT_DIR/.venv/bin/python" "run: ./scripts/dev.sh bootstrap"
  PYTHONUNBUFFERED=1 "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/verify/e2e_backend_call.py" \
    --repeat-input "${WORKFLOW_SMOKE_REPEAT_INPUT:-50}" \
    "${@:1}"
}

run_mock_smoke() {
  guard_local_env
  section "Mock Smoke"
  require_executable "$ROOT_DIR/.venv/bin/python" "run: ./scripts/dev.sh bootstrap"

  local mock_port="${MOCK_OPENAI_PORT:-18200}"
  local mock_pid_file="${RUN_DIR}/mock_openai.pid"
  local mock_log="${LOG_DIR}/mock_openai.log"
  local mock_worker_pid_file="${RUN_DIR}/mock_worker.pid"
  local mock_worker_log="${LOG_DIR}/mock_worker.log"
  local worker_pid_file="${RUN_DIR}/worker.pid"
  local worker_pid=""
  local restore_worker=0

  worker_pid="$(cat "$worker_pid_file" 2>/dev/null || true)"
  if [[ -n "$worker_pid" ]] && kill -0 "$worker_pid" 2>/dev/null; then
    restore_worker=1
  fi

  trap '
    p="$(cat "'"$mock_worker_pid_file"'" 2>/dev/null || true)"
    [[ -n "$p" ]] && kill "$p" 2>/dev/null || true
    rm -f "'"$mock_worker_pid_file"'"
    p="$(cat "'"$mock_pid_file"'" 2>/dev/null || true)"
    [[ -n "$p" ]] && kill "$p" 2>/dev/null || true
    rm -f "'"$mock_pid_file"'"
    if [[ "'"$restore_worker"'" == "1" ]]; then
      start_service worker
    fi
  ' EXIT

  stop_service worker 2>/dev/null || true

  rm -f "$mock_pid_file"
  nohup "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/verify/mock_openai_server.py" "$mock_port" \
    > "$mock_log" 2>&1 &
  echo $! > "$mock_pid_file"
  sleep 1
  if ! kill -0 "$(cat "$mock_pid_file")" 2>/dev/null; then
    die "mock OpenAI server 启动失败; 日志: $mock_log"
  fi
  event "STARTED" "mock-openai" "http://127.0.0.1:${mock_port}"

  rm -f "$mock_worker_pid_file"
  nohup bash -c "
    export STORAGE_BACKEND=local
    export OPENAI_BASE_URL=http://127.0.0.1:${mock_port}
    export OPENAI_API_KEY=mock-key
    exec \"$ROOT_DIR/start-worker.sh\"
  " > "$mock_worker_log" 2>&1 &
  echo $! > "$mock_worker_pid_file"
  sleep 2
  if ! kill -0 "$(cat "$mock_worker_pid_file")" 2>/dev/null; then
    die "mock worker 启动失败; 日志: $mock_worker_log"
  fi
  event "STARTED" "mock-worker" "storage=local ai=mock port=${mock_port}"

  STORAGE_BACKEND=local "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/verify/smoke_job.py"
}

run_oss_check() {
  section "OSS"
  require_executable "$ROOT_DIR/.venv/bin/python" "run: ./scripts/dev.sh bootstrap"
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/verify/check_aliyun_oss.py" "$@"
}

run_script_syntax() {
  local script
  section "Script"
  for script in \
    "$ROOT_DIR/scripts/dev.sh" \
    "$ROOT_DIR/scripts/verify.sh" \
    "$ROOT_DIR/scripts/lib/common.sh" \
    "$ROOT_DIR/scripts/dev/services.sh" \
    "$ROOT_DIR/scripts/verify/tasks.sh"
  do
    bash -n "$script"
    event "OK" "${script#$ROOT_DIR/}" "syntax"
  done
}

run_cli_smoke() {
  section "CLI"
  "$ROOT_DIR/scripts/dev.sh" --help >/dev/null
  event "OK" "dev.sh" "help"
  "$ROOT_DIR/scripts/verify.sh" --help >/dev/null
  event "OK" "verify.sh" "help"
}

run_python_syntax() {
  section "Python"
  require_executable "$ROOT_DIR/.venv/bin/python" "run: ./scripts/dev.sh bootstrap"
  "$ROOT_DIR/.venv/bin/python" -m py_compile \
    "$ROOT_DIR/scripts/dev/check_ports.py" \
    "$ROOT_DIR/scripts/verify/check_aliyun_oss.py" \
    "$ROOT_DIR/scripts/verify/e2e_backend_call.py" \
    "$ROOT_DIR/scripts/verify/mock_openai_server.py" \
    "$ROOT_DIR/scripts/verify/rs_tag_schema_translation_job.py" \
    "$ROOT_DIR/scripts/verify/smoke_job.py"
  event "OK" "dev/check_ports.py" "py_compile"
  event "OK" "verify/*.py" "py_compile"
}

run_check() {
  run_script_syntax
  run_cli_smoke
  run_python_syntax
  run_tests
}
