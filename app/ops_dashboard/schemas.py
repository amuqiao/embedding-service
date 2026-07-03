from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


VALID_WINDOWS: dict[str, int] = {
    "10m": 600,
    "1h": 3_600,
    "24h": 86_400,
}
VALID_BUCKETS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
}


@dataclass(frozen=True)
class DashboardFilters:
    window: str = "1h"
    bucket: str = "1m"
    caller_id: str | None = None
    job_type: str | None = None
    limit: int = 20

    @property
    def window_delta(self) -> timedelta:
        return timedelta(seconds=VALID_WINDOWS[self.window])

    @property
    def bucket_seconds(self) -> int:
        return VALID_BUCKETS[self.bucket]
