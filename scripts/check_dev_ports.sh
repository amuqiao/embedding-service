#!/usr/bin/env bash
# check_dev_ports.sh - 检查开发服务器端口占用情况
#
# 用法：
#   ./scripts/check_dev_ports.sh
#   ./scripts/check_dev_ports.sh 8100 15432 16379
#
# 默认检查本项目本地/compose 运行会用到的宿主机端口：
#   8100  API
#   15432 PostgreSQL
#   16379 Redis

set -u

DEFAULT_PORTS=(8100 15432 16379)

service_name() {
  case "$1" in
    8100) printf "api" ;;
    15432) printf "postgres" ;;
    16379) printf "redis" ;;
    *) printf "-" ;;
  esac
}

print_header() {
  printf "== Dev Port Check ==\n"
  printf "host: %s\n" "$(hostname 2>/dev/null || printf unknown)"
  printf "date: %s\n" "$(date '+%Y-%m-%d %H:%M:%S %z' 2>/dev/null || printf unknown)"
  printf "\n"
}

list_tcp_listeners() {
  local port="$1"

  if command -v ss >/dev/null 2>&1; then
    ss -H -ltnp 2>/dev/null | awk -v port=":${port}" '
      $4 == port || $4 ~ port "$" {
        print
      }
    '
    return
  fi

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null
    return
  fi

  if command -v netstat >/dev/null 2>&1; then
    netstat -ltnp 2>/dev/null | awk -v port=":${port}" '
      $4 == port || $4 ~ port "$" {
        print
      }
    '
    return
  fi
}

list_docker_port_users() {
  local port="$1"

  command -v docker >/dev/null 2>&1 || return 0
  docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | awk -v port=":${port}->" '
    index($0, port) > 0 {
      print
    }
  '
}

check_port() {
  local port="$1"
  local name
  local listeners
  local containers

  name="$(service_name "$port")"
  listeners="$(list_tcp_listeners "$port")"
  containers="$(list_docker_port_users "$port")"

  printf "PORT %-6s %-10s " "$port" "$name"

  if [[ -z "$listeners" && -z "$containers" ]]; then
    printf "FREE\n"
    return 0
  fi

  printf "BUSY\n"

  if [[ -n "$listeners" ]]; then
    printf "  listeners:\n"
    printf "%s\n" "$listeners" | sed 's/^/    /'
  else
    printf "  listeners: not found by ss/lsof/netstat\n"
  fi

  if [[ -n "$containers" ]]; then
    printf "  docker containers:\n"
    printf "%s\n" "$containers" | sed 's/^/    /'
  fi

  return 1
}

main() {
  local ports=("$@")
  local busy=0
  local port

  if [[ "${#ports[@]}" -eq 0 ]]; then
    ports=("${DEFAULT_PORTS[@]}")
  fi

  print_header

  for port in "${ports[@]}"; do
    if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
      printf "PORT %-6s %-10s INVALID\n" "$port" "-"
      busy=$((busy + 1))
      continue
    fi

    if ! check_port "$port"; then
      busy=$((busy + 1))
    fi
  done

  printf "\n"
  if (( busy == 0 )); then
    printf "summary: all checked ports are free\n"
    return 0
  fi

  printf "summary: %s checked port(s) are busy or invalid\n" "$busy"
  printf "hint: if process names are missing, rerun with sudo or paste this output directly.\n"
  return 1
}

main "$@"
