import pytest
from pydantic import ValidationError

from app.core import config as config_module
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
    SCRIPT_ENV_KEYS,
    SCRIPT_OR_DEPLOYMENT_ENV_KEYS,
    _key_set,
    check_example_alignment,
    check_file,
)


def _settings_kwargs(**overrides):
    values = {
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


def test_redis_list_broker_is_local_development_only():
    local = _build_settings(
        REDIS_URL="redis://127.0.0.1:26379/0",
        TASKIQ_BROKER_KIND="redis_list",
    )

    assert local.broker.kind == "redis_list"

    with pytest.raises(ValidationError, match="redis_list is local development only"):
        _build_settings(
            REDIS_URL="redis://redis:6379/0",
            TASKIQ_BROKER_KIND="redis_list",
        )

    remote_stream = _build_settings(
        REDIS_URL="redis://redis:6379/0",
        TASKIQ_BROKER_KIND="redis_stream",
    )
    assert remote_stream.broker.kind == "redis_stream"


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
        CALLBACK_RETRY_DELAY_SECONDS=300,
    )
    slow_retry = _build_settings(
        CALLBACK_TIMEOUT_SECONDS=5,
        CALLBACK_RETRY_DELAY_SECONDS=600,
    )

    assert fast_retry.callback.delivery_timeout_seconds == 5 + _CALLBACK_DELIVERY_CLAIM_GRACE
    assert slow_retry.callback.delivery_timeout_seconds == 5 + _CALLBACK_DELIVERY_CLAIM_GRACE


def test_settings_rejects_callback_retry_interval_overlapping_delivery_window():
    # Violate: timeout(5) + internal grace(175) = 180 >= retry_delay(180)
    with pytest.raises(ValidationError, match="retry interval must start after"):
        _build_settings(
            CALLBACK_TIMEOUT_SECONDS=5,
            CALLBACK_RETRY_DELAY_SECONDS=180,
        )

    # Violate: timeout(130) + internal grace(175) = 305 >= retry_delay(300)
    with pytest.raises(ValidationError, match="retry interval must start after"):
        _build_settings(
            CALLBACK_TIMEOUT_SECONDS=130,
            CALLBACK_RETRY_DELAY_SECONDS=300,
        )


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


def test_env_config_examples_match_declared_manifests():
    assert _key_set(config_module.ROOT_DIR / ".env.example") == APPLICATION_ENV_KEYS
    assert _key_set(config_module.ROOT_DIR / "scripts" / ".env.example") == SCRIPT_ENV_KEYS
    assert check_example_alignment() == []


def test_env_config_check_rejects_script_keys_in_application_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("API_PORT=8100\nPOSTGRES_HOST_PORT=25432\n", encoding="utf-8")

    issues = check_file(env_file)

    assert "API_PORT" in SCRIPT_OR_DEPLOYMENT_ENV_KEYS
    assert "POSTGRES_HOST_PORT" in SCRIPT_OR_DEPLOYMENT_ENV_KEYS
    assert issues == [
        f"{env_file}:1: script key must be set in scripts/.env, not application env: API_PORT",
        f"{env_file}:2: script key must be set in scripts/.env, not application env: POSTGRES_HOST_PORT",
    ]


def test_env_config_check_allows_script_keys_in_scripts_env(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    env_file = scripts_dir / ".env"
    env_file.write_text("API_PORT=8100\nPOSTGRES_HOST_PORT=25432\n", encoding="utf-8")

    issues = check_file(env_file)

    assert issues == []


def test_env_config_check_allows_script_env_variants(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    env_file = scripts_dir / ".env.dev"
    env_file.write_text("API_PORT=8100\nWORKER_CONCURRENCY=4\n", encoding="utf-8")

    issues = check_file(env_file)

    assert issues == []


def test_env_config_check_rejects_application_keys_in_script_env_variants(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    env_file = scripts_dir / ".env.test"
    env_file.write_text("DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/db\n", encoding="utf-8")

    issues = check_file(env_file)

    assert issues == [
        f"{env_file}:1: application key must be set in application env, not scripts/.env: DATABASE_URL"
    ]
