from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str
    SERVICE_API_KEY: str

    REDIS_URL: str = "redis://127.0.0.1:16379/0"
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    STORAGE_BACKEND: str = "local"
    LOCAL_OBJECT_STORAGE_PATH: str = "storage/objects"
    OSS_BUCKET: str = ""
    OSS_REGION: str = ""
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_PROJECT_ROOT: str = ""
    OSS_PUBLIC_ENDPOINT: str = ""
    OSS_ENDPOINT: str = ""
    OSS_ENDPOINT_STYLE: str = ""
    OSS_SCHEME: str = "https"

    CALLBACK_SIGNING_SECRET: str = ""
    ALLOW_INSECURE_CALLBACKS: bool = False
    CALLBACK_TIMEOUT_SECONDS: int = 5

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    DEFAULT_MODEL_ID: str = "mock-novel-localizer"
    ENABLE_MOCK_MODEL: bool = True

    TEXT_INPUT_MAX_BYTES: int = 1_048_576
    OSS_INPUT_MAX_BYTES: int = 5_242_880
    JOB_QUEUE_TIMEOUT_SECONDS: int = 600
    JOB_EXECUTION_TIMEOUT_SECONDS: int = 1800
    CELERY_SOFT_TIME_LIMIT: int = 1800
    CELERY_TIME_LIMIT: int = 1860
    CELERY_MAX_RETRIES: int = 0
    CELERY_RETRY_DELAY: int = 60
    CELERY_RESULT_EXPIRES: int = 86400
    NOVEL_LOCALIZATION_P1_MAX_CHARS: int = 5000
    NOVEL_LOCALIZATION_CHUNK_SIZE: int = 4500

    LOG_LEVEL: str = Field(default="INFO")

    @field_validator("STORAGE_BACKEND")
    @classmethod
    def validate_storage_backend(cls, value: str) -> str:
        if value not in {"local", "aliyun_oss"}:
            raise ValueError("STORAGE_BACKEND must be local or aliyun_oss")
        return value

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
    def sync_database_url(self) -> str:
        if self.DATABASE_URL.startswith("postgresql+asyncpg://"):
            return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        return self.DATABASE_URL


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
