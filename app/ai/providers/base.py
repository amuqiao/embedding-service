from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    api_key_env: str
    base_url_env: str | None = None
    default_base_url: str = ""
    supports_live_models: bool = False
    supports_probe: bool = False


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    provider: str
    api_key: str
    base_url: str
    api_key_env: str
    base_url_env: str | None

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def base_url_configured(self) -> bool:
        return bool(self.base_url)

    def redacted_summary(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "api_key_env": self.api_key_env,
            "api_key_configured": self.api_key_configured,
            "base_url_env": self.base_url_env,
            "base_url_configured": self.base_url_configured,
            "base_url": self.base_url,
        }
