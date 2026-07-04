from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


VALID_WINDOWS: dict[str, int] = {
    "10m": 600,
    "30m": 1_800,
    "1h": 3_600,
    "3h": 10_800,
    "6h": 21_600,
    "12h": 43_200,
    "1d": 86_400,
    "3d": 259_200,
    "7d": 604_800,
}
AUTO_BUCKET_BY_WINDOW: dict[str, str] = {
    "10m": "1m",
    "30m": "1m",
    "1h": "1m",
    "3h": "5m",
    "6h": "5m",
    "12h": "15m",
    "1d": "30m",
    "3d": "2h",
    "7d": "6h",
}
BUCKET_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "2h": 7_200,
    "6h": 21_600,
}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def is_valid_run_id(value: str) -> bool:
    return bool(RUN_ID_RE.fullmatch(value))


@dataclass(frozen=True)
class DashboardFilters:
    window: str = "1h"
    caller_id: str | None = None
    job_type: str | None = None
    run_id: str | None = None
    sample_limit: int = 20
    reference_at: datetime = field(default_factory=lambda: datetime.now(UTC), repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.window not in VALID_WINDOWS:
            raise ValueError(f"window must be one of: {', '.join(VALID_WINDOWS)}")
        reference_at = _require_aware_utc(self.reference_at, "reference_at")
        object.__setattr__(self, "reference_at", reference_at)

    @property
    def time_mode(self) -> str:
        return "relative"

    @property
    def range_start_at(self) -> datetime:
        return self.reference_at - self.window_delta

    @property
    def range_end_at(self) -> datetime:
        return self.reference_at

    @property
    def window_delta(self) -> timedelta:
        return timedelta(seconds=VALID_WINDOWS[self.window])

    @property
    def range_seconds(self) -> int:
        return VALID_WINDOWS[self.window]

    @property
    def resolved_bucket(self) -> str:
        return AUTO_BUCKET_BY_WINDOW[self.window]

    @property
    def bucket_seconds(self) -> int:
        return BUCKET_SECONDS[self.resolved_bucket]

    def as_payload(self) -> dict[str, object]:
        return {
            "time_mode": self.time_mode,
            "window": self.window,
            "range_seconds": self.range_seconds,
            "resolved_bucket": self.resolved_bucket,
            "bucket_seconds": self.bucket_seconds,
            "caller_id": self.caller_id,
            "job_type": self.job_type,
            "run_id": self.run_id,
            "sample_limit": self.sample_limit,
        }


def _require_aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone")
    return value.astimezone(UTC)
