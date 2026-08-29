from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from smoke.harness import env_runtime
from smoke.harness.errors import FlowError


def resolve_model_config_path(app_env: dict[str, str]) -> Path:
    raw = env_runtime.env_value("MODEL_CONFIG_PATH", app_env) or "app/ai/catalog/models.yaml"
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return env_runtime.ROOT_DIR / path


def default_model_id(app_env: dict[str, str], capability: str) -> str:
    path = resolve_model_config_path(app_env)
    if not path.is_file():
        raise FlowError(f"model config not found: {path}", exit_code=2)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FlowError(f"model config must be a YAML object: {path}", exit_code=2)
    default_model_ids: Any = data.get("default_model_ids")
    if not isinstance(default_model_ids, dict):
        raise FlowError(f"model config default_model_ids must be a YAML object: {path}", exit_code=2)
    value = default_model_ids.get(capability)
    if not isinstance(value, str) or not value.strip():
        raise FlowError(f"model config default_model_ids.{capability} is required: {path}", exit_code=2)
    return value.strip()
