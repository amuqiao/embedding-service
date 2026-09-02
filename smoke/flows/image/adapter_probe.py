from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import yaml

from app.ai.adapters.base import ImageGenerationRequest, ImageInput, ImageGenerationResult
from smoke.harness import formatters
from smoke.flows.oss import image_upload as oss_image_upload
from smoke.harness import env_runtime
from smoke.harness.errors import FlowError
ROOT_DIR = env_runtime.ROOT_DIR
ADAPTERS = ("openai_images", "openai_responses")
DEFAULT_MODELS_CONFIG = "app/business_packages/poster_title_image/models.yaml"


def _resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _required_str(data: dict[str, Any], path: tuple[str, ...], *, source: Path) -> str:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            joined = ".".join(path)
            raise FlowError(f"{source} requires string field: {joined}", exit_code=2)
        current = current.get(key)
    if not isinstance(current, str) or not current.strip():
        joined = ".".join(path)
        raise FlowError(f"{source} requires string field: {joined}", exit_code=2)
    return current.strip()


def load_models_config(models_config: str | None) -> dict[str, Any]:
    path = _resolve_repo_path(models_config or DEFAULT_MODELS_CONFIG)
    if not path.is_file():
        raise FlowError(f"poster title image models config not found: {path}", exit_code=2)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FlowError(f"{path} must be a YAML object", exit_code=2)
    provider_model = _required_str(data, ("model_slots", "generation", "default_model_id"), source=path)
    response_model = _required_str(data, ("model_slots", "style_probe", "default_model_id"), source=path)
    configured_adapter = _generation_adapter_for_model_id(provider_model)
    if configured_adapter not in ADAPTERS:
        raise FlowError(f"{provider_model} image route adapter must be one of: {', '.join(ADAPTERS)}", exit_code=2)
    adapter_order = [configured_adapter, *(adapter for adapter in ADAPTERS if adapter != configured_adapter)]
    return {
        "path": str(path),
        "configured_image_adapter": configured_adapter,
        "provider_model_id": provider_model,
        "response_model_id": response_model,
        "adapter_order": adapter_order,
    }


def _generation_adapter_for_model_id(model_id: str) -> str:
    from app.ai.capabilities import IMAGE_EDIT
    from app.ai.resolver import resolve_model

    try:
        return resolve_model(
            capability=IMAGE_EDIT,
            requested_model_id=model_id,
        ).resolved_model.adapter
    except Exception as exc:
        raise FlowError(f"model route is not available in app/ai/catalog/models.yaml: {model_id}", exit_code=2) from exc


def _provider_model_for_model_id(model_id: str) -> str:
    from app.ai.capabilities import MULTIMODAL_TEXT_GENERATION
    from app.ai.resolver import resolve_model

    try:
        return resolve_model(
            capability=MULTIMODAL_TEXT_GENERATION,
            requested_model_id=model_id,
        ).resolved_model.provider_model
    except Exception as exc:
        raise FlowError(f"model route is not available in app/ai/catalog/models.yaml: {model_id}", exit_code=2) from exc


def _required_openai_api_key(app_env: dict[str, str]) -> str:
    value = env_runtime.env_value("OPENAI_API_KEY", app_env)
    if not value or value.startswith("<"):
        raise FlowError("OPENAI_API_KEY is required for adapter-image-probe", exit_code=2)
    return value


def _timeout_seconds(app_env: dict[str, str], explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    raw = env_runtime.env_value("MODEL_CALL_TIMEOUT_SECONDS", app_env)
    if raw is None or not raw.strip():
        return 300
    try:
        value = int(raw)
    except ValueError as exc:
        raise FlowError("MODEL_CALL_TIMEOUT_SECONDS must be an integer", exit_code=2) from exc
    if value <= 0:
        raise FlowError("MODEL_CALL_TIMEOUT_SECONDS must be greater than 0", exit_code=2)
    return value


def _reference_images(reference_image: str | None, content_type: str | None) -> list[ImageInput]:
    if reference_image is None:
        return []
    path = _resolve_repo_path(reference_image)
    if not path.is_file():
        raise FlowError(f"reference image not found: {path}", exit_code=2)
    resolved_content_type = oss_image_upload.image_content_type(path, content_type)
    return [ImageInput(data=path.read_bytes(), content_type=resolved_content_type, detail="high")]


def _image_summaries(images: list[bytes]) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "size_bytes": len(image),
            "sha256": hashlib.sha256(image).hexdigest(),
        }
        for index, image in enumerate(images, start=1)
    ]


