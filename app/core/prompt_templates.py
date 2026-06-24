from typing import Any

import yaml

from app.core.config import settings
from app.schemas.meta import JobTypeTemplate, PromptBlockTemplate, PromptTemplatesResponse

PROMPT_CONFIG_SECTIONS = ("job_types", "prompts")


def _load_prompt_config() -> dict[str, Any]:
    try:
        raw = settings.registry.prompt_config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"prompt config not found: {settings.registry.prompt_config_path}") from exc
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise RuntimeError("prompt config must be a YAML object")
    return data


def _block_content(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if not isinstance(content, str):
        raise RuntimeError("prompt block content must be a string")
    return content.strip()


def _required_prompt_section(config: dict[str, Any], section: str) -> dict[str, Any]:
    if section == "prompts" and section not in config:
        return {}
    section_config = config.get(section)
    if not isinstance(section_config, dict):
        raise RuntimeError(f"prompt config {section} must be a YAML object")
    return section_config


def _prompt_entry_configs(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for section in PROMPT_CONFIG_SECTIONS:
        for prompt_ref, prompt_config in _required_prompt_section(config, section).items():
            if not isinstance(prompt_ref, str) or not prompt_ref.strip():
                raise RuntimeError(f"prompt config {section} key must be a non-empty string")
            if not isinstance(prompt_config, dict):
                raise RuntimeError(f"prompt {prompt_ref} must be a YAML object")
            normalized_ref = prompt_ref.strip()
            if normalized_ref in entries:
                raise RuntimeError(f"duplicate prompt ref: {normalized_ref}")
            entries[normalized_ref] = prompt_config
    return entries


def _validate_prompt_blocks(prompt_ref: str, prompt_config: dict[str, Any]) -> None:
    block_configs = prompt_config.get("prompt_blocks")
    if not isinstance(block_configs, dict) or not block_configs:
        raise RuntimeError(f"prompt {prompt_ref} prompt_blocks must be a non-empty YAML object")
    for block_key, block in block_configs.items():
        if not isinstance(block_key, str) or not block_key.strip():
            raise RuntimeError(f"prompt {prompt_ref} block key must be a non-empty string")
        if not isinstance(block, dict):
            raise RuntimeError(f"prompt {prompt_ref} block {block_key} must be a YAML object")
        role = block.get("role")
        label = block.get("label")
        if not isinstance(role, str) or not role.strip():
            raise RuntimeError(f"prompt {prompt_ref} block {block_key} requires role")
        if not isinstance(label, str) or not label.strip():
            raise RuntimeError(f"prompt {prompt_ref} block {block_key} requires label")
        _block_content(block)


def _prompt_output_schema_refs(config: dict[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for prompt_ref, prompt_config in _prompt_entry_configs(config).items():
        output_schema_ref = prompt_config.get("output_schema_ref")
        if output_schema_ref is None:
            continue
        if not isinstance(output_schema_ref, str) or not output_schema_ref.strip():
            raise RuntimeError(f"prompt {prompt_ref} output_schema_ref must be a non-empty string")
        refs[prompt_ref] = output_schema_ref.strip()
    return refs


def _template_blocks(config: dict[str, Any], job_config: dict[str, Any]) -> list[PromptBlockTemplate]:
    block_configs = job_config.get("prompt_blocks")
    if not isinstance(block_configs, dict) or not block_configs:
        raise RuntimeError("job prompt_blocks must be a non-empty YAML object")

    blocks: list[PromptBlockTemplate] = []
    for key, block in block_configs.items():
        if not isinstance(key, str) or not key.strip():
            raise RuntimeError("prompt block key must be a non-empty string")
        if not isinstance(block, dict):
            raise RuntimeError(f"prompt block {key} must be a YAML object")
        role = block.get("role")
        label = block.get("label")
        if not isinstance(role, str) or not role.strip() or not isinstance(label, str) or not label.strip():
            raise RuntimeError(f"prompt block {key} requires role and label")
        blocks.append(
            PromptBlockTemplate(
                key=key.strip(),
                role=role,
                label=label,
                default_content=_block_content(block),
            )
        )
    return blocks


def _job_templates() -> list[JobTypeTemplate]:
    config = _load_prompt_config()
    job_configs = _required_prompt_section(config, "job_types")

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


def all_prompt_refs() -> set[str]:
    return set(_prompt_entry_configs(_load_prompt_config()))


def prompt_output_schema_refs() -> dict[str, str]:
    return _prompt_output_schema_refs(_load_prompt_config())


def prompt_template_job_types() -> set[str]:
    return set(_required_prompt_section(_load_prompt_config(), "job_types"))


def validate_prompt_config_shape(*, known_output_schemas: set[str] | None = None) -> None:
    config = _load_prompt_config()
    version = config.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("prompt config version must be a non-empty string")
    _prompt_entry_configs(config)
    for section in PROMPT_CONFIG_SECTIONS:
        for prompt_ref, prompt_config in _required_prompt_section(config, section).items():
            if section == "job_types":
                _template_blocks(config, prompt_config)
            else:
                _validate_prompt_blocks(prompt_ref, prompt_config)
            name = prompt_config.get("name")
            description = prompt_config.get("description")
            if not isinstance(name, str) or not name.strip():
                raise RuntimeError(f"prompt {prompt_ref} requires name")
            if not isinstance(description, str) or not description.strip():
                raise RuntimeError(f"prompt {prompt_ref} requires description")
    schema_refs = _prompt_output_schema_refs(config)
    if known_output_schemas is not None:
        missing_schemas = sorted(set(schema_refs.values()) - known_output_schemas)
        if missing_schemas:
            raise RuntimeError(f"prompt config references unknown output schemas: {missing_schemas}")


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
