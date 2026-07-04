from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.ops_dashboard.schemas import DashboardFilters


SectionCollector = Callable[[AsyncSession, DashboardFilters], object]


@dataclass(frozen=True)
class DashboardDataSource:
    key: str
    title: str
    route: str
    refresh_seconds: int
    default_enabled: bool = True


DASHBOARD_DATA_SOURCES: tuple[DashboardDataSource, ...] = (
    DashboardDataSource(
        key="overview",
        title="总览",
        route="/internal/jobs-dashboard/sections/overview/data",
        refresh_seconds=15,
    ),
    DashboardDataSource(
        key="failures",
        title="失败",
        route="/internal/jobs-dashboard/sections/failures/data",
        refresh_seconds=30,
    ),
    DashboardDataSource(
        key="job_trace",
        title="Job 追踪",
        route="/internal/jobs-dashboard/jobs/{job_id}/data",
        refresh_seconds=0,
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
        }
        for data_source in DASHBOARD_DATA_SOURCES
    ]


def section_config() -> list[dict[str, object]]:
    return data_source_config()
