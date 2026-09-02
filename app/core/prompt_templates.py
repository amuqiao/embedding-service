from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.core.exceptions import ValidationAppError
from app.schemas.meta import PromptBlockTemplate, PromptTemplateResponseData

PROMPT_CONFIG_SECTIONS = ("job_types", "prompts")
PROMPT_CONFIG_JOB_TEMPLATE_VERSIONS_KEY = "_job_template_versions"
PROMPT_CONFIG_FILE_TOP_LEVEL_KEYS = frozenset({"version", *PROMPT_CONFIG_SECTIONS})
DEFAULT_PROMPT_TEMPLATE_JOB_TYPE = "poster_title_image"
APP_DIR = Path(__file__).resolve().parents[1]
JOB_PROMPT_CONFIG_ROOT = APP_DIR / "business_packages"


def _read_prompt_config_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"prompt config not found: {path}") from exc
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"prompt config must be a YAML object: {path}")
    unknown_keys = sorted(set(data) - PROMPT_CONFIG_FILE_TOP_LEVEL_KEYS)
    if unknown_keys:
        raise RuntimeError(f"prompt config contains unknown top-level keys: {unknown_keys}")
    return data


def _merge_prompt_config(base: dict[str, Any], overlay: dict[str, Any], *, source: Path) -> None:
    version = _prompt_version(overlay)
    for section in PROMPT_CONFIG_SECTIONS:
        section_config = overlay.get(section)
        if section_config is None:
            continue
        if not isinstance(section_config, dict):
            raise RuntimeError(f"prompt config {section} must be a YAML object: {source}")
        target = base.setdefault(section, {})
        if not isinstance(target, dict):
            raise RuntimeError(f"prompt config {section} must be a YAML object")
        for key, value in section_config.items():
            if key in target:
                raise RuntimeError(f"duplicate prompt config key in {section}: {key}")
            target[key] = value
            if section == "job_types":
                versions = base.setdefault(PROMPT_CONFIG_JOB_TEMPLATE_VERSIONS_KEY, {})
                if not isinstance(versions, dict):
                    raise RuntimeError("prompt config job template versions must be a YAML object")
                versions[key] = version


def _load_prompt_config(*, job_types: set[str] | None = None) -> dict[str, Any]:
    config = _read_prompt_config_file(settings.registry.prompt_config_path)
    for path in sorted(JOB_PROMPT_CONFIG_ROOT.glob("*/prompts.yaml")):
        if job_types is not None and path.parent.name not in job_types:
            continue
        _merge_prompt_config(config, _read_prompt_config_file(path), source=path)
    return config


