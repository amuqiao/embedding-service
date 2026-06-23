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

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_NAME = "fastapi-best-ai-architecture"
DEFAULT_SERVICE_TITLE = "FastAPI Best AI Architecture"

# Timeout chain safety margins. These are internal engineering margins, not env keys.
_WORKER_SOFT_TIMEOUT_BUFFER: int = 300
_WORKER_HARD_TIMEOUT_BUFFER: int = 60
_JOB_STALE_RUNNING_BUFFER: int = 600
_CALLBACK_DELIVERY_CLAIM_GRACE: int = 175

APPLICATION_ENV_FIELD_MAP: dict[str, tuple[str, str]] = {
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
    "DEFAULT_MODEL_ID": ("registry", "default_model_id"),
    "MODEL_CONFIG_PATH": ("registry", "model_config_path_raw"),
    "MODEL_CALL_TIMEOUT_SECONDS": ("ai_provider", "model_call_timeout_seconds"),
    "BILLING_ENABLED": ("billing", "enabled"),
    "MODEL_CATALOG_EXPOSE_BILLING_CAPABILITY": ("billing", "model_catalog_expose_billing_capability"),
    "PRICING_CONFIG_PATH": ("billing", "pricing_config_path_raw"),
    "MAX_ACTIVE_JOBS": ("job", "max_active_jobs"),
    "OSS_INPUT_MAX_BYTES": ("job", "oss_input_max_bytes"),
    "CALLBACK_TIMEOUT_SECONDS": ("callback", "timeout_seconds"),
    "CALLBACK_MAX_DELIVERY_ATTEMPTS": ("callback", "max_delivery_attempts"),
    "CALLBACK_RETRY_DELAY_SECONDS": ("callback", "retry_delay_seconds"),
    "JOB_ORPHAN_TIMEOUT_SECONDS": ("job", "orphan_timeout_seconds"),
    "JOB_DISPATCH_MAX_PUBLISH_ATTEMPTS": ("job", "dispatch_max_publish_attempts"),
    "PROMPT_CONFIG_PATH": ("registry", "prompt_config_path_raw"),
    "LOG_LEVEL": ("observability", "log_level"),
}

APPLICATION_ENV_KEYS = frozenset(APPLICATION_ENV_FIELD_MAP)
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
        "JOB_MAX_EXECUTION_ATTEMPTS",
        "JOB_RECOVERY_BATCH_SIZE",
        "JOB_RECOVERY_CALLBACK_BATCH_SIZE",
        "JOB_RECOVERY_INTERVAL_SECONDS",
        "JOB_STALE_RUNNING_BUFFER_SECONDS",
        "MODEL_CALL_MAX_RETRIES",
        "NOVEL_LOCALIZATION_CHUNKING_ENABLED",
        "NOVEL_LOCALIZATION_CHUNK_SIZE",
        "NOVEL_LOCALIZATION_SINGLE_MAX_CHARS",
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


def _assign_nested(data: dict[str, Any], path: tuple[str, str], value: str) -> None:
    group_name, field_name = path
    group = data.setdefault(group_name, {})
    group[field_name] = value


def _read_dotenv_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = dotenv_values(path)
    return {key: value for key, value in values.items() if key and value is not None}


def _unknown_dotenv_keys(dotenv: dict[str, str]) -> list[str]:
    return sorted(key for key in dotenv if key != key.upper() or key not in APPLICATION_ENV_KEYS)


