from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str
    DB_SSL: bool = True
    # API 侧连接池：pool_size × max_overflow × pods 数需 ≤ PG max_connections(默认 100)
    # 估算：3 API pods × (5+10) = 45 + 30 Worker 并发 = 75，留余量
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 1800
    SERVICE_API_KEY: str

    REDIS_URL: str = "redis://127.0.0.1:26379/0"
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    STORAGE_BACKEND: str = "local"
    LOCAL_OBJECT_STORAGE_PATH: str = "storage/objects"
    OSS_BUCKET: str = ""
    OSS_REGION: str = ""
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_PROJECT_ROOT: str = ""
    OSS_OUTPUT_PREFIX: str = "novel-localization/jobs"
    OSS_PUBLIC_ENDPOINT: str = ""
    OSS_ENDPOINT: str = ""
    OSS_ENDPOINT_STYLE: str = ""
    OSS_SCHEME: str = "https"

    CALLBACK_SIGNING_SECRET: str = ""
    ALLOW_INSECURE_CALLBACKS: bool = False
    CALLBACK_TIMEOUT_SECONDS: int = 5
    CALLBACK_MAX_DELIVERY_ATTEMPTS: int = 12
    CALLBACK_RETRY_DELAY_SECONDS: int = 300
    CALLBACK_DELIVERY_TIMEOUT_SECONDS: int = 180

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    DEFAULT_MODEL_ID: str = "gpt-4.1"
    MODEL_CALL_TIMEOUT_SECONDS: int = 300
    MODEL_CALL_MAX_RETRIES: int = 0
    PROMPT_CONFIG_PATH: str = "app/infrastructure/novel_loc/prompts.yaml"

    OSS_INPUT_MAX_BYTES: int = 5_242_880
    MAX_ACTIVE_JOBS: int = 5000
    JOB_ORPHAN_TIMEOUT_SECONDS: int = 300
    JOB_RECOVERY_INTERVAL_SECONDS: int = 60
    JOB_RECOVERY_BATCH_SIZE: int = 100
    JOB_RECOVERY_CALLBACK_BATCH_SIZE: int = 50
    JOB_STALE_RUNNING_SECONDS: int = 2460
    JOB_QUEUE_TIMEOUT_SECONDS: int = 600
    JOB_EXECUTION_TIMEOUT_SECONDS: int = 1800
    CELERY_SOFT_TIME_LIMIT: int = 1800
    CELERY_TIME_LIMIT: int = 1860
    CELERY_MAX_RETRIES: int = 0
    CELERY_RETRY_DELAY: int = 60
    CELERY_RESULT_EXPIRES: int = 86400
    NOVEL_LOCALIZATION_CHUNKING_ENABLED: bool = False
    NOVEL_LOCALIZATION_SINGLE_MAX_CHARS: int = 20000
    NOVEL_LOCALIZATION_CHUNK_SIZE: int = 3000

    LOG_LEVEL: str = Field(default="INFO")

    @field_validator("STORAGE_BACKEND")
    @classmethod
    def validate_storage_backend(cls, value: str) -> str:
        if value not in {"local", "aliyun_oss"}:
            raise ValueError("STORAGE_BACKEND must be local or aliyun_oss")
        return value

    @model_validator(mode="after")
    def validate_timeout_chain(self) -> "Settings":
        import logging as _logging
        _log = _logging.getLogger(__name__)

        if self.MODEL_CALL_TIMEOUT_SECONDS >= self.CELERY_SOFT_TIME_LIMIT:
            raise ValueError(
                f"MODEL_CALL_TIMEOUT_SECONDS ({self.MODEL_CALL_TIMEOUT_SECONDS}s) "
                f"must be less than CELERY_SOFT_TIME_LIMIT ({self.CELERY_SOFT_TIME_LIMIT}s). "
                f"Recommended margin: at least 300s."
            )
        if self.CELERY_SOFT_TIME_LIMIT >= self.CELERY_TIME_LIMIT:
            raise ValueError(
                f"CELERY_SOFT_TIME_LIMIT ({self.CELERY_SOFT_TIME_LIMIT}s) "
                f"must be less than CELERY_TIME_LIMIT ({self.CELERY_TIME_LIMIT}s). "
                f"Recommended margin: at least 60s."
            )
        if self.CELERY_TIME_LIMIT >= self.JOB_STALE_RUNNING_SECONDS:
            raise ValueError(
                f"CELERY_TIME_LIMIT ({self.CELERY_TIME_LIMIT}s) "
                f"must be less than JOB_STALE_RUNNING_SECONDS ({self.JOB_STALE_RUNNING_SECONDS}s). "
                f"Recommended margin: at least 600s."
            )

        margin1 = self.CELERY_SOFT_TIME_LIMIT - self.MODEL_CALL_TIMEOUT_SECONDS
        if margin1 < 300:
            _log.warning(
                "CELERY_SOFT_TIME_LIMIT - MODEL_CALL_TIMEOUT_SECONDS = %ds (recommend ≥ 300s). "
                "L3 may fire before L1 cleanup completes.",
                margin1,
            )
        margin2 = self.CELERY_TIME_LIMIT - self.CELERY_SOFT_TIME_LIMIT
        if margin2 < 60:
            _log.warning(
                "CELERY_TIME_LIMIT - CELERY_SOFT_TIME_LIMIT = %ds (recommend ≥ 60s). "
                "SIGKILL may arrive before soft-limit handler finishes.",
                margin2,
            )
        margin3 = self.JOB_STALE_RUNNING_SECONDS - self.CELERY_TIME_LIMIT
        if margin3 < 600:
            _log.warning(
                "JOB_STALE_RUNNING_SECONDS - CELERY_TIME_LIMIT = %ds (recommend ≥ 600s). "
                "Stale scan may mis-classify recently killed jobs.",
                margin3,
            )

        if not self.CALLBACK_SIGNING_SECRET:
            _log.warning(
                "CALLBACK_SIGNING_SECRET is not configured — callback HMAC signatures will be invalid"
            )
        return self

    @property
    def oss_endpoint(self) -> str:
        if self.OSS_ENDPOINT:
            return self.OSS_ENDPOINT
        if self.OSS_PUBLIC_ENDPOINT:
            return self.OSS_PUBLIC_ENDPOINT
        return f"oss-{self.OSS_REGION}.aliyuncs.com"

    @property
    def oss_endpoint_style(self) -> str:
        if self.OSS_ENDPOINT_STYLE:
            return self.OSS_ENDPOINT_STYLE
        if self.OSS_PUBLIC_ENDPOINT and self.oss_endpoint == self.OSS_PUBLIC_ENDPOINT:
            return "custom_domain"
        return "virtual_host"

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.ALLOWED_ORIGINS.split(",") if item.strip()]

    @property
    def local_object_storage_path(self) -> Path:
        path = Path(self.LOCAL_OBJECT_STORAGE_PATH)
        if not path.is_absolute():
            path = ROOT_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def prompt_config_path(self) -> Path:
        path = Path(self.PROMPT_CONFIG_PATH)
        if not path.is_absolute():
            path = ROOT_DIR / path
        return path

    @property
    def sync_database_url(self) -> str:
        if self.DATABASE_URL.startswith("postgresql+asyncpg://"):
            return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        return self.DATABASE_URL


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
