from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.ops_dashboard.schemas import DashboardFilters


SectionCollector = Callable[[AsyncSession, DashboardFilters], object]


@dataclass(frozen=True)
class DashboardPageControl:
    key: str
    control_type: str
    binding: str
    param: str
    label: str
    default: str | int | None = None
    options: tuple[str, ...] = ()
    min_value: int | None = None
    max_value: int | None = None


@dataclass(frozen=True)
class DashboardDataSource:
    key: str
    title: str
    route: str
    refresh_seconds: int
    default_enabled: bool = True
    controls: tuple[DashboardPageControl, ...] = ()


RECENT_JOBS_CONTROLS: tuple[DashboardPageControl, ...] = (
    DashboardPageControl(
        key="status",
        control_type="select",
        binding="query",
        param="status",
        label="状态",
        default="all",
        options=("all", "queued", "running", "succeeded", "failed"),
    ),
    DashboardPageControl(
        key="client_request_id",
        control_type="text",
        binding="query",
        param="client_request_id",
        label="client_request_id",
    ),
    DashboardPageControl(
        key="limit",
        control_type="number",
        binding="query",
        param="limit",
        label="数量",
        default=20,
        min_value=1,
        max_value=100,
    ),
)


JOB_TRACE_CONTROLS: tuple[DashboardPageControl, ...] = (
    DashboardPageControl(
        key="job_id",
        control_type="text",
        binding="route",
        param="job_id",
        label="job_id",
    ),
    DashboardPageControl(
        key="limit",
        control_type="number",
        binding="query",
        param="limit",
        label="数量",
        default=100,
        min_value=1,
        max_value=200,
    ),
)


DASHBOARD_DATA_SOURCES: tuple[DashboardDataSource, ...] = (
    DashboardDataSource(
        key="overview",
        title="总览",
        route="/internal/jobs-dashboard/sections/overview/data",
        refresh_seconds=15,
    ),
    DashboardDataSource(
        key="recent_jobs",
        title="最近任务",
        route="/internal/jobs-dashboard/sections/recent_jobs/data",
        refresh_seconds=15,
        controls=RECENT_JOBS_CONTROLS,
    ),
    DashboardDataSource(
        key="flow_capacity",
        title="吞吐与容量",
        route="/internal/jobs-dashboard/sections/flow_capacity/data",
        refresh_seconds=30,
    ),
    DashboardDataSource(
        key="failures_callbacks",
        title="失败与 Callback",
        route="/internal/jobs-dashboard/sections/failures_callbacks/data",
        refresh_seconds=30,
    ),
    DashboardDataSource(
        key="job_trace",
        title="Job 追踪",
        route="/internal/jobs-dashboard/jobs/{job_id}/data",
        refresh_seconds=0,
        controls=JOB_TRACE_CONTROLS,
    ),
)


def data_source_config() -> list[dict[str, object]]:
    return [
        {
            "key": data_source.key,
            "title": data_source.title,
            "route": data_source.route,
            "refresh_seconds": data_source.refresh_seconds,
            "default_enabled": data_source.default_enabled,
            "controls": [
                {
                    "key": control.key,
                    "type": control.control_type,
                    "binding": control.binding,
                    "param": control.param,
                    "label": control.label,
                    "default": control.default,
                    "options": list(control.options),
                    "min": control.min_value,
                    "max": control.max_value,
                }
                for control in data_source.controls
            ],
        }
        for data_source in DASHBOARD_DATA_SOURCES
    ]


def section_config() -> list[dict[str, object]]:
    return data_source_config()
