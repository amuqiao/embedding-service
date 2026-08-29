from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.job import CallbackOutbox, Job, JobEvent
from smoke.harness import env_runtime
from smoke.harness.errors import FlowError


FAULT_INJECTION_EVENT_TYPE = "smoke.reconciler.callback_gap_injected"
LOCAL_FAULT_INJECTION_APP_ENVS = {"local", "dev"}


@dataclass(frozen=True)
class CallbackGapInjection:
    job_id: str
    job_status: str
    callback_event_type: str
    callback_url: str
    injected_at: str


def terminal_callback_event_type(job_status: str) -> str:
    if job_status == "succeeded":
        return "job.succeeded"
    if job_status == "failed":
        return "job.failed"
    raise FlowError(f"reconciler callback gap requires terminal job status, got {job_status}", exit_code=1)


def assert_local_fault_injection_app_env(app_env: str) -> None:
    if app_env not in LOCAL_FAULT_INJECTION_APP_ENVS:
        allowed = ", ".join(sorted(LOCAL_FAULT_INJECTION_APP_ENVS))
        raise FlowError(
            f"reconciler fault injection is only allowed for APP_ENV in [{allowed}], got {app_env}",
            exit_code=2,
        )


def database_url_from_app_env(app_env: dict[str, str]) -> str:
    value = env_runtime.env_value("DATABASE_URL", app_env)
    if not value:
        raise FlowError("DATABASE_URL is required for reconciler fault injection", exit_code=2)
    return value


def database_ssl_from_app_env(app_env: dict[str, str]) -> bool:
    return env_runtime.bool_enabled(env_runtime.env_value("DB_SSL", app_env))


def _make_session(*, database_url: str, database_ssl: bool):
    connect_args = {} if database_ssl else {"ssl": False}
    engine = create_async_engine(database_url, poolclass=NullPool, connect_args=connect_args)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _with_db(coro, *, database_url: str, database_ssl: bool):
    engine, session_factory = _make_session(database_url=database_url, database_ssl=database_ssl)
    try:
        async with session_factory() as db:
            return await coro(db)
    finally:
        await engine.dispose()


async def _inject_missing_callback_outbox(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    callback_url: str,
    callback_events: list[str],
) -> CallbackGapInjection:
    result = await db.execute(
        select(Job)
        .where(
            Job.id == job_id,
            Job.root_job_id.is_(None),
            Job.workflow_node_key.is_(None),
            Job.deleted_at.is_(None),
        )
        .with_for_update(skip_locked=True)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise FlowError(f"job {job_id} was not found for reconciler fault injection", exit_code=1)
    if job.status not in {"succeeded", "failed"}:
        raise FlowError(
            f"job {job_id} must be terminal before reconciler fault injection, got {job.status}",
            exit_code=1,
        )
    if job.active_attempt_id is not None:
        raise FlowError(f"terminal job {job_id} still has active_attempt_id={job.active_attempt_id}", exit_code=1)
    if job.callback_url is not None:
        raise FlowError(f"job {job_id} already has callback_url; refusing to overwrite callback contract", exit_code=1)

    event_type = terminal_callback_event_type(job.status)
    existing_result = await db.execute(
        select(CallbackOutbox.id)
        .where(CallbackOutbox.job_id == job.id, CallbackOutbox.event_type == event_type)
        .limit(1)
    )
    if existing_result.scalar_one_or_none() is not None:
        raise FlowError(f"job {job_id} already has {event_type} callback_outbox; no gap to reconcile", exit_code=1)

    now = datetime.now(timezone.utc)
    job.callback_url = callback_url
    job.callback_events = callback_events
    job.updated_at = now
    db.add(
        JobEvent(
            job_id=job.id,
            event_type=FAULT_INJECTION_EVENT_TYPE,
            from_status=job.status,
            to_status=job.status,
            payload={
                "fault": "terminal_callback_outbox_missing",
                "callback_event_type": event_type,
                "callback_url": callback_url,
                "callback_events": callback_events,
            },
        )
    )
    await db.flush()
    await db.commit()
    return CallbackGapInjection(
        job_id=str(job.id),
        job_status=job.status,
        callback_event_type=event_type,
        callback_url=callback_url,
        injected_at=now.isoformat(),
    )


def inject_missing_callback_outbox(
    *,
    database_url: str,
    database_ssl: bool,
    job_id: str,
    callback_url: str,
    callback_events: list[str],
) -> CallbackGapInjection:
    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError as exc:
        raise FlowError(f"job_id must be a UUID for reconciler fault injection: {job_id}", exit_code=2) from exc
    return asyncio.run(
        _with_db(
            lambda db: _inject_missing_callback_outbox(
                db,
                job_id=parsed_job_id,
                callback_url=callback_url,
                callback_events=callback_events,
            ),
            database_url=database_url,
            database_ssl=database_ssl,
        )
    )


async def _callback_outbox_evidence(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    callback_event_type: str,
) -> dict[str, Any] | None:
    result = await db.execute(
        select(CallbackOutbox)
        .where(CallbackOutbox.job_id == job_id, CallbackOutbox.event_type == callback_event_type)
        .order_by(CallbackOutbox.created_at.desc())
        .limit(1)
    )
    outbox = result.scalar_one_or_none()
    if outbox is None:
        return None
    return {
        "callback_outbox_id": str(outbox.id),
        "event_id": str(outbox.event_id),
        "event_type": outbox.event_type,
        "status": outbox.status,
        "delivery_attempts": outbox.delivery_attempts,
        "created_at": outbox.created_at.isoformat() if outbox.created_at else None,
        "updated_at": outbox.updated_at.isoformat() if outbox.updated_at else None,
        "delivered_at": outbox.delivered_at.isoformat() if outbox.delivered_at else None,
        "dead_lettered_at": outbox.dead_lettered_at.isoformat() if outbox.dead_lettered_at else None,
        "last_http_status": outbox.last_http_status,
        "last_error": outbox.last_error,
    }


def callback_outbox_evidence(
    *,
    database_url: str,
    database_ssl: bool,
    job_id: str,
    callback_event_type: str,
) -> dict[str, Any] | None:
    return asyncio.run(
        _with_db(
            lambda db: _callback_outbox_evidence(
                db,
                job_id=uuid.UUID(job_id),
                callback_event_type=callback_event_type,
            ),
            database_url=database_url,
            database_ssl=database_ssl,
        )
    )


def wait_for_callback_outbox(
    *,
    database_url: str,
    database_ssl: bool,
    job_id: str,
    callback_event_type: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = callback_outbox_evidence(
            database_url=database_url,
            database_ssl=database_ssl,
            job_id=job_id,
            callback_event_type=callback_event_type,
        )
        if last is not None:
            return last
        time.sleep(poll_interval_seconds)
    raise FlowError(
        f"reconciler did not create callback_outbox within {timeout_seconds}s; last={last}",
        exit_code=5,
    )
