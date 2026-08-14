#!/usr/bin/env bash
# common.sh - 脚本公共路径、env 读取和人读输出 helper
#
# 输出规则：
#   section 标记稳定阶段，例如 "== Env Config =="。
#   event 输出状态词、对象和简短结果，例如 "OK .env.example present"。
#   row/detail 用于 status 类表格和补充 PID、URL、日志、端口等证据。
#   die 只用于失败出口，错误写入 stderr，并给出对象、原因或下一步。
# 新增脚本输出时优先复用这些 helper，不在子脚本中临时 echo 新格式。

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "$COMMON_DIR/../.." && pwd)}"
RUN_DIR="${RUN_DIR:-$ROOT_DIR/.run}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"

section() {
  printf "\n== %s ==\n" "$1"
}

row() {
  printf "  %-14s %-10s %s\n" "$1" "$2" "${3:-}"
}

detail() {
  printf "    %-9s %s\n" "${1}:" "$2"
}

event() {
  printf "%-9s %-10s %s\n" "$1" "$2" "${3:-}"
}

die() {
  printf "ERROR: %s\n" "$1" >&2
  exit "${2:-1}"
}

args_include_help() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -h|--help)
        return 0
        ;;
    esac
  done
  return 1
}

require_command() {
  local name="$1"
  local hint="$2"
  command -v "$name" >/dev/null 2>&1 || die "$name is not available; $hint" 2
}

require_executable() {
  local path="$1"
  local hint="$2"
  [[ -x "$path" ]] || die "$path not found or not executable; $hint" 2
}

resolve_repo_path() {
  local path="$1"
  case "$path" in
    /*) printf "%s" "$path" ;;
    *) printf "%s/%s" "$ROOT_DIR" "$path" ;;
  esac
}

env_value_from() {
  local key="$1"
  local path="$2"
  [[ -f "$path" ]] || return 0
  grep -E "^${key}=" "$path" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

application_env_file() {
  resolve_repo_path "${ENV_FILE:-.env}"
}

env_value() {
  local key="$1"
  env_value_from "$key" "$(application_env_file)"
}

assert_local_url() {
  local key="$1"
  local env_file
  local value
  env_file="$(application_env_file)"
  value="$(env_value "$key")"
  [[ -n "$value" ]] || return 0

  case "$value" in
    *127.0.0.1*|*localhost*|*0.0.0.0*|*//postgres:*|*@postgres:*|*//redis:*|*@redis:*|*host.docker.internal*)
      return 0
      ;;
  esac

  die "$key in $env_file does not look local: $value" 3
}

guard_local_env() {
  local env_file
  env_file="$(application_env_file)"
  [[ -f "$env_file" ]] || die "$env_file not found; run: ./scripts/dev.sh bootstrap or set ENV_FILE to an existing file" 2
  assert_local_url DATABASE_URL
  assert_local_url REDIS_URL
}

project_python_bin() {
  local prefer_env="${1:-true}"
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if [[ "$prefer_env" == "true" ]]; then
      require_command "$PYTHON_BIN" "install Python 3 or set PYTHON_BIN"
      printf "%s" "$PYTHON_BIN"
      return
    fi
  fi
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    printf "%s" "$ROOT_DIR/.venv/bin/python"
  elif [[ -n "${PYTHON_BIN:-}" && "$prefer_env" == "true" ]]; then
    require_command "$PYTHON_BIN" "install Python 3 or set PYTHON_BIN"
    printf "%s" "$PYTHON_BIN"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    die "python is not available; run: ./scripts/dev.sh bootstrap" 2
  fi
}

exec_project_python_module() {
  local module="$1"
  shift
  local python_bin
  python_bin="$(project_python_bin)"
  PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONUNBUFFERED=1 \
    exec "$python_bin" -m "$module" "$@"
}

exec_repo_python_module() {
  local module="$1"
  shift
  local python_bin
  python_bin="$(project_python_bin false)"
  PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONUNBUFFERED=1 \
    exec "$python_bin" -m "$module" "$@"
}
