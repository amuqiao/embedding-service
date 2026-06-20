import pytest
from pydantic import ValidationError

from app.core.config import (
    Settings,
    _CALLBACK_DELIVERY_CLAIM_GRACE,
    _CELERY_HARD_TIMEOUT_BUFFER,
    _CELERY_SOFT_TIMEOUT_BUFFER,
    _JOB_STALE_RUNNING_BUFFER,
)
from scripts.verify.env_config_check import (
    DEPLOYMENT_OR_SCRIPT_KEYS,
    check_file,
    settings_keys_from_config,
)


def _settings_kwargs(**overrides):
    values = {
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/ai_jobs",
        "SERVICE_API_KEY": "test-token",
    }
    values.update(overrides)
    return values


def test_settings_rejects_zero_or_negative_control_values():
    with pytest.raises(ValidationError, match="MODEL_CALL_TIMEOUT_SECONDS"):
        Settings(**_settings_kwargs(MODEL_CALL_TIMEOUT_SECONDS=0))

    with pytest.raises(ValidationError, match="CALLBACK_TIMEOUT_SECONDS"):
        Settings(**_settings_kwargs(CALLBACK_TIMEOUT_SECONDS=-1))

    with pytest.raises(ValidationError, match="OSS_INPUT_MAX_BYTES"):
        Settings(**_settings_kwargs(OSS_INPUT_MAX_BYTES=0))

    with pytest.raises(ValidationError, match="JOB_MAX_EXECUTION_ATTEMPTS"):
        Settings(**_settings_kwargs(JOB_MAX_EXECUTION_ATTEMPTS=0))


def test_derived_timeout_properties_use_fixed_buffers():
    # Buffer values are now code constants — passing them has no effect.
    s = Settings(**_settings_kwargs(MODEL_CALL_TIMEOUT_SECONDS=300))
    assert s.celery_soft_time_limit == 300 + _CELERY_SOFT_TIMEOUT_BUFFER
    assert s.celery_time_limit == 300 + _CELERY_SOFT_TIMEOUT_BUFFER + _CELERY_HARD_TIMEOUT_BUFFER
    assert s.job_stale_running_seconds == (
        300 + _CELERY_SOFT_TIMEOUT_BUFFER + _CELERY_HARD_TIMEOUT_BUFFER + _JOB_STALE_RUNNING_BUFFER
    )
    assert s.callback_delivery_timeout_seconds == 5 + _CALLBACK_DELIVERY_CLAIM_GRACE


def test_settings_rejects_buffers_below_minimum():
    # These fields no longer exist in Settings — passing them is a no-op (extra="ignore").
    # Buffer minimums are enforced by code constants, so any MODEL_CALL_TIMEOUT_SECONDS
    # value produces a valid chain. This test documents the constant floor.
    s = Settings(**_settings_kwargs(MODEL_CALL_TIMEOUT_SECONDS=1))
    assert s.celery_soft_time_limit == 1 + _CELERY_SOFT_TIMEOUT_BUFFER
    assert _CELERY_SOFT_TIMEOUT_BUFFER >= 300
    assert _CELERY_HARD_TIMEOUT_BUFFER >= 60
    assert _JOB_STALE_RUNNING_BUFFER >= 600


def test_callback_delivery_window_is_anchored_to_http_timeout_not_retry_delay():
    fast_retry = Settings(**_settings_kwargs(
        CALLBACK_TIMEOUT_SECONDS=5,
        CALLBACK_RETRY_DELAY_SECONDS=300,
    ))
    slow_retry = Settings(**_settings_kwargs(
        CALLBACK_TIMEOUT_SECONDS=5,
        CALLBACK_RETRY_DELAY_SECONDS=600,
    ))

    assert fast_retry.callback_delivery_timeout_seconds == 5 + _CALLBACK_DELIVERY_CLAIM_GRACE
    assert slow_retry.callback_delivery_timeout_seconds == 5 + _CALLBACK_DELIVERY_CLAIM_GRACE


def test_settings_rejects_callback_retry_interval_overlapping_delivery_window():
    # Violate: timeout(5) + internal grace(175) = 180 >= retry_delay(180)
    with pytest.raises(ValidationError, match="retry interval must start after"):
        Settings(**_settings_kwargs(
            CALLBACK_TIMEOUT_SECONDS=5,
            CALLBACK_RETRY_DELAY_SECONDS=180,
        ))

    # Violate: timeout(130) + internal grace(175) = 305 >= retry_delay(300)
    with pytest.raises(ValidationError, match="retry interval must start after"):
        Settings(**_settings_kwargs(
            CALLBACK_TIMEOUT_SECONDS=130,
            CALLBACK_RETRY_DELAY_SECONDS=300,
        ))


def test_settings_rejects_negative_or_zero_control_values():
    with pytest.raises(ValidationError, match="JOB_RECOVERY_INTERVAL_SECONDS"):
        Settings(**_settings_kwargs(JOB_RECOVERY_INTERVAL_SECONDS=0))

    with pytest.raises(ValidationError, match="MAX_ACTIVE_JOBS"):
        Settings(**_settings_kwargs(MAX_ACTIVE_JOBS=-1))


def test_env_config_check_rejects_deprecated_rs_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SHORT_DRAMA_RS_SCHEMA_SOURCE=fixture\n", encoding="utf-8")

    issues = check_file(env_file, settings_keys_from_config() | DEPLOYMENT_OR_SCRIPT_KEYS)

    assert any("deprecated or unsupported config key: SHORT_DRAMA_RS_SCHEMA_SOURCE" in issue for issue in issues)
