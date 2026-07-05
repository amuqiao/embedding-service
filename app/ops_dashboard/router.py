from __future__ import annotations

import asyncio
import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import bearer_scheme, require_service_auth
from app.ops_dashboard import read_model
from app.ops_dashboard.config import get_dashboard_config
from app.ops_dashboard.registry import data_source_config, section_config
from app.ops_dashboard.schemas import (
    DashboardFilters,
    VALID_WINDOWS,
    is_valid_run_id,
)

VALID_RECENT_JOB_STATUSES = {"all", "queued", "running", "succeeded", "failed"}

router = APIRouter(tags=["ops-dashboard"], include_in_schema=False)
STATIC_DIR = Path(__file__).resolve().parent / "static"


async def require_ops_access(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    caller_id: str | None = Header(default=None, alias="X-AI-Service-Caller-ID"),
) -> str:
    if not settings.ops_dashboard.require_auth:
        return "ops-dashboard"
    return await require_service_auth(credentials=credentials, caller_id=caller_id)


OpsAccess = Annotated[str, Depends(require_ops_access)]


def _filters(
    window: str | None = Query(default=None),
    bucket: str | None = Query(default=None),
    from_at: str | None = Query(default=None, alias="from"),
    to_at: str | None = Query(default=None, alias="to"),
    caller_id: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
) -> DashboardFilters:
    config = get_dashboard_config()
    if bucket is not None:
        raise HTTPException(status_code=400, detail="bucket is server-derived; use window only")
    if from_at is not None or to_at is not None:
        raise HTTPException(status_code=400, detail="from/to are not supported; use window")
    effective_window = (
        str(_window_config(config.max_window_seconds)["default_window"])
        if window is None
        else window
    )
    if effective_window not in VALID_WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of: {', '.join(VALID_WINDOWS)}")
    if run_id is not None and not is_valid_run_id(run_id):
        raise HTTPException(status_code=400, detail="run_id must match [A-Za-z0-9][A-Za-z0-9_-]{0,127}")
    if VALID_WINDOWS[effective_window] > config.max_window_seconds:
        raise HTTPException(status_code=400, detail="window exceeds OPS_DASHBOARD_MAX_WINDOW_SECONDS")
    try:
        filters = DashboardFilters(
            window=effective_window,
            caller_id=caller_id,
            job_type=job_type,
            run_id=run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if filters.range_seconds > config.max_window_seconds:
        raise HTTPException(status_code=400, detail="time range exceeds OPS_DASHBOARD_MAX_WINDOW_SECONDS")
    return filters


def _planned_section_payload(
    *,
    section: str,
    title: str,
    filters: DashboardFilters,
    next_checks: list[str],
    controls: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "generated_at": datetime.now(UTC),
        "section": section,
        "title": title,
        "filters": filters.as_payload(),
        "controls": controls or {},
        "health": {
            "status": "neutral",
            "reasons": ["planned"],
            "next_checks": next_checks,
        },
    }


def _window_config(max_window_seconds: int) -> dict[str, object]:
    windows = [key for key, seconds in VALID_WINDOWS.items() if seconds <= max_window_seconds]
    default_window = "1h" if "1h" in windows else windows[-1]
    return {
        "windows": windows,
        "default_window": default_window,
    }


async def _with_timeout(coro):
    try:
        return await asyncio.wait_for(coro, timeout=get_dashboard_config().query_timeout_seconds)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="ops dashboard query timed out") from exc


async def get_dashboard_db():
    async for session in get_db():
        yield session


def _static_file(relative_path: str) -> FileResponse:
    requested = (STATIC_DIR / relative_path).resolve()
    try:
        requested.relative_to(STATIC_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="static file not found") from exc
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="static file not found")
    media_type = mimetypes.guess_type(str(requested))[0]
    return FileResponse(requested, media_type=media_type)


@router.get("/internal/jobs-dashboard")
async def dashboard_page(_: OpsAccess):
    return _static_file("index.html")


@router.get("/internal/jobs-dashboard/examples")
async def dashboard_examples_page(_: OpsAccess):
    return _static_file("examples.html")


@router.get("/internal/jobs-dashboard/static/{path:path}")
async def dashboard_static(path: str, _: OpsAccess):
    return _static_file(path)


@router.get("/internal/jobs-dashboard/config")
async def dashboard_config(_: OpsAccess):
    config = get_dashboard_config()
    return {
        "enabled": config.enabled,
        "require_auth": config.require_auth,
        "refresh_seconds": config.refresh_seconds,
        "max_window_seconds": config.max_window_seconds,
        "query_timeout_seconds": config.query_timeout_seconds,
        "route_base": config.route_base,
        "data_sources": data_source_config(),
        "sections": section_config(),
        "filters": _window_config(config.max_window_seconds),
    }


@router.get("/internal/jobs-dashboard/sections/overview/data")
async def overview_section(
    _: OpsAccess,
    filters: DashboardFilters = Depends(_filters),
    db: AsyncSession = Depends(get_dashboard_db),
):
    payload = await _with_timeout(
        read_model.overview_data(db, filters, max_active_jobs=settings.job.max_active_jobs)
    )
    return jsonable_encoder(payload)


@router.get("/internal/jobs-dashboard/sections/recent_jobs/data")
async def recent_jobs_section(
    _: OpsAccess,
    filters: DashboardFilters = Depends(_filters),
    db: AsyncSession = Depends(get_dashboard_db),
    status: str = Query(default="all"),
    job_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    if status not in VALID_RECENT_JOB_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {', '.join(sorted(VALID_RECENT_JOB_STATUSES))}",
        )
    payload = await _with_timeout(
        read_model.recent_jobs_data(
            db,
            filters,
            status=status,
            job_id=str(job_id) if job_id is not None else None,
            limit=limit,
        )
    )
    return jsonable_encoder(payload)


@router.get("/internal/jobs-dashboard/sections/flow_capacity/data")
async def flow_capacity_section(
    _: OpsAccess,
    filters: DashboardFilters = Depends(_filters),
    db: AsyncSession = Depends(get_dashboard_db),
):
    payload = await _with_timeout(
        read_model.flow_capacity_data(
            db,
            filters,
            max_active_jobs=settings.job.max_active_jobs,
        )
    )
    return jsonable_encoder(payload)


@router.get("/internal/jobs-dashboard/sections/failures_callbacks/data")
async def failures_callbacks_section(
    _: OpsAccess,
    filters: DashboardFilters = Depends(_filters),
    db: AsyncSession = Depends(get_dashboard_db),
):
    payload = await _with_timeout(read_model.failures_data(db, filters))
    return jsonable_encoder(payload)


@router.get("/internal/jobs-dashboard/jobs/{job_id}/data")
async def job_trace_data(
    job_id: uuid.UUID,
    _: OpsAccess,
    db: AsyncSession = Depends(get_dashboard_db),
    limit: int = Query(default=100, ge=1, le=200),
):
    payload = await _with_timeout(read_model.job_trace_data(db, job_id, limit=limit))
    if payload is None:
        raise HTTPException(status_code=404, detail="job not found")
    return jsonable_encoder(payload)


@router.get("/internal/jobs-dashboard/health")
async def job_health(
    _: OpsAccess,
    filters: DashboardFilters = Depends(_filters),
    db: AsyncSession = Depends(get_dashboard_db),
):
    payload = await _with_timeout(
        read_model.overview_data(db, filters, max_active_jobs=settings.job.max_active_jobs)
    )
    return jsonable_encoder(
        {
            "generated_at": payload["generated_at"],
            "health": payload["health"],
            "summary": payload["summary"],
        }
    )
