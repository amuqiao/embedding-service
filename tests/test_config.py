import logging

import pytest
from pydantic import ValidationError

from app.infrastructure.config import Settings


def _settings_kwargs(**overrides):
    values = {
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/novel_localize",
        "SERVICE_API_KEY": "test-token",
    }
    values.update(overrides)
    return values


def test_settings_rejects_zero_or_negative_buffer():
    with pytest.raises(ValidationError, match="CELERY_SOFT_TIMEOUT_BUFFER_SECONDS"):
        Settings(**_settings_kwargs(CELERY_SOFT_TIMEOUT_BUFFER_SECONDS=0))

    with pytest.raises(ValidationError, match="CELERY_HARD_TIMEOUT_BUFFER_SECONDS"):
        Settings(**_settings_kwargs(CELERY_HARD_TIMEOUT_BUFFER_SECONDS=-1))

    with pytest.raises(ValidationError, match="JOB_STALE_RUNNING_BUFFER_SECONDS"):
        Settings(**_settings_kwargs(JOB_STALE_RUNNING_BUFFER_SECONDS=0))

    with pytest.raises(ValidationError, match="CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS"):
        Settings(**_settings_kwargs(CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS=0))


def test_derived_timeout_properties_compute_correctly():
    s = Settings(**_settings_kwargs(
        MODEL_CALL_TIMEOUT_SECONDS=300,
        CELERY_SOFT_TIMEOUT_BUFFER_SECONDS=400,
        CELERY_HARD_TIMEOUT_BUFFER_SECONDS=80,
        JOB_STALE_RUNNING_BUFFER_SECONDS=700,
        CALLBACK_TIMEOUT_SECONDS=5,
        CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS=175,
    ))
    assert s.celery_soft_time_limit == 700
    assert s.celery_time_limit == 780
    assert s.job_stale_running_seconds == 1480
    assert s.callback_delivery_timeout_seconds == 180


def test_settings_warns_when_buffers_below_recommended(caplog):
    with caplog.at_level(logging.WARNING, logger="app.infrastructure.config"):
        Settings(**_settings_kwargs(
            CELERY_SOFT_TIMEOUT_BUFFER_SECONDS=1,
            CELERY_HARD_TIMEOUT_BUFFER_SECONDS=1,
            JOB_STALE_RUNNING_BUFFER_SECONDS=1,
        ))
    assert "CELERY_SOFT_TIMEOUT_BUFFER_SECONDS" in caplog.text
    assert "CELERY_HARD_TIMEOUT_BUFFER_SECONDS" in caplog.text
    assert "JOB_STALE_RUNNING_BUFFER_SECONDS" in caplog.text


def test_settings_rejects_negative_or_zero_control_values():
    with pytest.raises(ValidationError, match="JOB_RECOVERY_INTERVAL_SECONDS"):
        Settings(**_settings_kwargs(JOB_RECOVERY_INTERVAL_SECONDS=0))

    with pytest.raises(ValidationError, match="MAX_ACTIVE_JOBS"):
        Settings(**_settings_kwargs(MAX_ACTIVE_JOBS=-1))
