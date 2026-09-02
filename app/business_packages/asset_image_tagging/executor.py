from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.business_packages.asset_image_tagging.errors import ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED
from app.business_packages.asset_image_tagging.model_adapter import (
    asset_image_tagging_model_adapter_from_settings,
    build_batch_summary,
)
from app.business_packages.asset_image_tagging.schemas import (
    AssetImageTaggingParams,
    AssetImageTaggingResult,
    AssetImageTaggingRuntimeFields,
)
from app.core.exceptions import AppError, ValidationAppError
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.services.job_runtime import job_params_from_job, runtime_fields_from_job


def _build_runtime_fields(params: AssetImageTaggingParams) -> AssetImageTaggingRuntimeFields:
    category_ids = list(dict.fromkeys(item.category_id for item in params.items))
    return AssetImageTaggingRuntimeFields(
        tagging_language=params.tagging_language,
        item_count=len(params.items),
        label_group_count=len(params.label_snapshot),
        category_ids=category_ids,
    )


@register_job_type
class AssetImageTaggingJob(JobExecutor):
    name = "asset_image_tagging"
    visibility = "public"
    role = "root"
    params_schema = AssetImageTaggingParams
    runtime_fields_schema_name = "AssetImageTaggingRuntimeFields"
    canonical_result_schema = AssetImageTaggingResult
    public_result_schema = AssetImageTaggingResult
    allow_callback = True
    timeout_seconds = 300
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset(
        {
            "INVALID_JOB_PARAMS",
            ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED,
        }
    )

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        try:
            params = AssetImageTaggingParams.model_validate(job_params)
        except ValidationError as exc:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "asset_image_tagging job_params does not match schema",
                {"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AssetImageTaggingParams.model_validate(job_params)
        return _build_runtime_fields(params).model_dump(by_alias=True, exclude_none=True)

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = AssetImageTaggingParams.model_validate(job_params_from_job(job))
        try:
            runtime_fields = AssetImageTaggingRuntimeFields.model_validate(runtime_fields_from_job(job))
        except ValidationError as exc:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_image_tagging runtime_fields does not match schema",
                details={"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        expected_runtime_fields = _build_runtime_fields(params)
        expected_runtime_payload = expected_runtime_fields.model_dump(by_alias=True, exclude_none=True)
        actual_runtime_payload = runtime_fields.model_dump(by_alias=True, exclude_none=True)
        actual_runtime_payload.pop("_system", None)
        if actual_runtime_payload != expected_runtime_payload:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_image_tagging runtime_fields does not match job_params",
                details={
                    "job_type": self.name,
                    "expected": expected_runtime_payload,
                    "actual": actual_runtime_payload,
                },
            )

        result_items = await asset_image_tagging_model_adapter_from_settings().tag(params)
        batch_summary = build_batch_summary(result_items)
        if batch_summary.failed == batch_summary.total:
            raise AppError(
                ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED,
                "all asset_image_tagging items failed",
                details={
                    "total": batch_summary.total,
                    "failed": batch_summary.failed,
                    "item_errors": [
                        {
                            "item_id": item.item_id,
                            "code": item.error.code if item.error is not None else None,
                            "details": item.error.details if item.error is not None else {},
                        }
                        for item in result_items
                    ],
                },
            )

        return AssetImageTaggingResult(
            tagging_language=params.tagging_language,
            batch_summary=batch_summary,
            items=result_items,
        ).model_dump(exclude_none=True)
