import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.oss_endpoint import normalize_oss_endpoint

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_NAME = "fastapi-best-ai-architecture"
DEFAULT_SERVICE_TITLE = "FastAPI Best AI Architecture"

# Timeout chain safety margins. These are internal engineering margins, not env keys.
_WORKER_SOFT_TIMEOUT_BUFFER: int = 300
_WORKER_HARD_TIMEOUT_BUFFER: int = 60
_JOB_STALE_RUNNING_BUFFER: int = 600
_CALLBACK_DELIVERY_CLAIM_GRACE: int = 175
_TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_ITEMS: int = 100
_ASSET_IMAGE_TAGGING_SCHEMA_MAX_ITEMS: int = 100
_ASSET_VECTOR_SCHEMA_MAX_ITEMS: int = 500
_ASSET_VECTOR_SCHEMA_MAX_TOP_K: int = 100
_TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_TEXT_LENGTH: int = 10_000
_TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_TOTAL_TEXT_LENGTH: int = (
    _TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_ITEMS * _TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_TEXT_LENGTH
)
_RELEASE_APP_ENVS = frozenset({"test", "prd"})
_PLACEHOLDER_SECRET_VALUES = frozenset(
    {
        "<替换为随机 token>",
        "<替换为随机 32 字节 hex>",
        "dev-service-key",
    }
)

APPLICATION_ENV_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "APP_ENV": ("runtime", "app_env"),
    "TEMPLATE_NAME": ("service", "template_name"),
    "SERVICE_NAME": ("service", "name"),
    "SERVICE_TITLE": ("service", "title"),
    "SERVICE_API_PREFIX": ("service", "api_prefix"),
    "DATABASE_URL": ("database", "url"),
    "DB_SSL": ("database", "ssl"),
    "DB_POOL_SIZE": ("database", "pool_size"),
    "DB_MAX_OVERFLOW": ("database", "max_overflow"),
    "SERVICE_API_KEY": ("security", "service_api_key"),
    "DISABLE_HTTP_AUTH_HEADER": ("security", "disable_http_auth_header"),
    "DISABLE_CALLER_ID_HEADER": ("security", "disable_caller_id_header"),
    "REDIS_URL": ("broker", "redis_url"),
    "TASKIQ_BROKER_KIND": ("broker", "kind"),
    "ALLOWED_ORIGINS": ("security", "allowed_origins_raw"),
    "CALLBACK_SIGNING_SECRET": ("callback", "signing_secret"),
    "ALLOW_INSECURE_CALLBACKS": ("callback", "allow_insecure_callbacks"),
    "STORAGE_BACKEND": ("storage", "backend"),
    "LOCAL_OBJECT_STORAGE_PATH": ("storage", "local_object_storage_path_raw"),
    "OSS_BUCKET": ("storage", "oss_bucket"),
    "OSS_REGION": ("storage", "oss_region"),
    "OSS_ACCESS_KEY_ID": ("storage", "oss_access_key_id"),
    "OSS_ACCESS_KEY_SECRET": ("storage", "oss_access_key_secret"),
    "OSS_PROJECT_ROOT": ("storage", "oss_project_root"),
    "OSS_OUTPUT_PREFIX": ("storage", "oss_output_prefix"),
    "OSS_ENDPOINT": ("storage", "oss_endpoint_override"),
    "OSS_PUBLIC_ENDPOINT": ("storage", "oss_public_endpoint"),
    "OPENAI_API_KEY": ("ai_provider", "openai_api_key"),
    "OPENAI_BASE_URL": ("ai_provider", "openai_base_url"),
    "DASHSCOPE_API_KEY": ("ai_provider", "dashscope_api_key"),
    "DASHSCOPE_BASE_URL": ("ai_provider", "dashscope_base_url"),
    "MODEL_CONFIG_PATH": ("registry", "model_config_path_raw"),
    "ENABLED_BUSINESS_PACKAGES": ("registry", "enabled_business_packages_raw"),
    "MODEL_CALL_TIMEOUT_SECONDS": ("ai_provider", "model_call_timeout_seconds"),
    "BILLING_ENABLED": ("billing", "enabled"),
    "MODEL_CATALOG_EXPOSE_BILLING_CAPABILITY": ("billing", "model_catalog_expose_billing_capability"),
    "PRICING_CONFIG_PATH": ("billing", "pricing_config_path_raw"),
    "OPS_DASHBOARD_ENABLED": ("ops_dashboard", "enabled"),
    "OPS_DASHBOARD_REQUIRE_AUTH": ("ops_dashboard", "require_auth"),
    "OPS_DASHBOARD_REFRESH_SECONDS": ("ops_dashboard", "refresh_seconds"),
    "OPS_DASHBOARD_MAX_WINDOW_SECONDS": ("ops_dashboard", "max_window_seconds"),
    "OPS_DASHBOARD_QUERY_TIMEOUT_SECONDS": ("ops_dashboard", "query_timeout_seconds"),
    "MAX_ACTIVE_JOBS": ("job", "max_active_jobs"),
    "OSS_INPUT_MAX_BYTES": ("job", "oss_input_max_bytes"),
    "TAGGED_TEXT_TRANSLATION_MAX_ITEMS": ("job", "tagged_text_translation", "max_items"),
    "TAGGED_TEXT_TRANSLATION_MAX_TEXT_LENGTH": ("job", "tagged_text_translation", "max_text_length"),
    "TAGGED_TEXT_TRANSLATION_MAX_TOTAL_TEXT_LENGTH": ("job", "tagged_text_translation", "max_total_text_length"),
    "ASSET_IMAGE_TAGGING_MODEL_ADAPTER": ("job", "asset_image_tagging", "model_adapter"),
    "ASSET_IMAGE_TAGGING_MODEL_ID": ("job", "asset_image_tagging", "model_id"),
    "ASSET_IMAGE_TAGGING_MAX_ITEMS": ("job", "asset_image_tagging", "max_items"),
    "ASSET_VECTOR_DASHSCOPE_API_KEY": ("job", "asset_vector", "dashscope_api_key"),
    "ASSET_VECTOR_DASHSCOPE_BASE_URL": ("job", "asset_vector", "dashscope_base_url"),
    "ASSET_VECTOR_EMBEDDING_MODEL": ("job", "asset_vector", "embedding_model"),
    "ASSET_VECTOR_EMBEDDING_DIMENSION": ("job", "asset_vector", "embedding_dimension"),
    "ASSET_VECTOR_MAX_ITEMS": ("job", "asset_vector", "max_items"),
    "ASSET_VECTOR_DELETE_MAX_ITEMS": ("job", "asset_vector", "delete_max_items"),
    "ASSET_VECTOR_SEARCH_DEFAULT_TOP_K": ("job", "asset_vector", "search_default_top_k"),
    "ASSET_VECTOR_SEARCH_MAX_TOP_K": ("job", "asset_vector", "search_max_top_k"),
    "POSTER_TITLE_IMAGE_MAX_ITEMS": ("job", "poster_title_image", "max_items"),
    "POSTER_TITLE_IMAGE_MAX_DRAW_COUNT": ("job", "poster_title_image", "max_draw_count"),
    "AUDIO_STEM_SEPARATION_EXECUTION_PROVIDER": ("job", "audio_stem_separation", "execution_provider"),
    "HTDEMUCS_MODEL_DIR": ("job", "audio_stem_separation", "htdemucs_model_dir_raw"),
    "AUDIO_STEM_TRITON_URL": ("job", "audio_stem_triton", "url"),
    "AUDIO_STEM_TRITON_TOKEN": ("job", "audio_stem_triton", "token"),
    "AUDIO_STEM_TRITON_MODEL_VERSION": ("job", "audio_stem_triton", "model_version"),
    "AUDIO_STEM_TRITON_REQUEST_TIMEOUT_SECONDS": ("job", "audio_stem_triton", "request_timeout_seconds"),
    "CALLBACK_TIMEOUT_SECONDS": ("callback", "timeout_seconds"),
    "PROMPT_CONFIG_PATH": ("registry", "prompt_config_path_raw"),
    "LOG_LEVEL": ("observability", "log_level"),
}

