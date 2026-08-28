from __future__ import annotations

import os
from pathlib import Path

from smoke.harness.errors import FlowError


ROOT_DIR = Path(__file__).resolve().parents[2]


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def configured_env_file(env_file: str | None = None) -> str | None:
    if env_file is not None:
        return env_file
    return os.environ.get("ENV_FILE")


def resolve_env_file_path(env_file: str | None = None, *, root_dir: Path | None = None) -> Path:
    base_dir = root_dir or ROOT_DIR
    selected = configured_env_file(env_file)
    if selected is None:
        return base_dir / ".env"
    path = Path(selected).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


def load_app_env(env_file: str | None = None, *, root_dir: Path | None = None) -> dict[str, str]:
    path = resolve_env_file_path(env_file, root_dir=root_dir)
    if configured_env_file(env_file) is not None and not path.is_file():
        raise FlowError(f"env file not found: {path}", exit_code=2)
    return load_env_file(path)


def env_value(name: str, *files: dict[str, str]) -> str | None:
    if os.environ.get(name) is not None:
        return os.environ[name]
    for values in files:
        if values.get(name) is not None:
            return values[name]
    return None


def env_source(name: str, *files: dict[str, str]) -> str:
    if os.environ.get(name) is not None:
        return "runtime_env"
    for values in files:
        if values.get(name) is not None:
            return "env_file"
    return "missing"


def bool_enabled(value: str | None) -> bool:
    return value in {"true", "True", "TRUE"}
