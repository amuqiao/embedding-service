from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch one local dev service as a detached process and write its pid.",
    )
    parser.add_argument("--pid-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("command is required after --")

    pid_file = Path(args.pid_file)
    log_file = Path(args.log_file)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with Path("/dev/null").open("rb") as stdin, log_file.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdin=stdin,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )

    try:
        pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    except Exception:
        process.terminate()
        raise


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(1)