APPLICATION_ENV_KEYS = frozenset(APPLICATION_ENV_FIELD_MAP)
POC_ENV_KEYS: frozenset[str] = frozenset(
    {
        "POC_DASHSCOPE_BASE_URL",
    }
)
_REMOVED_JOB_TYPE_OSS_ENV_KEYS = frozenset(
    {
        "POSTER_TITLE_IMAGE_ALLOWED_OSS_BUCKETS",
        "POSTER_TITLE_IMAGE_ALLOWED_OSS_REGIONS",
        "AUDIO_STEM_SEPARATION_ALLOWED_OSS_BUCKETS",
        "AUDIO_STEM_SEPARATION_ALLOWED_OSS_REGIONS",
    }
)
REMOVED_APPLICATION_ENV_KEYS = frozenset(
    {"DEFAULT_MODEL_ID", "ENABLED_JOB_TYPES", "OPS_DASHBOARD_MOCK_DATA_ENABLED"} | _REMOVED_JOB_TYPE_OSS_ENV_KEYS
)
LAUNCHER_ENV_KEYS: frozenset[str] = frozenset(
    {
        "API_HOST",
        "API_PORT",
        "API_HOST_PORT",
        "COMPOSE_PROJECT_NAME",
        "POSTGRES_DB",
        "POSTGRES_HOST_PORT",
        "REDIS_HOST_PORT",
        "WORKER_CONCURRENCY",
        "WORKER_LOGLEVEL",
    }
)
DERIVED_ENV_KEYS = frozenset(
    {
        "WORKER_SOFT_TIME_LIMIT",
        "WORKER_HARD_TIME_LIMIT",
        "JOB_STALE_RUNNING_SECONDS",
        "CALLBACK_DELIVERY_TIMEOUT_SECONDS",
        "SYNC_DATABASE_URL",
        "OSS_ENDPOINT_STYLE",
        "OSS_SCHEME",
    }
)
DEPRECATED_ENV_KEYS = frozenset(
    {
        "CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS",
        "DB_POOL_RECYCLE",
        "ENABLE_MOCK_INTERFACES",
        "POSTER_TITLE_IMAGE_ALLOWED_OSS_BUCKETS",
        "POSTER_TITLE_IMAGE_ALLOWED_OSS_REGIONS",
        "AUDIO_STEM_SEPARATION_ALLOWED_OSS_BUCKETS",
        "AUDIO_STEM_SEPARATION_ALLOWED_OSS_REGIONS",
        "JOB_MAX_EXECUTION_ATTEMPTS",
        "JOB_RECOVERY_BATCH_SIZE",
        "JOB_RECOVERY_CALLBACK_BATCH_SIZE",
        "JOB_RECOVERY_INTERVAL_SECONDS",
        "JOB_STALE_RUNNING_BUFFER_SECONDS",
        "MODEL_CALL_MAX_RETRIES",
        "SHORT_DRAMA_RS_API_KEY",
        "SHORT_DRAMA_RS_BASE_URL",
        "SHORT_DRAMA_RS_RESULT_MOCK_ENABLED",
        "SHORT_DRAMA_RS_RESULT_RESPONSE_FIXTURE_PATH",
        "SHORT_DRAMA_RS_RESULT_SINK",
        "SHORT_DRAMA_RS_SCHEMA_FIXTURE_PATH",
        "SHORT_DRAMA_RS_SCHEMA_MOCK_ENABLED",
        "SHORT_DRAMA_RS_SCHEMA_SOURCE",
        "SHORT_DRAMA_RS_TAG_SCHEMA_VERSION",
        "SHORT_DRAMA_RS_TIMEOUT_SECONDS",
        "TASKIQ_MAX_RETRIES",
        "TASKIQ_RETRY_DELAY",
        "WORKER_RECOVERY_LOOP",
    }
)

