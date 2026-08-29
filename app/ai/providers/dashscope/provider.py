from __future__ import annotations

from app.ai.providers.base import ProviderDefinition

PROVIDER = ProviderDefinition(
    name="dashscope",
    api_key_env="DASHSCOPE_API_KEY",
    base_url_env="DASHSCOPE_BASE_URL",
    default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    supports_live_models=True,
    supports_probe=True,
)
