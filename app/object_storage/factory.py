from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, Callable, Mapping

from .exceptions import ObjectStorageConfigError, ObjectStorageValidationError
from .providers.aliyun_oss import AliyunOSSConfig, AliyunOSSRepository
from .providers.local import LocalObjectStorageRepository, LocalStorageConfig
from .repository import ObjectStorageRepository


ProviderBuilder = Callable[[Mapping[str, Any]], ObjectStorageRepository]
_PROVIDER_BUILDERS: dict[str, ProviderBuilder] = {}
_ALIYUN_OSS_OPTIONS = frozenset(
    {
        "bucket",
        "region",
        "access_key_id",
        "access_key_secret",
        "key_prefix",
        "endpoint",
        "public_base_url",
        "scheme",
        "timeout_seconds",
    }
)
_LOCAL_OPTIONS = frozenset({"root", "bucket", "region", "public_base_url"})


@dataclass(frozen=True)
class ObjectStorageConfig:
    provider: str
    options: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.options, Mapping):
            raise ObjectStorageConfigError("options must be a mapping")
        object.__setattr__(self, "provider", _provider_name(self.provider))


def build_repository(config: ObjectStorageConfig) -> ObjectStorageRepository:
    if not isinstance(config, ObjectStorageConfig):
        raise ObjectStorageConfigError("config must be ObjectStorageConfig")
    builder = _PROVIDER_BUILDERS.get(config.provider)
    if builder is None:
        raise ObjectStorageConfigError(f"unsupported object storage provider: {config.provider}")
    try:
        repository = builder(config.options)
    except ObjectStorageValidationError as exc:
        raise ObjectStorageConfigError(str(exc)) from exc
    if not isinstance(repository, ObjectStorageRepository):
        raise ObjectStorageConfigError(f"provider builder returned an invalid repository: {config.provider}")
    return repository


def register_provider_builder(provider: str, builder: ProviderBuilder) -> None:
    name = _provider_name(provider)
    if not callable(builder):
        raise ObjectStorageConfigError("provider builder must be callable")
    if name in _PROVIDER_BUILDERS:
        raise ObjectStorageConfigError(f"object storage provider builder already registered: {name}")
    _PROVIDER_BUILDERS[name] = builder


def registered_provider_names() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDER_BUILDERS))


def _build_aliyun_oss(options: Mapping[str, Any]) -> AliyunOSSRepository:
    _reject_unknown_options("aliyun_oss", options, _ALIYUN_OSS_OPTIONS)
    required = ("bucket", "region", "access_key_id", "access_key_secret")
    missing = [name for name in required if not options.get(name)]
    if missing:
        raise ObjectStorageConfigError(f"aliyun_oss requires: {', '.join(missing)}")
    try:
        config = AliyunOSSConfig(
            bucket=_required_str(options, "bucket"),
            region=_required_str(options, "region"),
            access_key_id=_required_str(options, "access_key_id"),
            access_key_secret=_required_str(options, "access_key_secret"),
            key_prefix=_optional_str(options, "key_prefix"),
            endpoint=_optional_str(options, "endpoint"),
            public_base_url=_optional_str(options, "public_base_url"),
            scheme=_optional_str(options, "scheme", default="https"),
            timeout_seconds=_optional_float(options, "timeout_seconds", default=20),
        )
    except ObjectStorageValidationError as exc:
        raise ObjectStorageConfigError(str(exc)) from exc
    return AliyunOSSRepository(config)


def _build_local(options: Mapping[str, Any]) -> LocalObjectStorageRepository:
    _reject_unknown_options("local", options, _LOCAL_OPTIONS)
    root = options.get("root")
    if root is None:
        raise ObjectStorageConfigError("local requires: root")
    if not isinstance(root, str | PathLike):
        raise ObjectStorageConfigError("root must be a path")
    try:
        config = LocalStorageConfig(
            root=Path(root),
            bucket=_optional_str(options, "bucket", default="local"),
            region=_optional_str(options, "region", default="local"),
            public_base_url=_optional_str(options, "public_base_url"),
        )
    except ObjectStorageValidationError as exc:
        raise ObjectStorageConfigError(str(exc)) from exc
    return LocalObjectStorageRepository(config)


def _required_str(options: Mapping[str, Any], name: str) -> str:
    value = options.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ObjectStorageConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_str(options: Mapping[str, Any], name: str, *, default: str = "") -> str:
    value = options.get(name, default)
    if value is None:
        raise ObjectStorageConfigError(f"{name} must not be null")
    if not isinstance(value, str):
        raise ObjectStorageConfigError(f"{name} must be a string")
    return value.strip()


def _optional_float(options: Mapping[str, Any], name: str, *, default: float) -> float:
    value = options.get(name, default)
    if value is None:
        raise ObjectStorageConfigError(f"{name} must not be null")
    if isinstance(value, bool):
        raise ObjectStorageConfigError(f"{name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ObjectStorageConfigError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise ObjectStorageConfigError(f"{name} must be greater than 0")
    return parsed


def _provider_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ObjectStorageConfigError("provider must be a non-empty string")
    return value.strip()


def _reject_unknown_options(provider: str, options: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted((key for key in options if key not in allowed), key=str)
    if unknown:
        formatted = ", ".join(str(key) for key in unknown)
        raise ObjectStorageConfigError(f"{provider} contains unsupported options: {formatted}")


_BUILTIN_PROVIDER_BUILDERS: dict[str, ProviderBuilder] = {
    "aliyun_oss": _build_aliyun_oss,
    "local": _build_local,
}
BUILTIN_PROVIDERS = frozenset(_BUILTIN_PROVIDER_BUILDERS)

for _provider, _builder in _BUILTIN_PROVIDER_BUILDERS.items():
    register_provider_builder(_provider, _builder)