def _flat_env_settings_source() -> dict[str, Any]:
    dotenv = _read_dotenv_values(ROOT_DIR / ".env")
    unknown = _unknown_dotenv_keys(dotenv)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unsupported keys in .env: {joined}")

    raw: dict[str, str] = dict(dotenv)
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
            raise ValueError("DB_POOL_RECYCLE must be greater than 0")
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
            return self.oss_endpoint_override
        if self.oss_public_endpoint:
            return self.oss_public_endpoint
        return f"oss-{self.oss_region}.aliyuncs.com"

    @property
    def oss_endpoint_style(self) -> str:
        if self.oss_public_endpoint and self.oss_endpoint == self.oss_public_endpoint:
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
            "CALLBACK_MAX_DELIVERY_ATTEMPTS": self.max_delivery_attempts,
            "CALLBACK_RETRY_DELAY_SECONDS": self.retry_delay_seconds,
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
    model_call_timeout_seconds: int = 300

    @model_validator(mode="after")
    def validate_ai_provider(self) -> "AIProviderSettings":
        if self.model_call_timeout_seconds <= 0:
            raise ValueError("MODEL_CALL_TIMEOUT_SECONDS must be greater than 0")
        return self

    @property
    def openai_api_key_value(self) -> str:
        return self.openai_api_key.get_secret_value()


class RegistrySettings(ConfigSection):
    default_model_id: str = "gpt-5.5"
    model_config_path_raw: str = "app/core/models.yaml"
    prompt_config_path_raw: str = "app/core/prompts.yaml"

    @property
    def model_config_path(self) -> Path:
        return _resolve_repo_path(self.model_config_path_raw)

    @property
    def prompt_config_path(self) -> Path:
        return _resolve_repo_path(self.prompt_config_path_raw)


class BillingSettings(ConfigSection):
    enabled: bool = True
    model_catalog_expose_billing_capability: bool = False
    pricing_config_path_raw: str = "app/core/pricing.yaml"

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


class JobSettings(ConfigSection):
    max_active_jobs: int = 5000
    oss_input_max_bytes: int = 5_242_880
    orphan_timeout_seconds: int = 300
    dispatch_max_publish_attempts: int = 12
    recovery_interval_seconds: int = 60
    recovery_batch_size: int = 100
    recovery_callback_batch_size: int = 50

    @model_validator(mode="after")
    def validate_job(self) -> "JobSettings":
        positive_fields = {
            "OSS_INPUT_MAX_BYTES": self.oss_input_max_bytes,
            "JOB_ORPHAN_TIMEOUT_SECONDS": self.orphan_timeout_seconds,
            "JOB_DISPATCH_MAX_PUBLISH_ATTEMPTS": self.dispatch_max_publish_attempts,
            "JOB_RECOVERY_INTERVAL_SECONDS": self.recovery_interval_seconds,
            "JOB_RECOVERY_BATCH_SIZE": self.recovery_batch_size,
            "JOB_RECOVERY_CALLBACK_BATCH_SIZE": self.recovery_callback_batch_size,
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

    service: ServiceSettings = Field(default_factory=ServiceSettings)
    database: DatabaseSettings
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    security: SecuritySettings
    storage: StorageSettings = Field(default_factory=StorageSettings)
    callback: CallbackSettings
    ai_provider: AIProviderSettings = Field(default_factory=AIProviderSettings)
    registry: RegistrySettings = Field(default_factory=RegistrySettings)
    billing: BillingSettings = Field(default_factory=BillingSettings)
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

        if self.broker.kind == "redis_list" and not _looks_like_local_service_url(self.broker.redis_url):
            raise ValueError("TASKIQ_BROKER_KIND=redis_list is local development only; use redis_stream")

        delivery_timeout = self.callback.delivery_timeout_seconds
        if delivery_timeout >= self.callback.retry_delay_seconds:
            raise ValueError(
                f"derived callback claim window({delivery_timeout}s) must be < "
                f"CALLBACK_RETRY_DELAY_SECONDS({self.callback.retry_delay_seconds}s): "
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
        enabled_ids = {
            item.get("id")
            for item in raw["models"]
            if isinstance(item, dict) and item.get("enabled") is True and isinstance(item.get("id"), str)
        }
        if self.registry.default_model_id not in enabled_ids:
            raise ValueError(
                f"DEFAULT_MODEL_ID must exist in enabled model config: {self.registry.default_model_id}"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