def _block_content(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if not isinstance(content, str):
        raise RuntimeError("prompt block content must be a string")
    return content.strip()


def _required_prompt_section(config: dict[str, Any], section: str) -> dict[str, Any]:
    if section == PROMPT_CONFIG_JOB_TEMPLATE_VERSIONS_KEY:
        section_config = config.get(section, {})
        if not isinstance(section_config, dict):
            raise RuntimeError(f"prompt config {section} must be a YAML object")
        return section_config
    if section == "prompts" and section not in config:
        return {}
    section_config = config.get(section)
    if not isinstance(section_config, dict):
        raise RuntimeError(f"prompt config {section} must be a YAML object")
    return section_config


def _prompt_entry_configs(
    config: dict[str, Any],
    *,
    prompt_refs: set[str] | None = None,
    job_types: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for section in PROMPT_CONFIG_SECTIONS:
        for prompt_ref, prompt_config in _required_prompt_section(config, section).items():
            if not isinstance(prompt_ref, str) or not prompt_ref.strip():
                raise RuntimeError(f"prompt config {section} key must be a non-empty string")
            normalized_ref = prompt_ref.strip()
            if section == "prompts" and prompt_refs is not None and normalized_ref not in prompt_refs:
                continue
            if section == "job_types" and job_types is not None and normalized_ref not in job_types:
                continue
            if not isinstance(prompt_config, dict):
                raise RuntimeError(f"prompt {prompt_ref} must be a YAML object")
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


def _prompt_output_schema_refs(
    config: dict[str, Any],
    *,
    prompt_refs: set[str] | None = None,
    job_types: set[str] | None = None,
) -> dict[str, str]:
    refs: dict[str, str] = {}
    for prompt_ref, prompt_config in _prompt_entry_configs(
        config,
        prompt_refs=prompt_refs,
        job_types=job_types,
    ).items():
        output_schema_ref = prompt_config.get("output_schema_ref")
        if output_schema_ref is None:
            continue
        if not isinstance(output_schema_ref, str) or not output_schema_ref.strip():
            raise RuntimeError(f"prompt {prompt_ref} output_schema_ref must be a non-empty string")
        refs[prompt_ref] = output_schema_ref.strip()
    return refs


def _template_blocks(job_config: dict[str, Any]) -> list[PromptBlockTemplate]:
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


def _job_template(job_type: str, job_config: dict[str, Any], *, version: str) -> PromptTemplateResponseData:
    if not isinstance(job_type, str) or not isinstance(job_config, dict):
        raise RuntimeError("invalid prompt config job_type item")
    name = job_config.get("name")
    description = job_config.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        raise RuntimeError(f"job_type {job_type} requires name and description")
    return PromptTemplateResponseData(
        version=version,
        job_type=job_type,
        name=name,
        description=description,
        prompt_blocks=_template_blocks(job_config),
    )


def _job_template_for_type(job_type: str) -> PromptTemplateResponseData:
    normalized_job_type = job_type.strip()
    if not normalized_job_type:
        raise ValidationAppError("INVALID_JOB_TYPE", "job_type must be a non-empty string")
    config = _load_prompt_config(job_types={normalized_job_type})
    job_configs = _required_prompt_section(config, "job_types")
    job_config = job_configs.get(normalized_job_type)
    if not isinstance(job_config, dict):
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 job_type: {normalized_job_type}")
    version = _job_template_version(config, normalized_job_type)
    return _job_template(normalized_job_type, job_config, version=version)


def _prompt_version(config: dict[str, Any]) -> str:
    version = config.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("prompt config version must be a non-empty string")
    return version.strip()


def _job_template_version(config: dict[str, Any], job_type: str) -> str:
    versions = config.get(PROMPT_CONFIG_JOB_TEMPLATE_VERSIONS_KEY)
    if isinstance(versions, dict):
        version = versions.get(job_type)
        if isinstance(version, str) and version.strip():
            return version
    return _prompt_version(config)


def prompt_version() -> str:
    return _prompt_version(_load_prompt_config())


def _filtered_prompt_refs(
    config: dict[str, Any],
    *,
    prompt_refs: set[str] | None = None,
    job_types: set[str] | None = None,
) -> set[str]:
    return set(_prompt_entry_configs(config, prompt_refs=prompt_refs, job_types=job_types))


def all_prompt_refs(
    *,
    prompt_refs: set[str] | None = None,
    job_types: set[str] | None = None,
) -> set[str]:
    config = _load_prompt_config(job_types=job_types)
    return _filtered_prompt_refs(config, prompt_refs=prompt_refs, job_types=job_types)


def prompt_output_schema_refs(
    *,
    prompt_refs: set[str] | None = None,
    job_types: set[str] | None = None,
) -> dict[str, str]:
    config = _load_prompt_config(job_types=job_types)
    return _prompt_output_schema_refs(config, prompt_refs=prompt_refs, job_types=job_types)


def prompt_template_job_types(*, job_types: set[str] | None = None) -> set[str]:
    configured = set(_required_prompt_section(_load_prompt_config(job_types=job_types), "job_types"))
    if job_types is None:
        return configured
    return configured & job_types


def validate_prompt_config_shape(
    *,
    known_output_schemas: set[str] | None = None,
    prompt_refs: set[str] | None = None,
    job_types: set[str] | None = None,
) -> None:
    config = _load_prompt_config(job_types=job_types)
    _prompt_version(config)
    _prompt_entry_configs(config, prompt_refs=prompt_refs, job_types=job_types)
    for section in PROMPT_CONFIG_SECTIONS:
        for prompt_ref, prompt_config in _required_prompt_section(config, section).items():
            if section == "job_types" and job_types is not None and prompt_ref not in job_types:
                continue
            if section == "prompts" and prompt_refs is not None and prompt_ref not in prompt_refs:
                continue
            if not isinstance(prompt_config, dict):
                raise RuntimeError(f"prompt {prompt_ref} must be a YAML object")
            if section == "job_types":
                _template_blocks(prompt_config)
            else:
                _validate_prompt_blocks(prompt_ref, prompt_config)
            name = prompt_config.get("name")
            description = prompt_config.get("description")
            if not isinstance(name, str) or not name.strip():
                raise RuntimeError(f"prompt {prompt_ref} requires name")
            if not isinstance(description, str) or not description.strip():
                raise RuntimeError(f"prompt {prompt_ref} requires description")
    schema_refs = prompt_output_schema_refs(prompt_refs=prompt_refs, job_types=job_types)
    if known_output_schemas is not None:
        missing_schemas = sorted(set(schema_refs.values()) - known_output_schemas)
        if missing_schemas:
            raise RuntimeError(f"prompt config references unknown output schemas: {missing_schemas}")


def get_system_prompt(job_type: str) -> str:
    config = _load_prompt_config(job_types={job_type})
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
    config = _load_prompt_config(job_types={job_type})
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


def list_prompt_templates(job_type: str = DEFAULT_PROMPT_TEMPLATE_JOB_TYPE) -> PromptTemplateResponseData:
    return _job_template_for_type(job_type)


def get_template(job_type: str) -> PromptTemplateResponseData | None:
    try:
        return _job_template_for_type(job_type)
    except ValidationAppError:
        return None


def get_prompt_block_default(job_type: str, block_key: str) -> str:
    try:
        template = _job_template_for_type(job_type)
    except ValidationAppError as exc:
        raise RuntimeError(f"job_type {job_type} does not define prompt template") from exc
    for block in template.prompt_blocks:
        if block.key == block_key:
            return block.default_content
    raise RuntimeError(f"job_type {job_type} does not define prompt block: {block_key}")
