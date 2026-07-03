from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.ops_dashboard.schemas import DashboardFilters


SectionCollector = Callable[[AsyncSession, DashboardFilters], object]


@dataclass(frozen=True)
class DashboardSection:
    key: str
    title: str
    route: str
    refresh_seconds: int
    default_enabled: bool = True


DASHBOARD_SECTIONS: tuple[DashboardSection, ...] = (
    DashboardSection(
        key="overview",
        title="总览",
        route="/internal/jobs-dashboard/sections/overview/data",
        refresh_seconds=15,
    ),
    DashboardSection(
        key="failures",
        title="失败",
        route="/internal/jobs-dashboard/sections/failures/data",
        refresh_seconds=30,
    ),
    DashboardSection(
        key="job_trace",
        title="Job 追踪",
        route="/internal/jobs-dashboard/jobs/{job_id}/data",
        refresh_seconds=0,
    ),
)


def section_config() -> list[dict[str, object]]:
    return [
        {
            "key": section.key,
            "title": section.title,
            "route": section.route,
            "refresh_seconds": section.refresh_seconds,
            "default_enabled": section.default_enabled,
        }
        for section in DASHBOARD_SECTIONS
    ]
