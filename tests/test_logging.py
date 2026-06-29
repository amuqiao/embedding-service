import io
import logging

import pytest

from app.core import logging as logging_module
from app.core.logging import LogEvent, configure_logging, log_event, set_request_id


@pytest.fixture
def isolated_root_logger():
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    try:
        yield root
    finally:
        root.handlers.clear()
        root.handlers.extend(old_handlers)
        root.setLevel(old_level)
        set_request_id("-")


def test_configure_logging_writes_application_logs_to_stdout(monkeypatch, isolated_root_logger):
    stream = io.StringIO()
    monkeypatch.setattr(logging_module.sys, "stdout", stream)

    configure_logging()

    assert len(isolated_root_logger.handlers) == 1
    handler = isolated_root_logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert not isinstance(handler, logging.FileHandler)
    assert handler.stream is stream


def test_log_event_uses_request_id_and_key_value_fields(monkeypatch, isolated_root_logger):
    stream = io.StringIO()
    monkeypatch.setattr(logging_module.sys, "stdout", stream)
    configure_logging()
    set_request_id("req-logging-1")

    logger = logging.getLogger("tests.logging")
    log_event(
        logger,
        logging.INFO,
        LogEvent.JOB_CREATED,
        job_id="job-1",
        ignored_none=None,
        multiline="first\nsecond",
    )

    output = stream.getvalue()
    assert "level=INFO" in output
    assert "logger=tests.logging" in output
    assert "request_id=req-logging-1" in output
    assert "event=job_created" in output
    assert "job_id=job-1" in output
    assert "multiline=first\\nsecond" in output
    assert "ignored_none" not in output


def test_log_event_rejects_unknown_event():
    logger = logging.getLogger("tests.logging")

    with pytest.raises(ValueError, match="unknown log event"):
        log_event(logger, logging.INFO, "not_registered")


@pytest.mark.asyncio
async def test_taskiq_worker_startup_configures_stdout_logging(monkeypatch, isolated_root_logger):
    from app.tasks import taskiq_app

    stream = io.StringIO()
    monkeypatch.setattr(logging_module.sys, "stdout", stream)
    monkeypatch.setattr(taskiq_app, "init_db_engine", lambda: None)

    await taskiq_app._worker_startup(None)

    assert len(isolated_root_logger.handlers) == 1
    handler = isolated_root_logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert not isinstance(handler, logging.FileHandler)
    assert handler.stream is stream


def test_recovery_loop_main_configures_stdout_logging_before_loop(monkeypatch, isolated_root_logger):
    from app.tasks import recovery_loop

    stream = io.StringIO()
    monkeypatch.setattr(logging_module.sys, "stdout", stream)
    monkeypatch.setattr(recovery_loop, "run_recovery", lambda: (_ for _ in ()).throw(KeyboardInterrupt))

    with pytest.raises(KeyboardInterrupt):
        recovery_loop.main()

    assert len(isolated_root_logger.handlers) == 1
    handler = isolated_root_logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert not isinstance(handler, logging.FileHandler)
    assert handler.stream is stream
