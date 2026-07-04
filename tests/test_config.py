import pytest
from pydantic import ValidationError

from app.core import config as config_module
from scripts.verify import env_config_check as env_check_module
from app.core.config import (
    Settings,
    _CALLBACK_DELIVERY_CLAIM_GRACE,
    _JOB_STALE_RUNNING_BUFFER,
    _WORKER_HARD_TIMEOUT_BUFFER,
    _WORKER_SOFT_TIMEOUT_BUFFER,
)
from scripts.verify.env_config_check import (
    APPLICATION_ENV_KEYS,
    DERIVED_ENV_KEYS,
    LAUNCHER_ENV_KEYS,
    _key_set,
    check_example_alignment,
    check_file,
)


def _settings_kwargs(**overrides):
    values = {
        "APP_ENV": "local",
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/fastapi_best_ai_architecture",
        "SERVICE_API_KEY": "test-token",
        "CALLBACK_SIGNING_SECRET": "test-callback-secret",
        "DISABLE_HTTP_AUTH_HEADER": False,
        "DISABLE_CALLER_ID_HEADER": False,
    }
    values.update(overrides)
    return values


def _build_settings(**overrides) -> Settings:
    values = _settings_kwargs(**overrides)
    nested: dict[str, dict[str, object]] = {}
    for env_key, value in values.items():
        section_name, field_name = config_module.APPLICATION_ENV_FIELD_MAP[env_key]
        nested.setdefault(section_name, {})[field_name] = value
    return Settings(**nested)


def test_settings_rejects_zero_or_negative_control_values():
    with pytest.raises(ValidationError, match="MODEL_CALL_TIMEOUT_SECONDS"):
        _build_settings(MODEL_CALL_TIMEOUT_SECONDS=0)

    with pytest.raises(ValidationError, match="CALLBACK_TIMEOUT_SECONDS"):
        _build_settings(CALLBACK_TIMEOUT_SECONDS=-1)

    with pytest.raises(ValidationError, match="OSS_INPUT_MAX_BYTES"):
        _build_settings(OSS_INPUT_MAX_BYTES=0)

    with pytest.raises(ValidationError, match="DB_POOL_SIZE"):
        _build_settings(DB_POOL_SIZE=0)


def test_derived_timeout_properties_use_fixed_buffers():
    # Buffer values are now code constants — passing them has no effect.
    s = _build_settings(MODEL_CALL_TIMEOUT_SECONDS=300)
    assert s.worker_soft_time_limit == 300 + _WORKER_SOFT_TIMEOUT_BUFFER
    assert s.worker_hard_time_limit == 300 + _WORKER_SOFT_TIMEOUT_BUFFER + _WORKER_HARD_TIMEOUT_BUFFER
    assert s.job_stale_running_seconds == (
        300 + _WORKER_SOFT_TIMEOUT_BUFFER + _WORKER_HARD_TIMEOUT_BUFFER + _JOB_STALE_RUNNING_BUFFER
    )
    assert s.callback.delivery_timeout_seconds == 5 + _CALLBACK_DELIVERY_CLAIM_GRACE


def test_security_header_disable_flags_default_to_false():
    s = _build_settings()

    assert s.security.disable_http_auth_header is False
    assert s.security.disable_caller_id_header is False


def test_ops_dashboard_defaults_and_overrides():
    default_settings = _build_settings()

    assert default_settings.ops_dashboard.enabled is False
    assert default_settings.ops_dashboard.require_auth is False
    assert default_settings.ops_dashboard.refresh_seconds == 15
    assert default_settings.ops_dashboard.max_window_seconds == 604_800
    assert default_settings.ops_dashboard.query_timeout_seconds == 2

    custom_settings = _build_settings(
        OPS_DASHBOARD_ENABLED=True,
        OPS_DASHBOARD_REQUIRE_AUTH=True,
        OPS_DASHBOARD_REFRESH_SECONDS=30,
        OPS_DASHBOARD_MAX_WINDOW_SECONDS=3_600,
        OPS_DASHBOARD_QUERY_TIMEOUT_SECONDS=1,
    )

    assert custom_settings.ops_dashboard.enabled is True
    assert custom_settings.ops_dashboard.require_auth is True
    assert custom_settings.ops_dashboard.refresh_seconds == 30
    assert custom_settings.ops_dashboard.max_window_seconds == 3_600
    assert custom_settings.ops_dashboard.query_timeout_seconds == 1


