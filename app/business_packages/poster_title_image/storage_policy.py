from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PosterTitleImageStoragePolicy:
    output_namespace: str = "poster-title"
    allowed_input_buckets: tuple[str, ...] | None = None
    allowed_input_regions: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _validate_output_namespace(self.output_namespace)
        _validate_non_empty_values("allowed_input_buckets", self.allowed_input_buckets)
        _validate_non_empty_values("allowed_input_regions", self.allowed_input_regions)


def allowed_input_buckets(policy: PosterTitleImageStoragePolicy, settings: Any) -> tuple[str, ...]:
    if policy.allowed_input_buckets is not None:
        return policy.allowed_input_buckets
    bucket = settings.storage.oss_bucket if settings.storage.backend == "aliyun_oss" else settings.storage.oss_bucket or "local-dev"
    return (bucket,)


def allowed_input_regions(policy: PosterTitleImageStoragePolicy, settings: Any) -> tuple[str, ...]:
    if policy.allowed_input_regions is not None:
        return policy.allowed_input_regions
    region = settings.storage.oss_region if settings.storage.backend == "aliyun_oss" else settings.storage.oss_region or "local"
    return (region,)


def _validate_output_namespace(value: str) -> None:
    if value != value.strip("/").strip():
        raise ValueError("output_namespace must not have leading or trailing whitespace or slash")
    if not value:
        raise ValueError("output_namespace must not be empty")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("output_namespace must not contain empty, current, or parent path segments")


def _validate_non_empty_values(label: str, values: tuple[str, ...] | None) -> None:
    if values is None:
        return
    if not values:
        raise ValueError(f"{label} must not be empty when configured")
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{label} must contain non-empty trimmed values")


STORAGE_POLICY = PosterTitleImageStoragePolicy()
