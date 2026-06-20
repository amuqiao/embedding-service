#!/usr/bin/env bash

VERIFY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$VERIFY_DIR/../.." && pwd)}"
source "$ROOT_DIR/scripts/dev/services.sh"

run_tests() {
  section "Test"
  require_executable "$ROOT_DIR/.venv/bin/pytest" "run: ./scripts/dev.sh bootstrap"
  "$ROOT_DIR/.venv/bin/pytest" -q
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
    "$ROOT_DIR/scripts/verify/env_config_check.py" \
    "$ROOT_DIR/scripts/verify/mock_openai_server.py"
  event "OK" "dev/check_ports.py" "py_compile"
  event "OK" "verify/*.py" "py_compile"
}

run_env_config_check() {
  section "Env Config"
  require_executable "$ROOT_DIR/.venv/bin/python" "run: ./scripts/dev.sh bootstrap"
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/verify/env_config_check.py" "$@"
}

run_check() {
  run_script_syntax
  run_cli_smoke
  run_python_syntax
  run_env_config_check "$ROOT_DIR/.env.example"
  run_tests
}