def test_ops_dashboard_rejects_invalid_controls():
    with pytest.raises(ValidationError, match="OPS_DASHBOARD_REFRESH_SECONDS"):
        _build_settings(OPS_DASHBOARD_REFRESH_SECONDS=4)

    with pytest.raises(ValidationError, match="OPS_DASHBOARD_MAX_WINDOW_SECONDS"):
        _build_settings(OPS_DASHBOARD_MAX_WINDOW_SECONDS=599)

    with pytest.raises(ValidationError, match="OPS_DASHBOARD_QUERY_TIMEOUT_SECONDS"):
        _build_settings(OPS_DASHBOARD_QUERY_TIMEOUT_SECONDS=0)


def test_app_env_defaults_and_release_envs():
    local = _build_settings()
    assert local.runtime.app_env == "local"
    assert local.runtime.is_release_env is False

    dev = _build_settings(APP_ENV="dev")
    assert dev.runtime.app_env == "dev"
    assert dev.runtime.is_release_env is False

    with pytest.raises(ValidationError, match="APP_ENV"):
        _build_settings(APP_ENV="prod")


def test_template_identity_defaults_and_overrides():
    service_fields = config_module.ServiceSettings.model_fields
    assert service_fields["template_name"].default == "fastapi-best-ai-architecture"
    assert service_fields["name"].default == "fastapi-best-ai-architecture"
    assert service_fields["title"].default == "FastAPI Best AI Architecture"

    custom_settings = _build_settings(
        TEMPLATE_NAME="invoice-ai-template",
        SERVICE_NAME="invoice-ai-service",
        SERVICE_TITLE="Invoice AI Service",
    )
    assert custom_settings.service.template_name == "invoice-ai-template"
    assert custom_settings.service.name == "invoice-ai-service"
    assert custom_settings.service.title == "Invoice AI Service"


def test_poster_title_image_model_config_defaults_and_overrides():
    default_settings = _build_settings()
    assert default_settings.registry.model_config_path_raw == "app/core/models.yaml"
    assert default_settings.registry.prompt_config_path_raw == "app/core/prompts.yaml"


def test_poster_title_image_max_draw_count_config_defaults_and_overrides():
    default_settings = _build_settings()
    assert default_settings.job.poster_title_image_max_draw_count == 4

    custom_settings = _build_settings(POSTER_TITLE_IMAGE_MAX_DRAW_COUNT=2)
    assert custom_settings.job.poster_title_image_max_draw_count == 2

    with pytest.raises(ValidationError, match="POSTER_TITLE_IMAGE_MAX_DRAW_COUNT"):
        _build_settings(POSTER_TITLE_IMAGE_MAX_DRAW_COUNT=0)

    with pytest.raises(ValidationError, match="POSTER_TITLE_IMAGE_MAX_DRAW_COUNT"):
        _build_settings(POSTER_TITLE_IMAGE_MAX_DRAW_COUNT=5)


def test_poster_title_image_max_items_config_defaults_and_overrides():
    default_settings = _build_settings()
    assert default_settings.job.poster_title_image_max_items == 50

    custom_settings = _build_settings(POSTER_TITLE_IMAGE_MAX_ITEMS=12)
    assert custom_settings.job.poster_title_image_max_items == 12

    with pytest.raises(ValidationError, match="POSTER_TITLE_IMAGE_MAX_ITEMS"):
        _build_settings(POSTER_TITLE_IMAGE_MAX_ITEMS=0)


def test_poster_title_image_oss_allowlist_config_defaults_and_overrides():
    default_settings = _build_settings()
    assert default_settings.job.poster_title_image_allowed_oss_buckets == ("local-dev",)
    assert default_settings.job.poster_title_image_allowed_oss_regions == ("local",)

    custom_settings = _build_settings(
        POSTER_TITLE_IMAGE_ALLOWED_OSS_BUCKETS="cpp-rs-dev, cpp-rs-prod,cpp-rs-dev",
        POSTER_TITLE_IMAGE_ALLOWED_OSS_REGIONS="ap-southeast-1,cn-shanghai",
    )

    assert custom_settings.job.poster_title_image_allowed_oss_buckets == ("cpp-rs-dev", "cpp-rs-prod")
    assert custom_settings.job.poster_title_image_allowed_oss_regions == ("ap-southeast-1", "cn-shanghai")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("POSTER_TITLE_IMAGE_ALLOWED_OSS_BUCKETS", ""),
        ("POSTER_TITLE_IMAGE_ALLOWED_OSS_BUCKETS", "cpp-rs-dev,,cpp-rs-prod"),
        ("POSTER_TITLE_IMAGE_ALLOWED_OSS_REGIONS", ""),
        ("POSTER_TITLE_IMAGE_ALLOWED_OSS_REGIONS", "ap-southeast-1,"),
    ],
)
def test_poster_title_image_oss_allowlist_rejects_empty_values(key, value):
    with pytest.raises(ValidationError, match=key):
        _build_settings(**{key: value})


