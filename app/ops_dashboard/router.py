from __future__ import annotations

import asyncio
import mimetypes
import uuid
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
from app.ops_dashboard.config import get_dashboard_config
from app.ops_dashboard.registry import section_config
from app.ops_dashboard.schemas import DashboardFilters, VALID_BUCKETS, VALID_WINDOWS
from app.ops_dashboard import mock_data, read_model

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
    window: str = Query(default="1h"),
    bucket: str = Query(default="1m"),
    caller_id: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> DashboardFilters:
    if window not in VALID_WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of: {', '.join(VALID_WINDOWS)}")
    if bucket not in VALID_BUCKETS:
        raise HTTPException(status_code=400, detail=f"bucket must be one of: {', '.join(VALID_BUCKETS)}")
    config = get_dashboard_config()
    if VALID_WINDOWS[window] > config.max_window_seconds:
        raise HTTPException(status_code=400, detail="window exceeds OPS_DASHBOARD_MAX_WINDOW_SECONDS")
    return DashboardFilters(window=window, bucket=bucket, caller_id=caller_id, job_type=job_type, limit=limit)


async def _with_timeout(coro):
    return await asyncio.wait_for(coro, timeout=get_dashboard_config().query_timeout_seconds)


async def get_dashboard_db():
    if settings.ops_dashboard.mock_data_enabled:
        yield None
        return
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
        "mock_data_enabled": config.mock_data_enabled,
        "data_source": "mock" if config.mock_data_enabled else "live",
        "route_base": config.route_base,
        "sections": section_config(),
        "filters": {
            "windows": list(VALID_WINDOWS),
            "buckets": list(VALID_BUCKETS),
            "default_window": "1h",
            "default_bucket": "1m",
        },
    }


@router.get("/internal/jobs-dashboard/sections/overview/data")
async def overview_section(
    _: OpsAccess,
    db: AsyncSession | None = Depends(get_dashboard_db),
    filters: DashboardFilters = Depends(_filters),
):
    if settings.ops_dashboard.mock_data_enabled:
        return jsonable_encoder(
            mock_data.overview_data(filters, max_active_jobs=settings.job.max_active_jobs)
        )
    assert db is not None
    payload = await _with_timeout(
        read_model.overview_data(db, filters, max_active_jobs=settings.job.max_active_jobs)
    )
    return jsonable_encoder(payload)


@router.get("/internal/jobs-dashboard/sections/failures/data")
async def failures_section(
    _: OpsAccess,
    db: AsyncSession | None = Depends(get_dashboard_db),
    filters: DashboardFilters = Depends(_filters),
):
    if settings.ops_dashboard.mock_data_enabled:
        return jsonable_encoder(mock_data.failures_data(filters))
    assert db is not None
    payload = await _with_timeout(read_model.failures_data(db, filters))
    return jsonable_encoder(payload)


@router.get("/internal/jobs-dashboard/jobs/{job_id}/data")
async def job_trace_data(
    job_id: uuid.UUID,
    _: OpsAccess,
    db: AsyncSession | None = Depends(get_dashboard_db),
    limit: int = Query(default=100, ge=1, le=200),
):
    if settings.ops_dashboard.mock_data_enabled:
        return jsonable_encoder(mock_data.job_trace_data(job_id, limit=limit))
    assert db is not None
    payload = await _with_timeout(read_model.job_trace_data(db, job_id, limit=limit))
    if payload is None:
        raise HTTPException(status_code=404, detail="job not found")
    return jsonable_encoder(payload)


@router.get("/internal/jobs-dashboard/health")
async def job_health(
    _: OpsAccess,
    db: AsyncSession | None = Depends(get_dashboard_db),
    filters: DashboardFilters = Depends(_filters),
):
    if settings.ops_dashboard.mock_data_enabled:
        payload = mock_data.overview_data(filters, max_active_jobs=settings.job.max_active_jobs)
        return jsonable_encoder(
            {
                "mock_data": True,
                "generated_at": payload["generated_at"],
                "health": payload["health"],
                "summary": payload["summary"],
            }
        )
    assert db is not None
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
