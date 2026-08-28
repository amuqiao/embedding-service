import asyncio
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


def test_reconciler_main_configures_stdout_logging_before_loop(monkeypatch, isolated_root_logger):
    from app.runtime import common, reconciler

    stream = io.StringIO()
    calls = []
    monkeypatch.setattr(logging_module.sys, "stdout", stream)
    monkeypatch.setattr(common, "ensure_worker_runtime_initialized", lambda: calls.append("bootstrap"))
    monkeypatch.setattr(common.sys, "argv", ["python -m app.runtime.reconciler", "loop"])

    async def stop_after_first_recovery():
        calls.append("recover")
        raise KeyboardInterrupt

    monkeypatch.setattr(reconciler, "run_recovery_once", stop_after_first_recovery)

    with pytest.raises(KeyboardInterrupt):
        reconciler.main()

    assert calls == ["bootstrap", "recover"]
    assert len(isolated_root_logger.handlers) == 1
    handler = isolated_root_logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert not isinstance(handler, logging.FileHandler)
    assert handler.stream is stream


def test_role_loop_uses_one_asyncio_run_for_process_lifetime(monkeypatch, isolated_root_logger):
    from app.runtime import common

    calls = []
    run_calls = 0
    stream = io.StringIO()

    async def run_once():
        calls.append("once")
        return {"ok": True}

    def fake_asyncio_run(coro):
        nonlocal run_calls
        run_calls += 1
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(logging_module.sys, "stdout", stream)
    monkeypatch.setattr(common, "ensure_worker_runtime_initialized", lambda: calls.append("bootstrap"))
    monkeypatch.setattr(common.sys, "argv", ["python -m app.runtime.dispatcher", "loop", "--interval-seconds", "1"])
    monkeypatch.setattr(common.asyncio, "run", fake_asyncio_run)

    with pytest.raises(KeyboardInterrupt):
        common.run_role_cli(role="dispatcher", run_once=run_once, default_interval_seconds=1)

    assert calls == ["bootstrap"]
    assert run_calls == 1


@pytest.mark.asyncio
async def test_role_loop_runs_repeated_work_in_same_async_loop(monkeypatch, isolated_root_logger):
    from app.runtime import common

    calls = []
    sleeps = []
    configure_logging()

    async def run_once():
        calls.append(("once", id(asyncio.get_running_loop())))
        return {"count": len(calls)}

    async def fake_sleep(interval):
        sleeps.append(interval)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(common.asyncio, "sleep", fake_sleep)

    with pytest.raises(KeyboardInterrupt):
        await common._run_role_loop(role="dispatcher", run_once=run_once, interval_seconds=3)

    assert len(calls) == 2
    assert calls[0][1] == calls[1][1]
    assert sleeps == [3, 3]


@pytest.mark.asyncio
async def test_reconciler_once_uses_async_recovery_entrypoint(monkeypatch):
    from app.runtime import reconciler

    calls = []

    async def run_recovery_once():
        calls.append("recover")
        return {"locked": True}

    monkeypatch.setattr(reconciler, "run_recovery_once", run_recovery_once)

    result = await reconciler.run_reconciler_once()

    assert result == {"locked": True}
    assert calls == ["recover"]