def test_settings_requires_callback_signing_secret():
    with pytest.raises(ValidationError, match="CALLBACK_SIGNING_SECRET"):
        _build_settings(CALLBACK_SIGNING_SECRET="")


def test_security_header_disable_flags_parse_bool_strings():
    s = _build_settings(
        DISABLE_HTTP_AUTH_HEADER="true",
        DISABLE_CALLER_ID_HEADER="false",
    )

    assert s.security.disable_http_auth_header is True
    assert s.security.disable_caller_id_header is False


def test_security_header_disable_flags_reject_ambiguous_bool_strings():
    with pytest.raises(ValidationError, match="header disable flags"):
        _build_settings(DISABLE_HTTP_AUTH_HEADER="1")

    with pytest.raises(ValidationError, match="header disable flags"):
        _build_settings(DISABLE_CALLER_ID_HEADER="yes")


def test_security_header_disable_flags_require_local_service_urls():
    with pytest.raises(ValidationError, match="DATABASE_URL must point to a local service"):
        _build_settings(
            DATABASE_URL="postgresql+asyncpg://postgres:postgres@db.example.com:5432/fastapi_best_ai_architecture",
            DISABLE_HTTP_AUTH_HEADER=True,
        )

    with pytest.raises(ValidationError, match="DATABASE_URL must point to a local service"):
        _build_settings(
            DATABASE_URL="postgresql+asyncpg://postgres:postgres@postgres:5432/fastapi_best_ai_architecture",
            DISABLE_HTTP_AUTH_HEADER=True,
        )

    with pytest.raises(ValidationError, match="REDIS_URL must point to a local service"):
        _build_settings(
            REDIS_URL="redis://redis.example.com:6379/0",
            DISABLE_CALLER_ID_HEADER=True,
        )

    with pytest.raises(ValidationError, match="REDIS_URL must point to a local service"):
        _build_settings(
            REDIS_URL="redis://redis:6379/0",
            DISABLE_CALLER_ID_HEADER=True,
        )


def test_redis_list_broker_is_allowed_for_local_and_remote_redis():
    local = _build_settings(
        REDIS_URL="redis://127.0.0.1:26379/0",
        TASKIQ_BROKER_KIND="redis_list",
    )

    assert local.broker.kind == "redis_list"

    remote_list = _build_settings(
        REDIS_URL="redis://redis:6379/0",
        TASKIQ_BROKER_KIND="redis_list",
    )
    assert remote_list.broker.kind == "redis_list"

    remote_stream = _build_settings(
        REDIS_URL="redis://redis:6379/0",
        TASKIQ_BROKER_KIND="redis_stream",
    )
    assert remote_stream.broker.kind == "redis_stream"


def _release_settings_kwargs(**overrides):
    values = {
        "APP_ENV": "test",
        "SERVICE_API_KEY": "release-service-token-32",
        "CALLBACK_SIGNING_SECRET": "release-callback-secret-32-bytes",
        "ALLOW_INSECURE_CALLBACKS": False,
        "STORAGE_BACKEND": "aliyun_oss",
        "OSS_BUCKET": "bucket",
        "OSS_REGION": "cn-test",
        "OSS_ACCESS_KEY_ID": "access-key-id",
        "OSS_ACCESS_KEY_SECRET": "access-key-secret",
        "OSS_PROJECT_ROOT": "project/root",
    }
    values.update(overrides)
    return values


def test_release_app_env_accepts_production_grade_config():
    settings = _build_settings(**_release_settings_kwargs())

    assert settings.runtime.app_env == "test"
    assert settings.runtime.is_release_env is True
    assert settings.storage.backend == "aliyun_oss"


def test_release_app_env_accepts_redis_list_broker():
    test_settings = _build_settings(**_release_settings_kwargs(APP_ENV="test", TASKIQ_BROKER_KIND="redis_list"))
    prd_settings = _build_settings(**_release_settings_kwargs(APP_ENV="prd", TASKIQ_BROKER_KIND="redis_list"))

    assert test_settings.runtime.is_release_env is True
    assert test_settings.broker.kind == "redis_list"
    assert prd_settings.runtime.is_release_env is True
    assert prd_settings.broker.kind == "redis_list"