_log = logging.getLogger(__name__)


def _looks_like_local_service_url(value: str) -> bool:
    host = urlparse(value).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1"}


def _resolve_repo_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _assign_nested(data: dict[str, Any], path: tuple[str, ...], value: str) -> None:
    if len(path) < 2:
        raise ValueError("settings path must contain at least section and field")
    group = data
    for part in path[:-1]:
        next_group = group.setdefault(part, {})
        if not isinstance(next_group, dict):
            raise ValueError(f"settings path conflicts at {part}")
        group = next_group
    group[path[-1]] = value


def _read_dotenv_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = dotenv_values(path)
    return {key: value for key, value in values.items() if key and value is not None}


def _unknown_dotenv_keys(dotenv: dict[str, str]) -> list[str]:
    allowed_keys = APPLICATION_ENV_KEYS | LAUNCHER_ENV_KEYS | POC_ENV_KEYS
    return sorted(key for key in dotenv if key != key.upper() or key not in allowed_keys)


def _removed_process_env_keys() -> list[str]:
    return sorted(key for key in os.environ if key in REMOVED_APPLICATION_ENV_KEYS)


def _selected_env_file_path() -> Path | None:
    value = os.environ.get("ENV_FILE", "").strip()
    if not value:
        return None
    path = _resolve_repo_path(value)
    if not path.exists():
        raise ValueError(f"ENV_FILE not found: {path}")
    return path


def _load_application_dotenv(path: Path) -> dict[str, str]:
    dotenv = _read_dotenv_values(path)
    unknown = _unknown_dotenv_keys(dotenv)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unsupported keys in {path}: {joined}")
    return dotenv


def _looks_like_placeholder_secret(value: str) -> bool:
    normalized = value.strip()
    return not normalized or normalized in _PLACEHOLDER_SECRET_VALUES or (
        normalized.startswith("<") and normalized.endswith(">")
    )


def _comma_separated_non_empty_values(value: str, *, env_name: str) -> tuple[str, ...]:
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part for part in parts):
        raise ValueError(f"{env_name} must be a comma-separated list of non-empty values")
    return tuple(dict.fromkeys(parts))


def _flat_env_settings_source() -> dict[str, Any]:
    default_env_path = ROOT_DIR / ".env"
    raw: dict[str, str] = {}
    if os.environ.get("APP_CONFIG_SKIP_DEFAULT_ENV_FILE") != "true":
        raw.update(_load_application_dotenv(default_env_path))
    selected_env_path = _selected_env_file_path()
    if selected_env_path is not None and selected_env_path.resolve() != default_env_path.resolve():
        raw.update(_load_application_dotenv(selected_env_path))
    removed_process_keys = _removed_process_env_keys()
    if removed_process_keys:
        joined = ", ".join(removed_process_keys)
        raise ValueError(f"unsupported keys in process environment: {joined}")
    for key, value in os.environ.items():
        if key in APPLICATION_ENV_KEYS:
            raw[key] = value

    data: dict[str, Any] = {}
    for env_key, path in APPLICATION_ENV_FIELD_MAP.items():
        if env_key in raw:
            _assign_nested(data, path, raw[env_key])
    return data


class ConfigSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeSettings(ConfigSection):
    app_env: str = "local"

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, value: str) -> str:
        if value not in {"local", "dev", "test", "prd"}:
            raise ValueError("APP_ENV must be local, dev, test, or prd")
        return value

    @property
    def is_release_env(self) -> bool:
        return self.app_env in _RELEASE_APP_ENVS


class ServiceSettings(ConfigSection):
    template_name: str = DEFAULT_TEMPLATE_NAME
    name: str = DEFAULT_TEMPLATE_NAME
    title: str = DEFAULT_SERVICE_TITLE
    api_prefix: str = "/api/v1/ai-jobs"


class DatabaseSettings(ConfigSection):
    url: str
    ssl: bool = True
    pool_size: int = 5
    max_overflow: int = 10
    pool_recycle: int = 1800

    @model_validator(mode="after")
    def validate_database(self) -> "DatabaseSettings":
        if self.pool_size <= 0:
            raise ValueError("DB_POOL_SIZE must be greater than 0")
        if self.max_overflow < 0:
            raise ValueError("DB_MAX_OVERFLOW must be greater than or equal to 0")
        if self.pool_recycle <= 0:
            raise ValueError("database.pool_recycle must be greater than 0")
        return self

    @property
    def sync_url(self) -> str:
        if self.url.startswith("postgresql+asyncpg://"):
            return self.url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        return self.url


class BrokerSettings(ConfigSection):
    redis_url: str = "redis://127.0.0.1:26379/0"
    kind: str = "redis_stream"

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if value not in {"redis_stream", "redis_list"}:
            raise ValueError("TASKIQ_BROKER_KIND must be redis_stream or redis_list")
        return value


class SecuritySettings(ConfigSection):
    service_api_key: SecretStr = Field(repr=False)
    allowed_origins_raw: str = "http://localhost:3000"
    disable_http_auth_header: bool = False
    disable_caller_id_header: bool = False

    @field_validator("disable_http_auth_header", "disable_caller_id_header", mode="before")
    @classmethod
    def validate_disable_header_flag(cls, value: object) -> object:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
        raise ValueError("header disable flags must be boolean true or false")

    @property
    def api_key(self) -> str:
        return self.service_api_key.get_secret_value()

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins_raw.split(",") if item.strip()]


class StorageSettings(ConfigSection):
    backend: str = "local"
    local_object_storage_path_raw: str = "storage/objects"
    oss_bucket: str = ""
    oss_region: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    oss_project_root: str = ""
    oss_output_prefix: str = "ai-jobs"
    oss_endpoint_override: str = ""
    oss_public_endpoint: str = ""

    @field_validator("backend")
    @classmethod
    def validate_storage_backend(cls, value: str) -> str:
        if value not in {"local", "aliyun_oss"}:
            raise ValueError("STORAGE_BACKEND must be local or aliyun_oss")
        return value

    @model_validator(mode="after")
    def validate_storage(self) -> "StorageSettings":
        if self.backend == "aliyun_oss":
            required = {
                "OSS_BUCKET": self.oss_bucket,
                "OSS_REGION": self.oss_region,
                "OSS_ACCESS_KEY_ID": self.oss_access_key_id,
                "OSS_ACCESS_KEY_SECRET": self.oss_access_key_secret.get_secret_value(),
                "OSS_PROJECT_ROOT": self.oss_project_root,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"aliyun_oss storage requires: {', '.join(missing)}")
        return self

    @property
    def local_object_storage_path(self) -> Path:
        return _resolve_repo_path(self.local_object_storage_path_raw)

    @property
    def oss_access_key_secret_value(self) -> str:
        return self.oss_access_key_secret.get_secret_value()

    @property
    def oss_endpoint(self) -> str:
        if self.oss_endpoint_override:
            return normalize_oss_endpoint(self.oss_endpoint_override)
        return f"oss-{self.oss_region}.aliyuncs.com" if self.oss_region else ""

    @property
    def oss_endpoint_style(self) -> str:
        if (
            self.oss_endpoint_override
            and self.oss_public_endpoint
            and self.oss_endpoint == normalize_oss_endpoint(self.oss_public_endpoint)
        ):
            return "custom_domain"
        return "virtual_host"

    @property
    def oss_scheme(self) -> str:
        return "https"


