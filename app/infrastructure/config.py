from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]

# ── Timeout chain safety margins (code constants, not configurable) ───────────
# These are structural invariants in the Celery timeout chain.
# L1 = MODEL_CALL_TIMEOUT_SECONDS (operator-configured anchor).
# L3–L5 are derived from L1 + these buffers; they must never shrink below minimum.
_CELERY_SOFT_TIMEOUT_BUFFER: int = 300   # time for L1 cleanup (job write + callback) after AI timeout
_CELERY_HARD_TIMEOUT_BUFFER: int = 60    # time for soft-limit handler to finish before SIGKILL
_JOB_STALE_RUNNING_BUFFER: int = 600     # recovery scan gap to avoid mis-classifying a recently killed job
_CALLBACK_DELIVERY_WINDOW_BUFFER: int = 175  # per-delivery claim window above CALLBACK_TIMEOUT_SECONDS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Infrastructure credentials ────────────────────────────────────────────
    DATABASE_URL: str
    DB_SSL: bool = True
    DB_POOL_SIZE: int = 5
    # Advanced DB tuning — rarely changed; override via env if needed
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 1800
    SERVICE_API_KEY: str

    REDIS_URL: str = "redis://127.0.0.1:26379/0"

    # ── Access control ────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    # ── Object storage ────────────────────────────────────────────────────────
    STORAGE_BACKEND: str = "local"
    LOCAL_OBJECT_STORAGE_PATH: str = "storage/objects"
    OSS_BUCKET: str = ""
    OSS_REGION: str = ""
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_PROJECT_ROOT: str = ""
    OSS_OUTPUT_PREFIX: str = "novel-localization/jobs"
    # Optional endpoint overrides — derived from OSS_REGION when empty
    OSS_ENDPOINT: str = ""
    OSS_PUBLIC_ENDPOINT: str = ""
    OSS_ENDPOINT_STYLE: str = ""
    OSS_SCHEME: str = "https"

    # ── Callback ──────────────────────────────────────────────────────────────
    CALLBACK_SIGNING_SECRET: str = ""
    ALLOW_INSECURE_CALLBACKS: bool = False
    CALLBACK_TIMEOUT_SECONDS: int = 5
    CALLBACK_MAX_DELIVERY_ATTEMPTS: int = 12
    CALLBACK_RETRY_DELAY_SECONDS: int = 300

    # ── AI provider ───────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    DEFAULT_MODEL_ID: str = "gpt-4.1"
    MODEL_CONFIG_PATH: str = "app/infrastructure/models.yaml"
    # L1 anchor: asyncio.wait_for hard cut on AI call; L3–L5 are derived automatically.
    MODEL_CALL_TIMEOUT_SECONDS: int = 300

    # ── Capacity & limits ─────────────────────────────────────────────────────
    MAX_ACTIVE_JOBS: int = 5000     # queued+running cap; 0 = disable check
    OSS_INPUT_MAX_BYTES: int = 5_242_880

    # ── Job lifecycle — operational tuning (override via env when needed) ─────
    JOB_ORPHAN_TIMEOUT_SECONDS: int = 300
    JOB_RECOVERY_INTERVAL_SECONDS: int = 60
    JOB_RECOVERY_BATCH_SIZE: int = 100
    JOB_RECOVERY_CALLBACK_BATCH_SIZE: int = 50

    # ── Celery internals ──────────────────────────────────────────────────────
    CELERY_MAX_RETRIES: int = 0
    CELERY_RETRY_DELAY: int = 60
    CELERY_RESULT_EXPIRES: int = 86400

    # ── Novel localization ────────────────────────────────────────────────────
    NOVEL_LOCALIZATION_CHUNKING_ENABLED: bool = False
    NOVEL_LOCALIZATION_SINGLE_MAX_CHARS: int = 20000
    NOVEL_LOCALIZATION_CHUNK_SIZE: int = 3000  # internal chunk target, rarely changed
    PROMPT_CONFIG_PATH: str = "app/infrastructure/novel_loc/prompts.yaml"

    LOG_LEVEL: str = Field(default="INFO")

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("STORAGE_BACKEND")
    @classmethod
    def validate_storage_backend(cls, value: str) -> str:
        if value not in {"local", "aliyun_oss"}:
            raise ValueError("STORAGE_BACKEND must be local or aliyun_oss")
        return value

    @model_validator(mode="after")
    def validate_config_invariants(self) -> "Settings":
        import logging as _logging
        _log = _logging.getLogger(__name__)

        positive_fields = {
            "DB_POOL_SIZE": self.DB_POOL_SIZE,
            "DB_POOL_RECYCLE": self.DB_POOL_RECYCLE,
            "CALLBACK_TIMEOUT_SECONDS": self.CALLBACK_TIMEOUT_SECONDS,
            "CALLBACK_MAX_DELIVERY_ATTEMPTS": self.CALLBACK_MAX_DELIVERY_ATTEMPTS,
            "CALLBACK_RETRY_DELAY_SECONDS": self.CALLBACK_RETRY_DELAY_SECONDS,
            "MODEL_CALL_TIMEOUT_SECONDS": self.MODEL_CALL_TIMEOUT_SECONDS,
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
            "CELERY_MAX_RETRIES": self.CELERY_MAX_RETRIES,
        }
        for name, value in non_negative_fields.items():
            if value < 0:
                raise ValueError(f"{name} must be greater than or equal to 0")

        # Callback delivery window invariant:
        # delivery_timeout < retry_delay ensures no two workers claim the same callback.
        delivery_timeout = self.CALLBACK_TIMEOUT_SECONDS + _CALLBACK_DELIVERY_WINDOW_BUFFER
        if delivery_timeout >= self.CALLBACK_RETRY_DELAY_SECONDS:
            raise ValueError(
                f"CALLBACK_TIMEOUT_SECONDS({self.CALLBACK_TIMEOUT_SECONDS}) + "
                f"internal window buffer({_CALLBACK_DELIVERY_WINDOW_BUFFER}) "
                f"= {delivery_timeout}s must be < CALLBACK_RETRY_DELAY_SECONDS({self.CALLBACK_RETRY_DELAY_SECONDS}s): "
                "delivery window must not overlap retry interval."
            )

        if not self.CALLBACK_SIGNING_SECRET:
            _log.warning(
                "CALLBACK_SIGNING_SECRET is not configured — callback HMAC signatures will be invalid"
            )
        return self

    # ── Derived: Celery timeout chain (L1 anchor + fixed buffers) ─────────────

    @property
    def celery_soft_time_limit(self) -> int:
        return self.MODEL_CALL_TIMEOUT_SECONDS + _CELERY_SOFT_TIMEOUT_BUFFER

    @property
    def celery_time_limit(self) -> int:
        return self.celery_soft_time_limit + _CELERY_HARD_TIMEOUT_BUFFER

    @property
    def job_stale_running_seconds(self) -> int:
        return self.celery_time_limit + _JOB_STALE_RUNNING_BUFFER

    @property
    def callback_delivery_timeout_seconds(self) -> int:
        return self.CALLBACK_TIMEOUT_SECONDS + _CALLBACK_DELIVERY_WINDOW_BUFFER

    # ── Derived: OSS ──────────────────────────────────────────────────────────

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

    # ── Derived: misc ─────────────────────────────────────────────────────────

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
    def model_config_path(self) -> Path:
        path = Path(self.MODEL_CONFIG_PATH)
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