def test_release_app_env_uses_same_rules_for_test_and_prd():
    test_settings = _build_settings(**_release_settings_kwargs(APP_ENV="test"))
    prd_settings = _build_settings(**_release_settings_kwargs(APP_ENV="prd"))

    assert test_settings.runtime.is_release_env is True
    assert prd_settings.runtime.is_release_env is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"DISABLE_HTTP_AUTH_HEADER": True}, "must not disable HTTP auth"),
        ({"DISABLE_CALLER_ID_HEADER": True}, "must not disable HTTP auth"),
        ({"ALLOW_INSECURE_CALLBACKS": True}, "must not allow insecure callbacks"),
        ({"STORAGE_BACKEND": "local"}, "must not use STORAGE_BACKEND=local"),
        ({"SERVICE_API_KEY": "<替换为随机 token>"}, "SERVICE_API_KEY"),
        ({"SERVICE_API_KEY": "short"}, "SERVICE_API_KEY"),
        ({"CALLBACK_SIGNING_SECRET": "<替换为随机 32 字节 hex>"}, "CALLBACK_SIGNING_SECRET"),
        ({"CALLBACK_SIGNING_SECRET": "short"}, "CALLBACK_SIGNING_SECRET"),
    ],
)
def test_release_app_env_rejects_non_release_safe_config(overrides, message):
    with pytest.raises(ValidationError, match=message):
        _build_settings(**_release_settings_kwargs(**overrides))


def test_settings_rejects_buffers_below_minimum():
    s = _build_settings(MODEL_CALL_TIMEOUT_SECONDS=1)
    assert s.worker_soft_time_limit == 1 + _WORKER_SOFT_TIMEOUT_BUFFER
    assert _WORKER_SOFT_TIMEOUT_BUFFER >= 300
    assert _WORKER_HARD_TIMEOUT_BUFFER >= 60
    assert _JOB_STALE_RUNNING_BUFFER >= 600


def test_settings_rejects_old_flat_constructor_keys():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Settings(**_settings_kwargs())


def test_settings_secrets_are_not_dumped_or_repr_exposed():
    s = _build_settings(
        SERVICE_API_KEY="secret-service-token",
        CALLBACK_SIGNING_SECRET="secret-callback-token",
        OPENAI_API_KEY="secret-openai-token",
    )

    dumped = repr(s.model_dump())
    rendered = repr(s)

    assert "secret-service-token" not in dumped
    assert "secret-callback-token" not in dumped
    assert "secret-openai-token" not in dumped
    assert "secret-service-token" not in rendered
    assert "secret-callback-token" not in rendered
    assert "secret-openai-token" not in rendered


def test_callback_delivery_window_is_anchored_to_http_timeout_not_retry_delay():
    fast_retry = _build_settings(
        CALLBACK_TIMEOUT_SECONDS=5,
    )
    slow_retry = _build_settings(
        CALLBACK_TIMEOUT_SECONDS=10,
    )

    assert fast_retry.callback.delivery_timeout_seconds == 5 + _CALLBACK_DELIVERY_CLAIM_GRACE
    assert slow_retry.callback.delivery_timeout_seconds == 10 + _CALLBACK_DELIVERY_CLAIM_GRACE


def test_callback_retry_interval_is_internal_not_flat_env_control():
    with pytest.raises(KeyError, match="CALLBACK_RETRY_DELAY_SECONDS"):
        _build_settings(CALLBACK_RETRY_DELAY_SECONDS=300)


def test_settings_rejects_negative_or_zero_control_values():
    with pytest.raises(ValidationError, match="DB_MAX_OVERFLOW"):
        _build_settings(DB_MAX_OVERFLOW=-1)

    with pytest.raises(ValidationError, match="MAX_ACTIVE_JOBS"):
        _build_settings(MAX_ACTIVE_JOBS=-1)


@pytest.mark.parametrize(
    "key",
    [
        "ENABLE_MOCK_INTERFACES",
        "SHORT_DRAMA_RS_BASE_URL",
        "SHORT_DRAMA_RS_RESULT_MOCK_ENABLED",
        "SHORT_DRAMA_RS_SCHEMA_MOCK_ENABLED",
        "SHORT_DRAMA_RS_TIMEOUT_SECONDS",
    ],
)
def test_env_config_check_rejects_removed_mock_and_rs_keys_as_deprecated(tmp_path, key):
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}=value\n", encoding="utf-8")

    issues = check_file(env_file)

    assert any(f"deprecated or unsupported config key: {key}" in issue for issue in issues)
    assert not any(f"unknown config key: {key}" in issue for issue in issues)


