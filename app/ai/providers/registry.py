from __future__ import annotations

from app.ai.providers.base import ProviderDefinition, ProviderRuntimeConfig
from app.ai.providers.dashscope.provider import PROVIDER as DASHSCOPE_PROVIDER
from app.ai.providers.openai.provider import PROVIDER as OPENAI_PROVIDER

_PROVIDERS: dict[str, ProviderDefinition] = {
    OPENAI_PROVIDER.name: OPENAI_PROVIDER,
    DASHSCOPE_PROVIDER.name: DASHSCOPE_PROVIDER,
}


def all_provider_definitions() -> dict[str, ProviderDefinition]:
    return dict(_PROVIDERS)


def get_provider_definition(provider: str) -> ProviderDefinition | None:
    return _PROVIDERS.get(provider)


def require_provider_definition(provider: str) -> ProviderDefinition:
    definition = get_provider_definition(provider)
    if definition is None:
        raise RuntimeError(f"ai provider not registered: {provider}")
    return definition


def provider_runtime_config(provider: str, *, settings_obj=None) -> ProviderRuntimeConfig:
    if settings_obj is None:
        from app.core.config import settings

        runtime_settings = settings
    else:
        runtime_settings = settings_obj
    definition = require_provider_definition(provider)
    api_key = _settings_env_value(runtime_settings, definition.api_key_env)
    base_url = ""
    if definition.base_url_env is not None:
        base_url = _settings_env_value(runtime_settings, definition.base_url_env)
    if not base_url:
        base_url = definition.default_base_url
    return ProviderRuntimeConfig(
        provider=definition.name,
        api_key=api_key,
        base_url=base_url,
        api_key_env=definition.api_key_env,
        base_url_env=definition.base_url_env,
    )


def _settings_env_value(runtime_settings, name: str) -> str:
    env_value = getattr(runtime_settings, "application_env_value", None)
    if env_value is not None:
        return env_value(name)
    ai_provider = runtime_settings.ai_provider
    mapping = {
        "OPENAI_API_KEY": "openai_api_key_value",
        "OPENAI_BASE_URL": "openai_base_url",
        "DASHSCOPE_API_KEY": "dashscope_api_key_value",
        "DASHSCOPE_BASE_URL": "dashscope_base_url",
    }
    attr = mapping.get(name)
    if attr is None:
        raise RuntimeError(f"unsupported provider env key: {name}")
    return getattr(ai_provider, attr)


def validate_provider(provider: str) -> None:
    require_provider_definition(provider)


def provider_snapshot() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for definition in sorted(_PROVIDERS.values(), key=lambda item: item.name):
        runtime = provider_runtime_config(definition.name)
        row = runtime.redacted_summary()
        row["supports_live_models"] = definition.supports_live_models
        row["supports_probe"] = definition.supports_probe
        rows.append(row)
    return rows