class CallbackSettings(ConfigSection):
    signing_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    allow_insecure_callbacks: bool = False
    timeout_seconds: int = 5
    max_delivery_attempts: int = 12
    retry_delay_seconds: int = 300

    @model_validator(mode="after")
    def validate_callback(self) -> "CallbackSettings":
        positive_fields = {
            "CALLBACK_TIMEOUT_SECONDS": self.timeout_seconds,
            "callback.max_delivery_attempts": self.max_delivery_attempts,
            "callback.retry_delay_seconds": self.retry_delay_seconds,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than 0")
        if not self.signing_secret_value:
            raise ValueError("CALLBACK_SIGNING_SECRET must be configured for callback HMAC signatures")
        return self

    @property
    def signing_secret_value(self) -> str:
        return self.signing_secret.get_secret_value()

    @property
    def delivery_timeout_seconds(self) -> int:
        return self.timeout_seconds + _CALLBACK_DELIVERY_CLAIM_GRACE


class AIProviderSettings(ConfigSection):
    openai_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    openai_base_url: str = ""
    dashscope_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_call_timeout_seconds: int = 300

    @model_validator(mode="after")
    def validate_ai_provider(self) -> "AIProviderSettings":
        if self.model_call_timeout_seconds <= 0:
            raise ValueError("MODEL_CALL_TIMEOUT_SECONDS must be greater than 0")
        return self

    @property
    def openai_api_key_value(self) -> str:
        return self.openai_api_key.get_secret_value()

    @property
    def dashscope_api_key_value(self) -> str:
        return self.dashscope_api_key.get_secret_value()


class RegistrySettings(ConfigSection):
    model_config_path_raw: str = "app/ai/catalog/models.yaml"
    prompt_config_path_raw: str = "app/core/prompts.yaml"
    enabled_business_packages_raw: str = ""

    @model_validator(mode="after")
    def validate_registry(self) -> "RegistrySettings":
        if self.enabled_business_packages_raw.strip():
            _comma_separated_non_empty_values(
                self.enabled_business_packages_raw,
                env_name="ENABLED_BUSINESS_PACKAGES",
            )
        return self

    @property
    def model_config_path(self) -> Path:
        return _resolve_repo_path(self.model_config_path_raw)

    @property
    def prompt_config_path(self) -> Path:
        return _resolve_repo_path(self.prompt_config_path_raw)

    @property
    def enabled_business_packages(self) -> tuple[str, ...]:
        if not self.enabled_business_packages_raw.strip():
            return ()
        return _comma_separated_non_empty_values(
            self.enabled_business_packages_raw,
            env_name="ENABLED_BUSINESS_PACKAGES",
        )


class BillingSettings(ConfigSection):
    enabled: bool = True
    model_catalog_expose_billing_capability: bool = False
    pricing_config_path_raw: str = "app/ai/pricing/pricing.yaml"

    @field_validator("enabled", "model_catalog_expose_billing_capability", mode="before")
    @classmethod
    def validate_billing_flags(cls, value: object) -> object:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
        raise ValueError("billing flags must be boolean true or false")

    @property
    def pricing_config_path(self) -> Path:
        return _resolve_repo_path(self.pricing_config_path_raw)


class OpsDashboardSettings(ConfigSection):
    enabled: bool = False
    require_auth: bool = True
    refresh_seconds: int = 15
    max_window_seconds: int = 604_800
    query_timeout_seconds: int = 2

    @field_validator("enabled", "require_auth", mode="before")
    @classmethod
    def validate_ops_dashboard_flags(cls, value: object) -> object:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
        raise ValueError("ops dashboard flags must be boolean true or false")

    @model_validator(mode="after")
    def validate_ops_dashboard(self) -> "OpsDashboardSettings":
        if self.refresh_seconds < 5:
            raise ValueError("OPS_DASHBOARD_REFRESH_SECONDS must be greater than or equal to 5")
        if self.max_window_seconds < 600:
            raise ValueError("OPS_DASHBOARD_MAX_WINDOW_SECONDS must be greater than or equal to 600")
        if self.query_timeout_seconds <= 0:
            raise ValueError("OPS_DASHBOARD_QUERY_TIMEOUT_SECONDS must be greater than 0")
        return self


class TaggedTextTranslationJobSettings(ConfigSection):
    max_items: int = 100
    max_text_length: int = 200
    max_total_text_length: int = 20_000

    @model_validator(mode="after")
    def validate_tagged_text_translation(self) -> "TaggedTextTranslationJobSettings":
        positive_fields = {
            "TAGGED_TEXT_TRANSLATION_MAX_ITEMS": self.max_items,
            "TAGGED_TEXT_TRANSLATION_MAX_TEXT_LENGTH": self.max_text_length,
            "TAGGED_TEXT_TRANSLATION_MAX_TOTAL_TEXT_LENGTH": self.max_total_text_length,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than 0")
        if self.max_items > _TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_ITEMS:
            raise ValueError(
                "TAGGED_TEXT_TRANSLATION_MAX_ITEMS must be less than or equal to "
                f"{_TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_ITEMS}"
            )
        if self.max_text_length > _TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_TEXT_LENGTH:
            raise ValueError(
                "TAGGED_TEXT_TRANSLATION_MAX_TEXT_LENGTH must be less than or equal to "
                f"{_TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_TEXT_LENGTH}"
            )
        if self.max_total_text_length > _TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_TOTAL_TEXT_LENGTH:
            raise ValueError(
                "TAGGED_TEXT_TRANSLATION_MAX_TOTAL_TEXT_LENGTH must be less than or equal to "
                f"{_TAGGED_TEXT_TRANSLATION_SCHEMA_MAX_TOTAL_TEXT_LENGTH}"
            )
        if self.max_total_text_length < self.max_text_length:
            raise ValueError(
                "TAGGED_TEXT_TRANSLATION_MAX_TOTAL_TEXT_LENGTH must be greater than or equal to "
                "TAGGED_TEXT_TRANSLATION_MAX_TEXT_LENGTH"
            )
        return self


class PosterTitleImageJobSettings(ConfigSection):
    max_items: int = 50
    max_draw_count: int = 4

    @model_validator(mode="after")
    def validate_poster_title_image(self) -> "PosterTitleImageJobSettings":
        positive_fields = {
            "POSTER_TITLE_IMAGE_MAX_ITEMS": self.max_items,
            "POSTER_TITLE_IMAGE_MAX_DRAW_COUNT": self.max_draw_count,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than 0")
        if self.max_draw_count > 4:
            raise ValueError("POSTER_TITLE_IMAGE_MAX_DRAW_COUNT must be less than or equal to 4")
        return self


class AssetImageTaggingJobSettings(ConfigSection):
    model_adapter: str = "openai_responses"
    model_id: str = "gpt-5.5"
    max_items: int = 10

    @model_validator(mode="after")
    def validate_asset_image_tagging(self) -> "AssetImageTaggingJobSettings":
        if not self.model_adapter.strip():
            raise ValueError("ASSET_IMAGE_TAGGING_MODEL_ADAPTER must not be empty")
        if self.model_adapter != self.model_adapter.strip():
            raise ValueError("ASSET_IMAGE_TAGGING_MODEL_ADAPTER must not have leading or trailing whitespace")
        if self.model_adapter not in {"openai_responses"}:
            raise ValueError("ASSET_IMAGE_TAGGING_MODEL_ADAPTER must be openai_responses")
        if not self.model_id.strip():
            raise ValueError("ASSET_IMAGE_TAGGING_MODEL_ID must not be empty")
        if self.model_id != self.model_id.strip():
            raise ValueError("ASSET_IMAGE_TAGGING_MODEL_ID must not have leading or trailing whitespace")
        if self.max_items <= 0:
            raise ValueError("ASSET_IMAGE_TAGGING_MAX_ITEMS must be greater than 0")
        if self.max_items > _ASSET_IMAGE_TAGGING_SCHEMA_MAX_ITEMS:
            raise ValueError(
                "ASSET_IMAGE_TAGGING_MAX_ITEMS must be less than or equal to "
                f"{_ASSET_IMAGE_TAGGING_SCHEMA_MAX_ITEMS}"
            )
        return self


class AssetVectorJobSettings(ConfigSection):
    dashscope_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    dashscope_base_url: str = ""
    embedding_model: str = "tongyi-embedding-vision-flash"
    embedding_dimension: int = 768
    max_items: int = 10
    delete_max_items: int = 100
    search_default_top_k: int = 20
    search_max_top_k: int = 100

    @model_validator(mode="after")
    def validate_asset_vector(self) -> "AssetVectorJobSettings":
        if self.dashscope_base_url != self.dashscope_base_url.strip():
            raise ValueError("ASSET_VECTOR_DASHSCOPE_BASE_URL must not have leading or trailing whitespace")
        if self.dashscope_base_url and "://" not in self.dashscope_base_url:
            raise ValueError("ASSET_VECTOR_DASHSCOPE_BASE_URL must be an absolute URL")
        if self.dashscope_base_url:
            normalized_base_url = self.dashscope_base_url.rstrip("/")
            if "/services/embeddings/" in normalized_base_url:
                raise ValueError("ASSET_VECTOR_DASHSCOPE_BASE_URL must not include concrete embedding service path")
            if not (
                normalized_base_url.endswith("/api/v1")
                or normalized_base_url.endswith("/compatible-mode/v1")
            ):
                raise ValueError("ASSET_VECTOR_DASHSCOPE_BASE_URL must end with /api/v1 or /compatible-mode/v1")
        if not self.embedding_model.strip():
            raise ValueError("ASSET_VECTOR_EMBEDDING_MODEL must not be empty")
        if self.embedding_model != self.embedding_model.strip():
            raise ValueError("ASSET_VECTOR_EMBEDDING_MODEL must not have leading or trailing whitespace")
        positive_fields = {
            "ASSET_VECTOR_EMBEDDING_DIMENSION": self.embedding_dimension,
            "ASSET_VECTOR_MAX_ITEMS": self.max_items,
            "ASSET_VECTOR_DELETE_MAX_ITEMS": self.delete_max_items,
            "ASSET_VECTOR_SEARCH_DEFAULT_TOP_K": self.search_default_top_k,
            "ASSET_VECTOR_SEARCH_MAX_TOP_K": self.search_max_top_k,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than 0")
        if self.embedding_dimension != 768:
            raise ValueError("ASSET_VECTOR_EMBEDDING_DIMENSION must be 768")
        if self.max_items > _ASSET_VECTOR_SCHEMA_MAX_ITEMS:
            raise ValueError(f"ASSET_VECTOR_MAX_ITEMS must be <= {_ASSET_VECTOR_SCHEMA_MAX_ITEMS}")
        if self.delete_max_items > _ASSET_VECTOR_SCHEMA_MAX_ITEMS:
            raise ValueError(f"ASSET_VECTOR_DELETE_MAX_ITEMS must be <= {_ASSET_VECTOR_SCHEMA_MAX_ITEMS}")
        if self.search_default_top_k > self.search_max_top_k:
            raise ValueError("ASSET_VECTOR_SEARCH_DEFAULT_TOP_K must be <= ASSET_VECTOR_SEARCH_MAX_TOP_K")
        if self.search_max_top_k > _ASSET_VECTOR_SCHEMA_MAX_TOP_K:
            raise ValueError(f"ASSET_VECTOR_SEARCH_MAX_TOP_K must be <= {_ASSET_VECTOR_SCHEMA_MAX_TOP_K}")
        return self

    @property
    def dashscope_api_key_value(self) -> str:
        return self.dashscope_api_key.get_secret_value()


class AudioStemSeparationJobSettings(ConfigSection):
    execution_provider: str = "cpu"
    htdemucs_model_dir_raw: str = ".data/models/htdemucs-ft"

    @model_validator(mode="after")
    def validate_audio_stem_separation(self) -> "AudioStemSeparationJobSettings":
        if self.execution_provider not in {"auto", "cpu", "cuda"}:
            raise ValueError("AUDIO_STEM_SEPARATION_EXECUTION_PROVIDER must be auto, cpu, or cuda")
        if not self.htdemucs_model_dir_raw.strip():
            raise ValueError("HTDEMUCS_MODEL_DIR must not be empty")
        return self

    @property
    def htdemucs_model_dir(self) -> Path:
        return _resolve_repo_path(self.htdemucs_model_dir_raw)


class AudioStemTritonJobSettings(ConfigSection):
    url: str = ""
    token: SecretStr = Field(default=SecretStr(""), repr=False)
    model_version: str = "1"
    request_timeout_seconds: float = 300

    @model_validator(mode="after")
    def validate_audio_stem_triton(self) -> "AudioStemTritonJobSettings":
        if self.request_timeout_seconds <= 0:
            raise ValueError("AUDIO_STEM_TRITON_REQUEST_TIMEOUT_SECONDS must be greater than 0")
        if self.url.strip() and "://" in self.url:
            raise ValueError("AUDIO_STEM_TRITON_URL must not include http:// or https://")
        if self.url != self.url.strip():
            raise ValueError("AUDIO_STEM_TRITON_URL must not have leading or trailing whitespace")
        if not self.model_version.strip():
            raise ValueError("AUDIO_STEM_TRITON_MODEL_VERSION must not be empty")
        return self

    @property
    def token_value(self) -> str:
        return self.token.get_secret_value()


class JobSettings(ConfigSection):
    max_active_jobs: int = 5000
    oss_input_max_bytes: int = 5_242_880
    tagged_text_translation: TaggedTextTranslationJobSettings = Field(
        default_factory=TaggedTextTranslationJobSettings
    )
    asset_image_tagging: AssetImageTaggingJobSettings = Field(default_factory=AssetImageTaggingJobSettings)
    asset_vector: AssetVectorJobSettings = Field(default_factory=AssetVectorJobSettings)
    poster_title_image: PosterTitleImageJobSettings = Field(default_factory=PosterTitleImageJobSettings)
    audio_stem_separation: AudioStemSeparationJobSettings = Field(default_factory=AudioStemSeparationJobSettings)
    audio_stem_triton: AudioStemTritonJobSettings = Field(default_factory=AudioStemTritonJobSettings)
    orphan_timeout_seconds: int = 300
    dispatch_max_publish_attempts: int = 12
    recovery_interval_seconds: int = 60
    recovery_batch_size: int = 100
    recovery_callback_batch_size: int = 50

    @model_validator(mode="after")
    def validate_job(self) -> "JobSettings":
        positive_fields = {
            "OSS_INPUT_MAX_BYTES": self.oss_input_max_bytes,
            "job.orphan_timeout_seconds": self.orphan_timeout_seconds,
            "job.dispatch_max_publish_attempts": self.dispatch_max_publish_attempts,
            "job.recovery_interval_seconds": self.recovery_interval_seconds,
            "job.recovery_batch_size": self.recovery_batch_size,
            "job.recovery_callback_batch_size": self.recovery_callback_batch_size,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than 0")
        if self.max_active_jobs < 0:
            raise ValueError("MAX_ACTIVE_JOBS must be greater than or equal to 0")
        return self


class ObservabilitySettings(ConfigSection):
    log_level: str = "INFO"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    service: ServiceSettings = Field(default_factory=ServiceSettings)
    database: DatabaseSettings
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    security: SecuritySettings
    storage: StorageSettings = Field(default_factory=StorageSettings)
    callback: CallbackSettings
    ai_provider: AIProviderSettings = Field(default_factory=AIProviderSettings)
    registry: RegistrySettings = Field(default_factory=RegistrySettings)
    billing: BillingSettings = Field(default_factory=BillingSettings)
    ops_dashboard: OpsDashboardSettings = Field(default_factory=OpsDashboardSettings)
    job: JobSettings = Field(default_factory=JobSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        def flat_env_source() -> dict[str, Any]:
            if init_settings():
                return {}
            return _flat_env_settings_source()

        return (init_settings, flat_env_source, file_secret_settings)

    @model_validator(mode="after")
    def validate_config_invariants(self) -> "Settings":
        if self.runtime.is_release_env:
            if self.security.disable_http_auth_header or self.security.disable_caller_id_header:
                raise ValueError("release APP_ENV must not disable HTTP auth or caller id headers")
            if self.callback.allow_insecure_callbacks:
                raise ValueError("release APP_ENV must not allow insecure callbacks")
            if _looks_like_placeholder_secret(self.security.api_key) or len(self.security.api_key) < 16:
                raise ValueError("release APP_ENV requires a non-placeholder SERVICE_API_KEY with at least 16 characters")
            if (
                _looks_like_placeholder_secret(self.callback.signing_secret_value)
                or len(self.callback.signing_secret_value) < 32
            ):
                raise ValueError(
                    "release APP_ENV requires a non-placeholder CALLBACK_SIGNING_SECRET with at least 32 characters"
                )

        if self.security.disable_http_auth_header or self.security.disable_caller_id_header:
            for name, value in {
                "DATABASE_URL": self.database.url,
                "REDIS_URL": self.broker.redis_url,
            }.items():
                if not _looks_like_local_service_url(value):
                    raise ValueError(
                        f"{name} must point to a local service when auth header disable flags are enabled"
                    )
            _log.warning(
                "insecure HTTP auth/caller header disable flag enabled; use local development only"
            )

        delivery_timeout = self.callback.delivery_timeout_seconds
        if delivery_timeout >= self.callback.retry_delay_seconds:
            raise ValueError(
                f"derived callback claim window({delivery_timeout}s) must be < "
                f"callback.retry_delay_seconds({self.callback.retry_delay_seconds}s): "
                "retry interval must start after the callback claim window."
            )

        if self.worker_soft_time_limit >= self.worker_hard_time_limit:
            raise ValueError("worker hard timeout must be greater than worker soft timeout")
        if self.worker_hard_time_limit >= self.job_stale_running_seconds:
            raise ValueError("job stale running threshold must be greater than worker hard timeout")

        self._validate_registry_files()
        return self

    @property
    def worker_soft_time_limit(self) -> int:
        return self.ai_provider.model_call_timeout_seconds + _WORKER_SOFT_TIMEOUT_BUFFER

    @property
    def worker_hard_time_limit(self) -> int:
        return self.worker_soft_time_limit + _WORKER_HARD_TIMEOUT_BUFFER

    @property
    def job_stale_running_seconds(self) -> int:
        return self.worker_hard_time_limit + _JOB_STALE_RUNNING_BUFFER

    def application_env_value(self, name: str) -> str:
        if name == "OPENAI_API_KEY":
            return self.ai_provider.openai_api_key_value
        if name == "OPENAI_BASE_URL":
            return self.ai_provider.openai_base_url
        if name == "DASHSCOPE_API_KEY":
            return self.ai_provider.dashscope_api_key_value
        if name == "DASHSCOPE_BASE_URL":
            return self.ai_provider.dashscope_base_url
        raise RuntimeError(f"unsupported model requires_env key: {name}")

    def _validate_registry_files(self) -> None:
        model_path = self.registry.model_config_path
        prompt_path = self.registry.prompt_config_path
        pricing_path = self.billing.pricing_config_path
        if not model_path.exists():
            raise ValueError(f"model config not found: {model_path}")
        if not prompt_path.exists():
            raise ValueError(f"prompt config not found: {prompt_path}")
        if not pricing_path.exists():
            raise ValueError(f"pricing config not found: {pricing_path}")
        try:
            raw = yaml.safe_load(model_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"model config is invalid YAML: {model_path}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
            raise ValueError("model config models must be a YAML list")
        enabled_model_ids = {
            item.get("id"): item
            for item in raw["models"]
            if isinstance(item, dict) and item.get("enabled") is True and isinstance(item.get("id"), str)
        }
        default_model_ids = raw.get("default_model_ids")
        if not isinstance(default_model_ids, dict) or not default_model_ids:
            raise ValueError("model config default_model_ids must be a non-empty YAML object")
        for capability, model_id in default_model_ids.items():
            if not isinstance(capability, str) or not capability.strip():
                raise ValueError("model config default_model_ids keys must be non-empty strings")
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError(f"model config default_model_ids.{capability} must be a non-empty string")
            if model_id not in enabled_model_ids:
                raise ValueError(f"model config default_model_ids.{capability} must reference an enabled model: {model_id}")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
