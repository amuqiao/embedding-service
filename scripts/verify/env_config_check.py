"""Check service and script env files against their explicit key manifests.

This script is called under the shell "Env Config" section. Success is printed
as one OK event; issues go to stderr with file, line, object, and reason.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "app" / "core" / "config.py"
SERVICE_EXAMPLE_PATH = ROOT_DIR / ".env.example"
SCRIPT_EXAMPLE_PATH = ROOT_DIR / "scripts" / ".env.example"

KEY_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


@dataclass(frozen=True)
class EnvKeyManifest:
    script_keys: frozenset[str]
    deprecated_keys: frozenset[str]
    derived_keys: frozenset[str]


ENV_KEY_MANIFEST = EnvKeyManifest(
    script_keys=frozenset(
        {
            "API_HOST",
            "API_PORT",
            "API_HOST_PORT",
            "COMPOSE_PROJECT_NAME",
            "POSTGRES_DB",
            "POSTGRES_HOST_PORT",
            "REDIS_HOST_PORT",
            "WORKER_CONCURRENCY",
            "WORKER_LOGLEVEL",
            "WORKER_RECOVERY_LOOP",
        }
    ),
    deprecated_keys=frozenset(
        {
            "CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS",
            "DB_POOL_RECYCLE",
            "ENABLE_MOCK_INTERFACES",
            "JOB_MAX_EXECUTION_ATTEMPTS",
            "JOB_RECOVERY_BATCH_SIZE",
            "JOB_RECOVERY_CALLBACK_BATCH_SIZE",
            "JOB_RECOVERY_INTERVAL_SECONDS",
            "JOB_STALE_RUNNING_BUFFER_SECONDS",
            "MODEL_CALL_MAX_RETRIES",
            "NOVEL_LOCALIZATION_CHUNKING_ENABLED",
            "NOVEL_LOCALIZATION_CHUNK_SIZE",
            "NOVEL_LOCALIZATION_SINGLE_MAX_CHARS",
            "SHORT_DRAMA_RS_API_KEY",
            "SHORT_DRAMA_RS_BASE_URL",
            "SHORT_DRAMA_RS_RESULT_MOCK_ENABLED",
            "SHORT_DRAMA_RS_RESULT_RESPONSE_FIXTURE_PATH",
            "SHORT_DRAMA_RS_RESULT_SINK",
            "SHORT_DRAMA_RS_SCHEMA_FIXTURE_PATH",
            "SHORT_DRAMA_RS_SCHEMA_MOCK_ENABLED",
            "SHORT_DRAMA_RS_SCHEMA_SOURCE",
            "SHORT_DRAMA_RS_TAG_SCHEMA_VERSION",
            "SHORT_DRAMA_RS_TIMEOUT_SECONDS",
            "TASKIQ_MAX_RETRIES",
            "TASKIQ_RETRY_DELAY",
        }
    ),
    derived_keys=frozenset(
        {
            "CALLBACK_DELIVERY_TIMEOUT_SECONDS",
            "JOB_STALE_RUNNING_SECONDS",
            "OSS_ENDPOINT_STYLE",
            "OSS_SCHEME",
            "SYNC_DATABASE_URL",
            "WORKER_HARD_TIME_LIMIT",
            "WORKER_SOFT_TIME_LIMIT",
        }
    ),
)

def settings_keys_from_config() -> frozenset[str]:
    module = ast.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != "APPLICATION_ENV_FIELD_MAP":
            continue
        if node.value is None:
            break
        mapping = ast.literal_eval(node.value)
        if not isinstance(mapping, dict) or not all(isinstance(key, str) for key in mapping):
            raise RuntimeError(f"APPLICATION_ENV_FIELD_MAP must be a string-keyed dict: {CONFIG_PATH}")
        return frozenset(mapping)
    raise RuntimeError(f"APPLICATION_ENV_FIELD_MAP not found: {CONFIG_PATH}")


APPLICATION_ENV_KEYS = settings_keys_from_config()
SCRIPT_ENV_KEYS = ENV_KEY_MANIFEST.script_keys
SCRIPT_OR_DEPLOYMENT_ENV_KEYS = SCRIPT_ENV_KEYS
DEPRECATED_KEYS = ENV_KEY_MANIFEST.deprecated_keys
DERIVED_ENV_KEYS = ENV_KEY_MANIFEST.derived_keys


def _relative_path(path: Path) -> Path:
    try:
        return path.resolve().relative_to(ROOT_DIR)
    except ValueError:
        return path


def _is_script_env_file(path: Path) -> bool:
    relative = _relative_path(path)
    return (
        len(relative.parts) >= 2
        and relative.parts[-2] == "scripts"
        and (relative.name == ".env" or relative.name.startswith(".env."))
    )


def _is_service_env_file(path: Path) -> bool:
    relative = _relative_path(path)
    if len(relative.parts) == 1 and (relative.name == ".env" or relative.name.startswith(".env.")):
        return True
    return relative == Path("env_test/.env")


def _key_set(path: Path) -> frozenset[str]:
    return frozenset(key for _line_no, key in parse_keys(path))


def _service_example_keys() -> frozenset[str]:
    return _key_set(SERVICE_EXAMPLE_PATH)


def _script_example_keys() -> frozenset[str]:
    return _key_set(SCRIPT_EXAMPLE_PATH)


def allowed_keys_for_file(path: Path) -> frozenset[str]:
    if _is_script_env_file(path):
        return _script_example_keys()
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


def check_file(path: Path, manifest: EnvKeyManifest = ENV_KEY_MANIFEST) -> list[str]:
    # File-level checks enforce the service/script config boundary before Settings loads.
    issues: list[str] = []
    allowed_keys = allowed_keys_for_file(path)
    for line_no, key in parse_keys(path):
        normalized_key = key.upper()
        if key != normalized_key:
            issues.append(f"{path}:{line_no}: config key must be uppercase: {key}")
        elif normalized_key in manifest.deprecated_keys:
            issues.append(f"{path}:{line_no}: deprecated or unsupported config key: {key}")
        elif normalized_key in manifest.derived_keys:
            issues.append(f"{path}:{line_no}: derived config key must not be set in env: {key}")
        elif normalized_key in manifest.script_keys and normalized_key not in allowed_keys:
            issues.append(f"{path}:{line_no}: script key must be set in scripts/.env, not application env: {key}")
        elif normalized_key in APPLICATION_ENV_KEYS and normalized_key not in allowed_keys:
            issues.append(f"{path}:{line_no}: application key must be set in application env, not scripts/.env: {key}")
        elif normalized_key not in allowed_keys:
            issues.append(f"{path}:{line_no}: unknown config key: {key}")
    return issues


def check_example_alignment() -> list[str]:
    # Example files are the committed truth sources for local env file key sets.
    issues: list[str] = []
    service_keys = _service_example_keys()
    script_keys = _script_example_keys()

    missing_service = sorted(APPLICATION_ENV_KEYS - service_keys)
    extra_service = sorted(service_keys - APPLICATION_ENV_KEYS)
    for key in missing_service:
        issues.append(f"{SERVICE_EXAMPLE_PATH}: missing service config key from .env.example: {key}")
    for key in extra_service:
        issues.append(f"{SERVICE_EXAMPLE_PATH}: key is not defined by APPLICATION_ENV_FIELD_MAP: {key}")

    missing_script = sorted(SCRIPT_ENV_KEYS - script_keys)
    extra_script = sorted(script_keys - SCRIPT_ENV_KEYS)
    for key in missing_script:
        issues.append(f"{SCRIPT_EXAMPLE_PATH}: missing script config key from scripts/.env.example: {key}")
    for key in extra_script:
        issues.append(f"{SCRIPT_EXAMPLE_PATH}: key is not defined by SCRIPT_ENV_KEYS: {key}")

    overlap = sorted(service_keys & script_keys)
    for key in overlap:
        issues.append(f"env examples define key in both service and script domains: {key}")

    return issues


def default_env_files() -> list[Path]:
    candidates: list[Path] = []
    candidates.extend(path for path in sorted(ROOT_DIR.glob(".env*")) if path.is_file())
    env_test = ROOT_DIR / "env_test" / ".env"
    if env_test.exists():
        candidates.append(env_test)
    scripts_dir = ROOT_DIR / "scripts"
    candidates.extend(path for path in sorted(scripts_dir.glob(".env*")) if path.is_file())

    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


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
    issues: list[str] = check_example_alignment()
    checked = 0
    paths = [Path(name) for name in args.files] if args.files else default_env_files()
    for path in paths:
        if not path.is_absolute():
            path = ROOT_DIR / path
        if not path.exists():
            continue
        checked += 1
        issues.extend(check_file(path))

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print(f"{'OK':<9} {'env-files':<10} checked={checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
