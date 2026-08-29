from __future__ import annotations

from app.ai.providers.base import ProviderDefinition

PROVIDER = ProviderDefinition(
    name="openai",
    api_key_env="OPENAI_API_KEY",
    base_url_env="OPENAI_BASE_URL",
    default_base_url="",
    supports_live_models=True,
    supports_probe=True,
)
