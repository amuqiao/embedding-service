"""Check root env files against their explicit key manifests.

This script is called under the shell "Env Config" section. Success is printed
as one OK event; issues go to stderr with file, line, object, and reason.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "app" / "core" / "config.py"
SERVICE_EXAMPLE_PATH = ROOT_DIR / ".env.example"

KEY_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
APP_ENV_VALUES = ("local", "dev", "test", "prd")

def constant_keys_from_config(name: str) -> frozenset[str]:
    module = ast.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value_node = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value_node = node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id != name:
            continue
        if value_node is None:
            break
        if isinstance(value_node, ast.Call) and isinstance(value_node.func, ast.Name) and value_node.func.id == "frozenset":
            if len(value_node.args) != 1:
                raise RuntimeError(f"{name} frozenset must have exactly one argument: {CONFIG_PATH}")
            value = ast.literal_eval(value_node.args[0])
        else:
            value = ast.literal_eval(value_node)
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise RuntimeError(f"{name} must be a string-keyed dict: {CONFIG_PATH}")
            return frozenset(value)
        if isinstance(value, (set, frozenset)):
            if not all(isinstance(key, str) for key in value):
                raise RuntimeError(f"{name} must contain strings: {CONFIG_PATH}")
            return frozenset(value)
        raise RuntimeError(f"{name} must be a dict or set literal: {CONFIG_PATH}")
    raise RuntimeError(f"{name} not found: {CONFIG_PATH}")


APPLICATION_ENV_KEYS = constant_keys_from_config("APPLICATION_ENV_FIELD_MAP")
LAUNCHER_ENV_KEYS = constant_keys_from_config("LAUNCHER_ENV_KEYS")
POC_ENV_KEYS = constant_keys_from_config("POC_ENV_KEYS")
ROOT_ENV_KEYS = APPLICATION_ENV_KEYS | LAUNCHER_ENV_KEYS | POC_ENV_KEYS
DEPRECATED_KEYS = constant_keys_from_config("DEPRECATED_ENV_KEYS")
DERIVED_ENV_KEYS = constant_keys_from_config("DERIVED_ENV_KEYS")


def _key_set(path: Path) -> frozenset[str]:
    return frozenset(key for _line_no, key in parse_keys(path))


def _service_example_keys() -> frozenset[str]:
    return _key_set(SERVICE_EXAMPLE_PATH)


def allowed_keys_for_file(path: Path) -> frozenset[str]:
    return _service_example_keys()


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


def check_file(path: Path) -> list[str]:
    # File-level checks enforce the root env config boundary before Settings loads.
    issues: list[str] = []
    allowed_keys = allowed_keys_for_file(path)
    for line_no, key in parse_keys(path):
        normalized_key = key.upper()
        if key != normalized_key:
            issues.append(f"{path}:{line_no}: config key must be uppercase: {key}")
        elif normalized_key in DEPRECATED_KEYS:
            issues.append(f"{path}:{line_no}: deprecated or unsupported config key: {key}")
        elif normalized_key in DERIVED_ENV_KEYS:
            issues.append(f"{path}:{line_no}: derived config key must not be set in env: {key}")
        elif normalized_key in ROOT_ENV_KEYS and normalized_key not in allowed_keys:
            issues.append(f"{path}:{line_no}: root env key is missing from .env.example: {key}")
        elif normalized_key not in allowed_keys:
            issues.append(f"{path}:{line_no}: unknown config key: {key}")
    return issues


def check_example_alignment() -> list[str]:
    # .env.example is the committed truth source for local env file key sets.
    issues: list[str] = []
    service_keys = _service_example_keys()

    missing_service = sorted(ROOT_ENV_KEYS - service_keys)
    extra_service = sorted(service_keys - ROOT_ENV_KEYS)
    for key in missing_service:
        issues.append(f"{SERVICE_EXAMPLE_PATH}: missing root config key from .env.example: {key}")
    for key in extra_service:
        issues.append(f"{SERVICE_EXAMPLE_PATH}: key is not defined by root env manifest: {key}")

    return issues


def default_env_files() -> list[Path]:
    candidates: list[Path] = []
    candidates.extend(path for path in sorted(ROOT_DIR.glob(".env*")) if path.is_file())

    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def run_settings_validation(env_file: Path, app_env: str) -> int:
    env = os.environ.copy()
    for key in APPLICATION_ENV_KEYS:
        env.pop(key, None)
    env["ENV_FILE"] = str(env_file)
    env["APP_ENV"] = app_env
    env["APP_CONFIG_SKIP_DEFAULT_ENV_FILE"] = "true"

    code = """
import sys
from pydantic import ValidationError
try:
    from app.core.config import settings
    from app.business_packages.register import validate_business_package_config

    validate_business_package_config(settings)
except ValidationError as exc:
    parts = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error.get("loc", ()))
        msg = error.get("msg", str(error))
        parts.append(f"{loc}: {msg}" if loc else msg)
    messages = "; ".join(parts)
    print(f"ERROR: app config invalid: {messages}", file=sys.stderr)
    raise SystemExit(1)
except Exception as exc:
    print(f"ERROR: app config invalid: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(1)
release = "true" if settings.runtime.is_release_env else "false"
print(f"OK        app-config app_env={settings.runtime.app_env} release={release}")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout.rstrip(), flush=True)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr, flush=True)
    return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check env files only expose supported configuration keys.")
    parser.add_argument(
        "--env-file",
        help="Explicit application env file to check. Combine with --app-env to validate Settings startup rules.",
    )
    parser.add_argument(
        "--app-env",
        choices=APP_ENV_VALUES,
        help="APP_ENV override for this validation. Requires --env-file and does not auto-select a file.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Env files to check. Defaults to .env.example plus local/test env files when present.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.env_file and args.files:
        print("ERROR: --env-file cannot be combined with positional env files", file=sys.stderr)
        return 2
    if args.app_env and not args.env_file:
        print("ERROR: --app-env requires --env-file", file=sys.stderr)
        return 2

    issues: list[str] = check_example_alignment()
    checked = 0
    paths = [Path(args.env_file)] if args.env_file else [Path(name) for name in args.files] if args.files else default_env_files()
    for path in paths:
        if not path.is_absolute():
            path = ROOT_DIR / path
        if args.env_file and not path.exists():
            print(f"ERROR: ENV_FILE not found: {path}", file=sys.stderr)
            return 2
        if not path.exists():
            continue
        checked += 1
        issues.extend(check_file(path))

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print(f"{'OK':<9} {'env-files':<10} checked={checked}", flush=True)
    if args.app_env:
        env_file = Path(args.env_file)
        if not env_file.is_absolute():
            env_file = ROOT_DIR / env_file
        result = run_settings_validation(env_file, args.app_env)
        if result != 0:
            return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
