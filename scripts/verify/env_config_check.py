from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "app" / "core" / "config.py"

DEFAULT_ENV_FILES = (
    ".env.example",
    ".env",
    ".env.dev",
    ".env.test",
    "env_test/.env",
)

DEPLOYMENT_OR_SCRIPT_KEYS = {
    "API_HOST",
    "API_PORT",
    "API_HOST_PORT",
    "BASE_URL",
    "COMPOSE_PROJECT_NAME",
    "DATABASE_PUBLIC_URL",
    "DEV_API_RELOAD",
    "ENV_FILE",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "INTERNAL_BASE_URL",
    "NO_PROXY",
    "POSTGRES_DB",
    "POSTGRES_HOST_PORT",
    "REDIS_HOST_PORT",
    "WATCHFILES_FORCE_POLLING",
    "WORKER_CONCURRENCY",
    "WORKER_LOGLEVEL",
    "WORKER_POOL",
}

DEPRECATED_KEYS = {
    "CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS",
    "CELERY_HARD_TIMEOUT_BUFFER_SECONDS",
    "CELERY_SOFT_TIMEOUT_BUFFER_SECONDS",
    "JOB_STALE_RUNNING_BUFFER_SECONDS",
    "MODEL_CALL_MAX_RETRIES",
    "NOVEL_LOCALIZATION_CHUNKING_ENABLED",
    "NOVEL_LOCALIZATION_CHUNK_SIZE",
    "NOVEL_LOCALIZATION_SINGLE_MAX_CHARS",
    "SHORT_DRAMA_RS_API_KEY",
    "SHORT_DRAMA_RS_RESULT_RESPONSE_FIXTURE_PATH",
    "SHORT_DRAMA_RS_RESULT_SINK",
    "SHORT_DRAMA_RS_SCHEMA_FIXTURE_PATH",
    "SHORT_DRAMA_RS_SCHEMA_SOURCE",
    "SHORT_DRAMA_RS_TAG_SCHEMA_VERSION",
}

KEY_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def settings_keys_from_config() -> set[str]:
    module = ast.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Settings":
            continue
        keys: set[str] = set()
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                continue
            name = statement.target.id
            if name.isupper():
                keys.add(name)
        return keys
    raise RuntimeError(f"Settings class not found: {CONFIG_PATH}")


def parse_keys(path: Path) -> list[tuple[int, str]]:
    keys: list[tuple[int, str]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = KEY_PATTERN.match(line)
        if match:
            keys.append((line_no, match.group(1)))
    return keys


def check_file(path: Path, allowed_keys: set[str]) -> list[str]:
    issues: list[str] = []
    for line_no, key in parse_keys(path):
        normalized_key = key.upper()
        if normalized_key in DEPRECATED_KEYS:
            issues.append(f"{path}:{line_no}: deprecated or unsupported config key: {key}")
        elif normalized_key not in allowed_keys:
            issues.append(f"{path}:{line_no}: unknown config key: {key}")
    return issues


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check env files only expose supported configuration keys.")
    parser.add_argument(
        "files",
        nargs="*",
        help="Env files to check. Defaults to .env.example plus local/test env files when present.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    names = tuple(args.files) if args.files else DEFAULT_ENV_FILES
    allowed_keys = settings_keys_from_config() | DEPLOYMENT_OR_SCRIPT_KEYS
    issues: list[str] = []
    checked = 0
    for name in names:
        path = Path(name)
        if not path.is_absolute():
            path = ROOT_DIR / path
        if not path.exists():
            continue
        checked += 1
        issues.extend(check_file(path, allowed_keys))

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print(f"env config check passed: {checked} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
