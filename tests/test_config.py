import pytest
from pydantic import ValidationError

from app.core.config import (
    Settings,
    _CALLBACK_DELIVERY_WINDOW_BUFFER,
    _CELERY_HARD_TIMEOUT_BUFFER,
    _CELERY_SOFT_TIMEOUT_BUFFER,
    _JOB_STALE_RUNNING_BUFFER,
)


def _settings_kwargs(**overrides):
    values = {
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/novel_localize",
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


def test_derived_timeout_properties_use_fixed_buffers():
    # Buffer values are now code constants — passing them has no effect.
    s = Settings(**_settings_kwargs(MODEL_CALL_TIMEOUT_SECONDS=300))
    assert s.celery_soft_time_limit == 300 + _CELERY_SOFT_TIMEOUT_BUFFER
    assert s.celery_time_limit == 300 + _CELERY_SOFT_TIMEOUT_BUFFER + _CELERY_HARD_TIMEOUT_BUFFER
    assert s.job_stale_running_seconds == (
        300 + _CELERY_SOFT_TIMEOUT_BUFFER + _CELERY_HARD_TIMEOUT_BUFFER + _JOB_STALE_RUNNING_BUFFER
    )
    assert s.callback_delivery_timeout_seconds == 5 + _CALLBACK_DELIVERY_WINDOW_BUFFER


def test_settings_rejects_buffers_below_minimum():
    # These fields no longer exist in Settings — passing them is a no-op (extra="ignore").
    # Buffer minimums are enforced by code constants, so any MODEL_CALL_TIMEOUT_SECONDS
    # value produces a valid chain. This test documents the constant floor.
    s = Settings(**_settings_kwargs(MODEL_CALL_TIMEOUT_SECONDS=1))
    assert s.celery_soft_time_limit == 1 + _CELERY_SOFT_TIMEOUT_BUFFER
    assert _CELERY_SOFT_TIMEOUT_BUFFER >= 300
    assert _CELERY_HARD_TIMEOUT_BUFFER >= 60
    assert _JOB_STALE_RUNNING_BUFFER >= 600


def test_settings_rejects_callback_delivery_window_overlap():
    # Violate: CALLBACK_TIMEOUT_SECONDS(130) + buffer(175) = 305 >= RETRY_DELAY(300)
    with pytest.raises(ValidationError, match="delivery window must not overlap"):
        Settings(**_settings_kwargs(
            CALLBACK_TIMEOUT_SECONDS=130,
            CALLBACK_RETRY_DELAY_SECONDS=300,
        ))

    # Violate: CALLBACK_TIMEOUT_SECONDS(5) + buffer(175) = 180 >= RETRY_DELAY(180)
    with pytest.raises(ValidationError, match="delivery window must not overlap"):
        Settings(**_settings_kwargs(
            CALLBACK_TIMEOUT_SECONDS=5,
            CALLBACK_RETRY_DELAY_SECONDS=180,
        ))


def test_settings_rejects_negative_or_zero_control_values():
    with pytest.raises(ValidationError, match="JOB_RECOVERY_INTERVAL_SECONDS"):
        Settings(**_settings_kwargs(JOB_RECOVERY_INTERVAL_SECONDS=0))

    with pytest.raises(ValidationError, match="MAX_ACTIVE_JOBS"):
        Settings(**_settings_kwargs(MAX_ACTIVE_JOBS=-1))