def _result_payload(adapter_name: str, result: ImageGenerationResult) -> dict[str, Any]:
    return {
        "adapter": adapter_name,
        "status": "succeeded",
        "image_count": len(result.images),
        "revised_prompt": result.revised_prompt,
        "usage": result.usage,
        "images": _image_summaries(result.images),
        "result": {
            "revised_prompt": result.revised_prompt,
            "usage": result.usage,
        },
    }


def _error_payload(adapter_name: str, exc: Exception) -> dict[str, Any]:
    return {
        "adapter": adapter_name,
        "status": "failed",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
        },
    }


async def _run_adapter(adapter_name: str, request: ImageGenerationRequest) -> dict[str, Any]:
    from app.ai.adapters.registry import require_image_generation_adapter

    adapter = require_image_generation_adapter(adapter_name)
    result = await adapter.generate_image(request)
    return _result_payload(adapter_name, result)


async def _run_all(request: ImageGenerationRequest, adapter_names: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for adapter_name in adapter_names:
        try:
            results.append(await _run_adapter(adapter_name, request))
        except Exception as exc:
            results.append(_error_payload(adapter_name, exc))
    return results


def run(
    *,
    confirm_cost: bool,
    env_file: str | None,
    models_config: str | None,
    prompt: str,
    reference_image: str | None,
    reference_content_type: str | None,
    provider_model: str | None,
    response_model: str | None,
    size: str,
    quality: str,
    background: str,
    output_format: str,
    timeout_seconds: int | None,
    json_output: bool,
) -> None:
    if not confirm_cost:
        raise FlowError("adapter image probe requires --confirm-cost", exit_code=2)

    probe_config = load_models_config(models_config)
    app_env = env_runtime.load_app_env(env_file)
    api_key = _required_openai_api_key(app_env)
    api_base = env_runtime.env_value("OPENAI_BASE_URL", app_env) or None
    resolved_timeout_seconds = _timeout_seconds(app_env, timeout_seconds)
    images = _reference_images(reference_image, reference_content_type)
    configured_provider_model_id = str(probe_config["provider_model_id"])
    configured_response_model_id = str(probe_config["response_model_id"])
    resolved_provider_model = provider_model or _provider_model_for_model_id(configured_provider_model_id)
    resolved_response_model = response_model or _provider_model_for_model_id(configured_response_model_id)
    request = ImageGenerationRequest(
        adapter_model=f"openai/{resolved_provider_model}",
        provider_model=resolved_provider_model,
        response_model=resolved_response_model,
        prompt=prompt,
        reference_images=images,
        size=size,
        quality=quality,
        background=background,
        output_format=output_format,
        timeout_seconds=resolved_timeout_seconds,
        api_key=api_key,
        api_base=api_base,
    )
    adapter_order = list(probe_config["adapter_order"])
    results = asyncio.run(_run_all(request, adapter_order))
    payload = {
        "summary": {
            "adapters": adapter_order,
            "models_config": probe_config["path"],
            "configured_image_adapter": probe_config["configured_image_adapter"],
            "provider_model_id": configured_provider_model_id,
            "provider_model": resolved_provider_model,
            "response_model_id": configured_response_model_id,
            "response_model": resolved_response_model,
            "reference_image_count": len(images),
            "size": size,
            "quality": quality,
            "background": background,
            "output_format": output_format,
            "timeout_seconds": resolved_timeout_seconds,
            "openai_base_url_configured": api_base is not None,
        },
        "results": results,
    }
    if json_output:
        formatters.print_json(payload)
        if any(result.get("status") == "failed" for result in results):
            raise FlowError("one or more image adapters failed", exit_code=4)
        return

    formatters.section("Adapter Image Probe")
    formatters.print_table(
        results,
        [
            ("adapter", "adapter"),
            ("status", "status"),
            ("image_count", "images"),
            ("usage", "usage"),
            ("error", "error"),
            ("revised_prompt", "revised_prompt"),
        ],
    )
    formatters.section("Raw Result Summaries")
    formatters.print_json(payload)
    if any(result.get("status") == "failed" for result in results):
        raise FlowError("one or more image adapters failed", exit_code=4)
