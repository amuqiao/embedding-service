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
    CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS: int = 175

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    DEFAULT_MODEL_ID: str = "gpt-4.1"
    MODEL_CALL_TIMEOUT_SECONDS: int = 300
    CELERY_SOFT_TIMEOUT_BUFFER_SECONDS: int = 300
    CELERY_HARD_TIMEOUT_BUFFER_SECONDS: int = 60
    JOB_STALE_RUNNING_BUFFER_SECONDS: int = 600
    MODEL_CALL_MAX_RETRIES: int = 0
    PROMPT_CONFIG_PATH: str = "app/infrastructure/novel_loc/prompts.yaml"

    OSS_INPUT_MAX_BYTES: int = 5_242_880
    MAX_ACTIVE_JOBS: int = 5000
    JOB_ORPHAN_TIMEOUT_SECONDS: int = 300
    JOB_RECOVERY_INTERVAL_SECONDS: int = 60
    JOB_RECOVERY_BATCH_SIZE: int = 100
    JOB_RECOVERY_CALLBACK_BATCH_SIZE: int = 50
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

        positive_fields = {
            "DB_POOL_SIZE": self.DB_POOL_SIZE,
            "DB_POOL_RECYCLE": self.DB_POOL_RECYCLE,
            "CALLBACK_TIMEOUT_SECONDS": self.CALLBACK_TIMEOUT_SECONDS,
            "CALLBACK_MAX_DELIVERY_ATTEMPTS": self.CALLBACK_MAX_DELIVERY_ATTEMPTS,
            "CALLBACK_RETRY_DELAY_SECONDS": self.CALLBACK_RETRY_DELAY_SECONDS,
            "CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS": self.CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS,
            "MODEL_CALL_TIMEOUT_SECONDS": self.MODEL_CALL_TIMEOUT_SECONDS,
            "CELERY_SOFT_TIMEOUT_BUFFER_SECONDS": self.CELERY_SOFT_TIMEOUT_BUFFER_SECONDS,
            "CELERY_HARD_TIMEOUT_BUFFER_SECONDS": self.CELERY_HARD_TIMEOUT_BUFFER_SECONDS,
            "JOB_STALE_RUNNING_BUFFER_SECONDS": self.JOB_STALE_RUNNING_BUFFER_SECONDS,
            "OSS_INPUT_MAX_BYTES": self.OSS_INPUT_MAX_BYTES,
            "JOB_ORPHAN_TIMEOUT_SECONDS": self.JOB_ORPHAN_TIMEOUT_SECONDS,
            "JOB_RECOVERY_INTERVAL_SECONDS": self.JOB_RECOVERY_INTERVAL_SECONDS,
            "JOB_RECOVERY_BATCH_SIZE": self.JOB_RECOVERY_BATCH_SIZE,
            "JOB_RECOVERY_CALLBACK_BATCH_SIZE": self.JOB_RECOVERY_CALLBACK_BATCH_SIZE,
            "CELERY_RETRY_DELAY": self.CELERY_RETRY_DELAY,
            "CELERY_RESULT_EXPIRES": self.CELERY_RESULT_EXPIRES,
            "NOVEL_LOCALIZATION_SINGLE_MAX_CHARS": self.NOVEL_LOCALIZATION_SINGLE_MAX_CHARS,
            "NOVEL_LOCALIZATION_CHUNK_SIZE": self.NOVEL_LOCALIZATION_CHUNK_SIZE,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than 0")

        non_negative_fields = {
            "DB_MAX_OVERFLOW": self.DB_MAX_OVERFLOW,
            "MAX_ACTIVE_JOBS": self.MAX_ACTIVE_JOBS,
            "MODEL_CALL_MAX_RETRIES": self.MODEL_CALL_MAX_RETRIES,
            "CELERY_MAX_RETRIES": self.CELERY_MAX_RETRIES,
        }
        for name, value in non_negative_fields.items():
            if value < 0:
                raise ValueError(f"{name} must be greater than or equal to 0")

        if self.CELERY_SOFT_TIMEOUT_BUFFER_SECONDS < 300:
            _log.warning(
                "CELERY_SOFT_TIMEOUT_BUFFER_SECONDS = %ds (recommend ≥ 300s). "
                "L3 may fire before L1 cleanup completes.",
                self.CELERY_SOFT_TIMEOUT_BUFFER_SECONDS,
            )
        if self.CELERY_HARD_TIMEOUT_BUFFER_SECONDS < 60:
            _log.warning(
                "CELERY_HARD_TIMEOUT_BUFFER_SECONDS = %ds (recommend ≥ 60s). "
                "SIGKILL may arrive before soft-limit handler finishes.",
                self.CELERY_HARD_TIMEOUT_BUFFER_SECONDS,
            )
        if self.JOB_STALE_RUNNING_BUFFER_SECONDS < 600:
            _log.warning(
                "JOB_STALE_RUNNING_BUFFER_SECONDS = %ds (recommend ≥ 600s). "
                "Stale scan may mis-classify recently killed jobs.",
                self.JOB_STALE_RUNNING_BUFFER_SECONDS,
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

    @property
    def celery_soft_time_limit(self) -> int:
        return self.MODEL_CALL_TIMEOUT_SECONDS + self.CELERY_SOFT_TIMEOUT_BUFFER_SECONDS

    @property
    def celery_time_limit(self) -> int:
        return self.celery_soft_time_limit + self.CELERY_HARD_TIMEOUT_BUFFER_SECONDS

    @property
    def job_stale_running_seconds(self) -> int:
        return self.celery_time_limit + self.JOB_STALE_RUNNING_BUFFER_SECONDS

    @property
    def callback_delivery_timeout_seconds(self) -> int:
        return self.CALLBACK_TIMEOUT_SECONDS + self.CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
