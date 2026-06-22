"""Check local TCP port availability for multi-project development.

Text output is for humans and keeps a compact scan result. JSON output is the
stable machine-readable contract for callers that need to parse the result.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

DEFAULT_PORTS = [
    3000,
    3001,
    3002,
    5173,
    5174,
    5175,
    8000,
    8001,
    8080,
    8100,
    18100,
    28100,
    8787,
    5432,
    5433,
    25432,
    35432,
    45432,
    55432,
    6379,
    6380,
    26379,
    36379,
    46379,
    56379,
]


def parse_port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"port must be numeric: {raw}") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"port must be between 1 and 65535: {port}")
    return port


def parse_ports(values: list[str]) -> list[int]:
    if not values:
        return DEFAULT_PORTS

    ports: list[int] = []
    for value in values:
        for item in value.split(","):
            token = item.strip()
            if not token:
                continue
            if "-" in token:
                start_raw, end_raw = token.split("-", 1)
                start = parse_port(start_raw)
                end = parse_port(end_raw)
                if start > end:
                    raise ValueError(f"range start must be <= end: {token}")
                ports.extend(range(start, end + 1))
            else:
                ports.append(parse_port(token))

    result: list[int] = []
    seen: set[int] = set()
    for port in ports:
        if port not in seen:
            result.append(port)
            seen.add(port)
    return result


def command_lines(command: list[str]) -> list[str]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError:
        return []
    output = result.stdout.strip()
    return output.splitlines() if output else []


def list_listeners(port: int) -> list[str]:
    if shutil.which("lsof"):
        return command_lines(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
    if shutil.which("ss"):
        return [line for line in command_lines(["ss", "-H", "-ltnp"]) if f":{port}" in line]
    if shutil.which("netstat"):
        return [line for line in command_lines(["netstat", "-ltnp"]) if f":{port}" in line]
    return []


def has_tcp_listener(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def check_port(host: str, port: int) -> dict[str, Any]:
    listeners = list_listeners(port)
    busy = bool(listeners) or has_tcp_listener(host, port)
    return {
        "port": port,
        "status": "busy" if busy else "free",
        "available": not busy,
        "listeners": listeners,
    }


def build_report(host: str, ports: list[int], count: int) -> dict[str, Any]:
    checks = [check_port(host, port) for port in ports]
    free_ports = [check["port"] for check in checks if check["available"]]
    return {
        "schema_version": 1,
        "kind": "local_port_scan",
        "host": host,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "ok": bool(free_ports),
        "recommended_ports": free_ports[:count],
        "free_ports": free_ports,
        "busy_ports": [check["port"] for check in checks if not check["available"]],
        "checks": checks,
    }


def print_text(report: dict[str, Any]) -> None:
    # Keep text output readable in terminals; callers should use --format json.
    print("== Local Port Scan ==")
    print(f"host: {report['host']}")
    print(f"ok: {str(report['ok']).lower()}")
    print(f"recommended_ports: {','.join(map(str, report['recommended_ports'])) or 'NONE'}")
    print(f"free_ports: {','.join(map(str, report['free_ports'])) or 'NONE'}")
    print(f"busy_ports: {','.join(map(str, report['busy_ports'])) or 'NONE'}")
    print()

    for check in report["checks"]:
        print(f"PORT {check['port']:<6} {check['status'].upper()}")
        if check["listeners"]:
            print("  listeners:")
            for line in check["listeners"]:
                print(f"    {line}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check local TCP ports and recommend free ones.")
    parser.add_argument("positional_ports", nargs="*", help="Ports or ranges, e.g. 3000 5173 8000-8010.")
    parser.add_argument("--ports", action="append", help="Ports or ranges, e.g. 3000,5173,8000-8010.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--count", type=int, default=5, help="How many free ports to recommend.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--allow-busy", action="store_true", help="Exit 0 even when no free port is found.")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be >= 1")
    return args


def main() -> int:
    args = parse_args()
    ports = parse_ports([*(args.ports or []), *args.positional_ports])
    report = build_report(args.host, ports, args.count)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)
    return 0 if report["ok"] or args.allow_busy else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
