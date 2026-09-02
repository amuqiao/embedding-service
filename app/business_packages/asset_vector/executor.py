from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.business_packages.asset_vector.repository import delete_items, upsert_items
from app.business_packages.asset_vector.schemas import (
    AssetVectorBatchDeleteParams,
    AssetVectorBatchDeleteResult,
    AssetVectorBatchDeleteRuntimeFields,
    AssetVectorBatchUpsertParams,
    AssetVectorBatchUpsertResult,
    AssetVectorBatchUpsertRuntimeFields,
    AssetVectorDeleteBatchSummary,
    AssetVectorDeleteResultItem,
    AssetVectorIndexedInfo,
    AssetVectorUpsertBatchSummary,
    AssetVectorUpsertResultItem,
)
from app.core.exceptions import AppError, ValidationAppError
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.services.job_runtime import job_params_from_job, runtime_fields_from_job


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@register_job_type
class AssetVectorBatchUpsertJob(JobExecutor):
    name = "asset_vector_batch_upsert"
    visibility = "public"
    role = "root"
    params_schema = AssetVectorBatchUpsertParams
    runtime_fields_schema_name = "AssetVectorBatchUpsertRuntimeFields"
    canonical_result_schema = AssetVectorBatchUpsertResult
    public_result_schema = AssetVectorBatchUpsertResult
    allow_callback = True
    timeout_seconds = 300
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset(
        {
            "INVALID_JOB_PARAMS",
        }
    )

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        try:
            params = AssetVectorBatchUpsertParams.model_validate(job_params)
        except ValidationError as exc:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "asset_vector_batch_upsert job_params does not match schema",
                {"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AssetVectorBatchUpsertParams.model_validate(job_params)
        return AssetVectorBatchUpsertRuntimeFields(item_count=len(params.items)).model_dump(exclude_none=True)

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = AssetVectorBatchUpsertParams.model_validate(job_params_from_job(job))
        try:
            runtime_fields = AssetVectorBatchUpsertRuntimeFields.model_validate(runtime_fields_from_job(job))
        except ValidationError as exc:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_vector_batch_upsert runtime_fields does not match schema",
                details={"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        if runtime_fields.item_count != len(params.items):
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_vector_batch_upsert runtime_fields does not match job_params",
                details={"expected_item_count": len(params.items), "actual_item_count": runtime_fields.item_count},
            )

        indexed_at = _now_iso()
        result_items = [
            AssetVectorUpsertResultItem(
                item_id=item.item_id,
                status="succeeded",
                indexed=AssetVectorIndexedInfo(indexed_at=indexed_at),
            )
            for item in params.items
        ]
        return AssetVectorBatchUpsertResult(
            batch_summary=AssetVectorUpsertBatchSummary(
                total=len(result_items),
                succeeded=len(result_items),
            ),
            items=result_items,
        ).model_dump(exclude_none=True)

    async def run_success_side_effect(self, job, canonical_result: dict[str, Any], db) -> None:
        params = AssetVectorBatchUpsertParams.model_validate(job_params_from_job(job))
        AssetVectorBatchUpsertResult.model_validate(canonical_result)
        await upsert_items(db, caller_id=job.caller_id, items=params.items)


@register_job_type
class AssetVectorBatchDeleteJob(JobExecutor):
    name = "asset_vector_batch_delete"
    visibility = "public"
    role = "root"
    params_schema = AssetVectorBatchDeleteParams
    runtime_fields_schema_name = "AssetVectorBatchDeleteRuntimeFields"
    canonical_result_schema = AssetVectorBatchDeleteResult
    public_result_schema = AssetVectorBatchDeleteResult
    allow_callback = True
    timeout_seconds = 300
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset({"INVALID_JOB_PARAMS"})

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        try:
            params = AssetVectorBatchDeleteParams.model_validate(job_params)
        except ValidationError as exc:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "asset_vector_batch_delete job_params does not match schema",
                {"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AssetVectorBatchDeleteParams.model_validate(job_params)
        return AssetVectorBatchDeleteRuntimeFields(item_count=len(params.item_ids)).model_dump(exclude_none=True)

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = AssetVectorBatchDeleteParams.model_validate(job_params_from_job(job))
        try:
            runtime_fields = AssetVectorBatchDeleteRuntimeFields.model_validate(runtime_fields_from_job(job))
        except ValidationError as exc:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_vector_batch_delete runtime_fields does not match schema",
                details={"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        if runtime_fields.item_count != len(params.item_ids):
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_vector_batch_delete runtime_fields does not match job_params",
                details={"expected_item_count": len(params.item_ids), "actual_item_count": runtime_fields.item_count},
            )

        result_items = [
            AssetVectorDeleteResultItem(item_id=item_id, status="deleted")
            for item_id in params.item_ids
        ]
        return AssetVectorBatchDeleteResult(
            batch_summary=AssetVectorDeleteBatchSummary(
                total=len(result_items),
                deleted=len(result_items),
            ),
            items=result_items,
        ).model_dump(exclude_none=True)

    async def run_success_side_effect(self, job, canonical_result: dict[str, Any], db) -> None:
        params = AssetVectorBatchDeleteParams.model_validate(job_params_from_job(job))
        AssetVectorBatchDeleteResult.model_validate(canonical_result)
        await delete_items(db, caller_id=job.caller_id, item_ids=params.item_ids)
