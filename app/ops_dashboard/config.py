from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class DashboardRuntimeConfig:
    enabled: bool
    require_auth: bool
    refresh_seconds: int
    max_window_seconds: int
    query_timeout_seconds: int
    route_base: str = "/internal/jobs-dashboard"


def get_dashboard_config() -> DashboardRuntimeConfig:
    return DashboardRuntimeConfig(
        enabled=settings.ops_dashboard.enabled,
        require_auth=settings.ops_dashboard.require_auth,
        refresh_seconds=settings.ops_dashboard.refresh_seconds,
        max_window_seconds=settings.ops_dashboard.max_window_seconds,
        query_timeout_seconds=settings.ops_dashboard.query_timeout_seconds,
    )
