from typing import Any

import yaml

from app.infrastructure.config import settings
from app.schemas.meta import JobTypeTemplate, PromptBlockTemplate, PromptTemplatesResponse

PROMPT_BLOCK_ORDER = ("user", "work_note")


def _load_prompt_config() -> dict[str, Any]:
    try:
        raw = settings.prompt_config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"prompt config not found: {settings.prompt_config_path}") from exc
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise RuntimeError("prompt config must be a YAML object")
    return data


def _block_content(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if not isinstance(content, str):
        raise RuntimeError("prompt block content must be a string")
    return content.strip()


def _template_blocks(config: dict[str, Any], job_config: dict[str, Any]) -> list[PromptBlockTemplate]:
    block_configs = job_config.get("prompt_blocks")
    if not isinstance(block_configs, dict):
        raise RuntimeError("job prompt_blocks must be a YAML object")

    blocks: list[PromptBlockTemplate] = []
    for key in PROMPT_BLOCK_ORDER:
        block = block_configs.get(key)
        if not isinstance(block, dict):
            raise RuntimeError(f"missing prompt block config: {key}")
        role = block.get("role")
        label = block.get("label")
        if not isinstance(role, str) or not isinstance(label, str):
            raise RuntimeError(f"prompt block {key} requires role and label")
        blocks.append(
            PromptBlockTemplate(
                key=key,
                role=role,
                label=label,
                default_content=_block_content(block),
            )
        )
    return blocks


def _job_templates() -> list[JobTypeTemplate]:
    config = _load_prompt_config()
    job_configs = config.get("job_types")
    if not isinstance(job_configs, dict):
        raise RuntimeError("prompt config job_types must be a YAML object")

    templates: list[JobTypeTemplate] = []
    for job_type, job_config in job_configs.items():
        if not isinstance(job_type, str) or not isinstance(job_config, dict):
            raise RuntimeError("invalid prompt config job_type item")
        name = job_config.get("name")
        description = job_config.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            raise RuntimeError(f"job_type {job_type} requires name and description")
        templates.append(
            JobTypeTemplate(
                job_type=job_type,
                name=name,
                description=description,
                prompt_blocks=_template_blocks(config, job_config),
            )
        )
    return templates


def prompt_version() -> str:
    version = _load_prompt_config().get("version")
    if not isinstance(version, str):
        raise RuntimeError("prompt config version must be a string")
    return version


def get_system_prompt(job_type: str) -> str:
    config = _load_prompt_config()
    job_configs = config.get("job_types")
    if not isinstance(job_configs, dict):
        return ""
    job_config = job_configs.get(job_type)
    if not isinstance(job_config, dict):
        return ""
    block_configs = job_config.get("prompt_blocks") or {}
    system_block = block_configs.get("system")
    if not isinstance(system_block, dict):
        return ""
    content = system_block.get("content", "")
    return content.strip() if isinstance(content, str) else ""


def get_output_contract(job_type: str) -> str:
    config = _load_prompt_config()
    job_configs = config.get("job_types")
    if not isinstance(job_configs, dict):
        raise RuntimeError("prompt config job_types must be a YAML object")
    job_config = job_configs.get(job_type)
    if not isinstance(job_config, dict):
        return ""
    output_contract = job_config.get("output_contract", "")
    if not isinstance(output_contract, str):
        raise RuntimeError(f"job_type {job_type} output_contract must be a string")
    return output_contract.strip()


def list_prompt_templates() -> PromptTemplatesResponse:
    return PromptTemplatesResponse(version=prompt_version(), job_types=_job_templates())


def get_template(job_type: str) -> JobTypeTemplate | None:
    return next((item for item in _job_templates() if item.job_type == job_type), None)
