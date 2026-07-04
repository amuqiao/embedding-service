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
from app.ops_dashboard.schemas import DashboardFilters, VALID_BUCKETS, VALID_WINDOWS

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
    window: str = Query(default="1h"),
    bucket: str = Query(default="1m"),
    caller_id: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
) -> DashboardFilters:
    if window not in VALID_WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of: {', '.join(VALID_WINDOWS)}")
    if bucket not in VALID_BUCKETS:
        raise HTTPException(status_code=400, detail=f"bucket must be one of: {', '.join(VALID_BUCKETS)}")
    config = get_dashboard_config()
    if VALID_WINDOWS[window] > config.max_window_seconds:
        raise HTTPException(status_code=400, detail="window exceeds OPS_DASHBOARD_MAX_WINDOW_SECONDS")
    return DashboardFilters(window=window, bucket=bucket, caller_id=caller_id, job_type=job_type)


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
        "filters": filters.__dict__,
        "controls": controls or {},
        "health": {
            "status": "neutral",
            "reasons": ["planned"],
            "next_checks": next_checks,
        },
    }


async def _with_timeout(coro):
    return await asyncio.wait_for(coro, timeout=get_dashboard_config().query_timeout_seconds)


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
    db: AsyncSession = Depends(get_dashboard_db),
    filters: DashboardFilters = Depends(_filters),
):
    payload = await _with_timeout(
        read_model.overview_data(db, filters, max_active_jobs=settings.job.max_active_jobs)
    )
    return jsonable_encoder(payload)


@router.get("/internal/jobs-dashboard/sections/recent_jobs/data")
async def recent_jobs_section(
    _: OpsAccess,
    filters: DashboardFilters = Depends(_filters),
    status: str = Query(default="all"),
    client_request_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    if status not in VALID_RECENT_JOB_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {', '.join(sorted(VALID_RECENT_JOB_STATUSES))}",
        )
    return jsonable_encoder(
        _planned_section_payload(
            section="recent_jobs",
            title="最近任务",
            filters=filters,
            controls={"status": status, "client_request_id": client_request_id, "limit": limit},
            next_checks=[
                "Phase 1 will connect public root Job rows.",
                "./scripts/jobs.sh list --status succeeded,failed --json",
            ],
        )
    )


@router.get("/internal/jobs-dashboard/sections/flow_capacity/data")
async def flow_capacity_section(
    _: OpsAccess,
    filters: DashboardFilters = Depends(_filters),
):
    return jsonable_encoder(
        _planned_section_payload(
            section="flow_capacity",
            title="吞吐与容量",
            filters=filters,
            next_checks=[
                "Phase 2 will connect ingress, drain, gate/headroom and latency signals.",
                "./scripts/jobs.sh dashboard --since 1h",
            ],
        )
    )


@router.get("/internal/jobs-dashboard/sections/failures_callbacks/data")
async def failures_callbacks_section(
    _: OpsAccess,
    db: AsyncSession = Depends(get_dashboard_db),
    filters: DashboardFilters = Depends(_filters),
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
    db: AsyncSession = Depends(get_dashboard_db),
    filters: DashboardFilters = Depends(_filters),
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
