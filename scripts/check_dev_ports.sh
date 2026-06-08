#!/usr/bin/env bash
# check_dev_ports.sh - 检查开发服务器端口占用情况
#
# 用法：
#   ./scripts/check_dev_ports.sh
#   ./scripts/check_dev_ports.sh 8100 25432 26379
#
# 默认会扫描本项目可能使用的一组候选宿主机端口，并给出每类服务的第一个空闲端口。

set -u

API_CANDIDATES=(8100 18100 28100 38100 48100)
POSTGRES_CANDIDATES=(25432 15432 35432 45432 55432)
REDIS_CANDIDATES=(26379 16379 36379 46379 56379)

service_name() {
  local port="$1"

  case "$port" in
    8100|18100|28100|38100|48100) printf "api" ;;
    25432|15432|35432|45432|55432) printf "postgres" ;;
    26379|16379|36379|46379|56379) printf "redis" ;;
    *) printf "custom" ;;
  esac
}

print_header() {
  printf "== Dev Server Port Scan ==\n"
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
  local name="${2:-$(service_name "$port")}"
  local listeners
  local containers

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

scan_group() {
  local name="$1"
  shift
  local ports=("$@")
  local port
  local first_free=""
  local busy=0

  printf "## %s candidates\n" "$name"

  for port in "${ports[@]}"; do
    if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
      printf "PORT %-6s %-10s INVALID\n" "$port" "$name"
      busy=$((busy + 1))
      continue
    fi

    if check_port "$port" "$name"; then
      if [[ -z "$first_free" ]]; then
        first_free="$port"
      fi
    else
      busy=$((busy + 1))
    fi
  done

  if [[ -n "$first_free" ]]; then
    printf "recommended_%s_port=%s\n" "$name" "$first_free"
  else
    printf "recommended_%s_port=NONE\n" "$name"
  fi
  printf "\n"
}

scan_custom_ports() {
  local ports=("$@")
  local port
  local busy=0

  printf "## custom ports\n"

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

main() {
  print_header

  if [[ "$#" -gt 0 ]]; then
    scan_custom_ports "$@"
    return
  fi

  scan_group api "${API_CANDIDATES[@]}"
  scan_group postgres "${POSTGRES_CANDIDATES[@]}"
  scan_group redis "${REDIS_CANDIDATES[@]}"

  printf "summary: paste the full output back, especially the recommended_*_port lines.\n"
}

main "$@"