@pytest.mark.parametrize(
    "key",
    [
        "JOB_STALE_RUNNING_SECONDS",
        "WORKER_SOFT_TIME_LIMIT",
        "CALLBACK_DELIVERY_TIMEOUT_SECONDS",
    ],
)
def test_env_config_check_rejects_derived_keys(tmp_path, key):
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}=1\n", encoding="utf-8")

    issues = check_file(env_file)

    assert key in DERIVED_ENV_KEYS
    assert issues == [f"{env_file}:1: derived config key must not be set in env: {key}"]


def test_env_config_check_rejects_lowercase_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("service_api_key=value\n", encoding="utf-8")

    issues = check_file(env_file)

    assert issues == [f"{env_file}:1: config key must be uppercase: service_api_key"]


def test_settings_dotenv_source_rejects_lowercase_keys():
    assert config_module._unknown_dotenv_keys({"service_api_key": "value"}) == ["service_api_key"]


def test_settings_dotenv_source_reads_env_file_only_when_explicit(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "APP_ENV=local",
                "DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/local_db",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.test").write_text(
        "\n".join(
            [
                "APP_ENV=test",
                "DATABASE_URL=postgresql+asyncpg://postgres:postgres@test-db:5432/test_db",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ENV_FILE", raising=False)

    implicit = config_module._flat_env_settings_source()
    assert implicit["runtime"]["app_env"] == "local"
    assert implicit["database"]["url"].endswith("/local_db")

    monkeypatch.setenv("ENV_FILE", ".env.test")
    explicit = config_module._flat_env_settings_source()
    assert explicit["runtime"]["app_env"] == "test"
    assert explicit["database"]["url"].endswith("/test_db")


def test_settings_dotenv_source_can_skip_default_env_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SERVICE_API_KEY=local-token",
                "CALLBACK_SIGNING_SECRET=local-callback-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.test").write_text(
        "APP_ENV=test\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("ENV_FILE", ".env.test")
    monkeypatch.setenv("APP_CONFIG_SKIP_DEFAULT_ENV_FILE", "true")
    for key in config_module.APPLICATION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    data = config_module._flat_env_settings_source()

    assert data == {"runtime": {"app_env": "test"}}


def test_settings_dotenv_source_requires_explicit_env_file_to_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("ENV_FILE", ".env.missing")

    with pytest.raises(ValueError, match="ENV_FILE not found"):
        config_module._flat_env_settings_source()


def test_settings_source_rejects_removed_application_env_key_from_dotenv(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("OPS_DASHBOARD_MOCK_DATA_ENABLED=true\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("ENV_FILE", raising=False)
    monkeypatch.delenv("OPS_DASHBOARD_MOCK_DATA_ENABLED", raising=False)

    with pytest.raises(ValueError, match="unsupported keys .*OPS_DASHBOARD_MOCK_DATA_ENABLED"):
        config_module._flat_env_settings_source()


def test_settings_source_rejects_removed_application_env_key_from_process_env(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("ENV_FILE", raising=False)
    monkeypatch.setenv("OPS_DASHBOARD_MOCK_DATA_ENABLED", "true")

    with pytest.raises(ValueError, match="unsupported keys in process environment: OPS_DASHBOARD_MOCK_DATA_ENABLED"):
        config_module._flat_env_settings_source()


def test_env_config_examples_match_declared_manifests():
    assert _key_set(config_module.ROOT_DIR / ".env.example") == APPLICATION_ENV_KEYS | LAUNCHER_ENV_KEYS
    assert check_example_alignment() == []


def test_env_config_check_allows_launcher_keys_in_root_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("API_PORT=8100\nPOSTGRES_HOST_PORT=25432\n", encoding="utf-8")

    issues = check_file(env_file)

    assert "API_PORT" in LAUNCHER_ENV_KEYS
    assert "POSTGRES_HOST_PORT" in LAUNCHER_ENV_KEYS
    assert issues == []


def test_env_config_check_detects_launcher_key_missing_from_env_example(monkeypatch, tmp_path):
    env_example = tmp_path / ".env.example"
    env_example.write_text(
        "\n".join(f"{key}=value" for key in sorted((APPLICATION_ENV_KEYS | LAUNCHER_ENV_KEYS) - {"API_PORT"}))
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_check_module, "SERVICE_EXAMPLE_PATH", env_example)

    issues = check_example_alignment()

    assert issues == [f"{env_example}: missing root config key from .env.example: API_PORT"]


def test_env_config_check_rejects_unknown_root_keys(tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text("UNKNOWN_LOCAL_KEY=value\n", encoding="utf-8")

    issues = check_file(env_file)

    assert issues == [f"{env_file}:1: unknown config key: UNKNOWN_LOCAL_KEY"]
