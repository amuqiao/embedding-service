from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_packages.asset_vector.embedding_adapter import (
    DashScopeMultimodalEmbeddingAdapter,
    input_sha256,
    item_embedding_content,
    item_index_text,
)
from app.business_packages.asset_vector.errors import ASSET_VECTOR_ITEMS_EXCEEDS_LIMIT
from app.business_packages.asset_vector.repository import delete_items, upsert_embedded_items
from app.business_packages.asset_vector.schemas import (
    AssetVectorBatchDeleteParams,
    AssetVectorBatchDeleteResult,
    AssetVectorBatchDeleteRuntimeFields,
    AssetVectorBatchUpsertParams,
    AssetVectorBatchUpsertResult,
    AssetVectorBatchUpsertRuntimeFields,
    AssetVectorDeleteBatchSummary,
    AssetVectorDeleteResultItem,
    AssetVectorEmbedItemParams,
    AssetVectorEmbedItemRuntimeFields,
    AssetVectorEmbeddedItemResult,
    AssetVectorIndexedInfo,
    AssetVectorUpsertBatchSummary,
    AssetVectorUpsertJoinParams,
    AssetVectorUpsertJoinRuntimeFields,
    AssetVectorUpsertResultItem,
)
from app.core.config import settings
from app.core.exceptions import AppError, ValidationAppError
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.models.job import Job
from app.repositories.job_repo import JobRepo
from app.services.job_runtime import job_params_from_job, runtime_fields_from_job
from app.workflows import WorkflowDefinition, chord, group, register as register_workflow, task

ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE = "asset_vector_batch_upsert"
ASSET_VECTOR_EMBED_ITEM_JOB_TYPE = "asset_vector_embed_item"
ASSET_VECTOR_UPSERT_JOIN_JOB_TYPE = "asset_vector_upsert_join"
ASSET_VECTOR_UPSERT_JOIN_NODE_KEY = "join"
_WORKFLOW_DEFINITION: WorkflowDefinition | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _item_node_key(index: int) -> str:
    return f"item.{index}"


def _build_upsert_runtime_fields(params: AssetVectorBatchUpsertParams) -> AssetVectorBatchUpsertRuntimeFields:
    return AssetVectorBatchUpsertRuntimeFields(item_count=len(params.items))


def _build_embed_item_runtime_fields(params: AssetVectorEmbedItemParams) -> AssetVectorEmbedItemRuntimeFields:
    return AssetVectorEmbedItemRuntimeFields(item_id=params.item.item_id)


def _build_join_runtime_fields(params: AssetVectorUpsertJoinParams) -> AssetVectorUpsertJoinRuntimeFields:
    return AssetVectorUpsertJoinRuntimeFields(item_count=len(params.item_ids))


def _workflow_expr(job_params: dict[str, Any]) -> Any:
    params = AssetVectorBatchUpsertParams.model_validate(job_params)
    item_tasks = [
        task(
            _item_node_key(index),
            ASSET_VECTOR_EMBED_ITEM_JOB_TYPE,
            {"item": item.model_dump(exclude_none=True)},
        )
        for index, item in enumerate(params.items)
    ]
    return chord(
        group(*item_tasks),
        task(
            ASSET_VECTOR_UPSERT_JOIN_NODE_KEY,
            ASSET_VECTOR_UPSERT_JOIN_JOB_TYPE,
            {"item_ids": [item.item_id for item in params.items]},
        ),
    )


def _workflow_definition() -> WorkflowDefinition:
    global _WORKFLOW_DEFINITION
    if _WORKFLOW_DEFINITION is None:
        _WORKFLOW_DEFINITION = WorkflowDefinition(
            workflow_type=ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE,
            root_job_type=ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE,
            build=_workflow_expr,
            max_nodes=settings.job.asset_vector.max_items + 1,
            runtime_job_type_dependencies=frozenset(
                {
                    ASSET_VECTOR_EMBED_ITEM_JOB_TYPE,
                    ASSET_VECTOR_UPSERT_JOIN_JOB_TYPE,
                }
            ),
        )
    return _WORKFLOW_DEFINITION


def register_asset_vector_workflow() -> None:
    register_workflow(_workflow_definition())


def _extract_join_result(result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        raise ValueError("asset_vector_batch_upsert succeeded result is required")
    if result.get("job_type") == ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE and "batch_summary" in result:
        return AssetVectorBatchUpsertResult.model_validate(result).model_dump(exclude_none=True)
    workflow = result.get("workflow")
    if not isinstance(workflow, dict):
        raise ValueError("asset_vector_batch_upsert workflow result is missing workflow")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("asset_vector_batch_upsert workflow result is missing nodes")
    for node in nodes:
        if isinstance(node, dict) and node.get("node_key") == ASSET_VECTOR_UPSERT_JOIN_NODE_KEY:
            join_result = node.get("result")
            if not isinstance(join_result, dict):
                raise ValueError("asset_vector_batch_upsert workflow result is missing join result")
            return AssetVectorBatchUpsertResult.model_validate(join_result).model_dump(exclude_none=True)
    raise ValueError("asset_vector_batch_upsert workflow result is missing join node")


async def _workflow_children(job: Job, db: AsyncSession) -> list[Job]:
    if job.root_job_id is None:
        raise AppError(
            "RUNTIME_REF_MISSING",
            "asset_vector workflow child is missing root_job_id",
            details={"job_id": str(job.id), "job_type": job.job_type},
        )
    return await JobRepo.list_internal_children(db, root_job_id=job.root_job_id)


@register_job_type
class AssetVectorBatchUpsertJob(JobExecutor):
    name = ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE
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
            ASSET_VECTOR_ITEMS_EXCEEDS_LIMIT,
            "WORKFLOW_CHILD_FAILED",
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
        max_items = settings.job.asset_vector.max_items
        if len(params.items) > max_items:
            raise ValidationAppError(
                ASSET_VECTOR_ITEMS_EXCEEDS_LIMIT,
                "asset_vector_batch_upsert items exceed configured limit",
                {
                    "item_count": len(params.items),
                    "max_items": max_items,
                    "job_type": self.name,
                },
            )
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AssetVectorBatchUpsertParams.model_validate(job_params)
        return _build_upsert_runtime_fields(params).model_dump(exclude_none=True)

    def validate_canonical_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if isinstance(result, dict) and isinstance(result.get("workflow"), dict):
            return result
        return super().validate_canonical_result(result)

    def public_result(self, canonical_result: dict[str, Any]) -> dict[str, Any] | None:
        return self.validate_public_result(canonical_result)

    def validate_public_result(self, result: dict[str, Any] | None) -> dict[str, Any] | None:
        return _extract_join_result(result)

    async def _execute(self, job, db) -> dict[str, Any] | None:
        raise AppError(
            "JOB_RUNTIME_NOT_SUPPORTED",
            "asset_vector_batch_upsert root must be executed by workflow orchestration",
            details={"job_id": str(job.id), "job_type": job.job_type},
        )


@register_job_type
class AssetVectorEmbedItemJob(JobExecutor):
    name = ASSET_VECTOR_EMBED_ITEM_JOB_TYPE
    visibility = "internal"
    role = "leaf"
    params_schema = AssetVectorEmbedItemParams
    runtime_fields_schema_name = "AssetVectorEmbedItemRuntimeFields"
    canonical_result_schema = AssetVectorEmbeddedItemResult
    public_result_schema = AssetVectorEmbeddedItemResult
    allow_callback = False
    timeout_seconds = 300
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset(
        {
            "INVALID_JOB_PARAMS",
            "MODEL_CALL_FAILED",
            "MODEL_CALL_TIMEOUT",
            "MODEL_OUTPUT_INVALID",
        }
    )

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        try:
            params = AssetVectorEmbedItemParams.model_validate(job_params)
        except ValidationError as exc:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "asset_vector_embed_item job_params does not match schema",
                {"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AssetVectorEmbedItemParams.model_validate(job_params)
        return _build_embed_item_runtime_fields(params).model_dump(exclude_none=True)

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = AssetVectorEmbedItemParams.model_validate(job_params_from_job(job))
        try:
            runtime_fields = AssetVectorEmbedItemRuntimeFields.model_validate(runtime_fields_from_job(job))
        except ValidationError as exc:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_vector_embed_item runtime_fields does not match schema",
                details={"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        expected_runtime_fields = _build_embed_item_runtime_fields(params)
        actual_runtime_payload = runtime_fields.model_dump(by_alias=True, exclude_none=True)
        actual_runtime_payload.pop("_system", None)
        if actual_runtime_payload != expected_runtime_fields.model_dump(by_alias=True, exclude_none=True):
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_vector_embed_item runtime_fields does not match job_params",
                details={"job_type": self.name, "item_id": params.item.item_id},
            )

        adapter = DashScopeMultimodalEmbeddingAdapter()
        content = item_embedding_content(params.item)
        embedding = await adapter.embed(content)
        return AssetVectorEmbeddedItemResult(
            item=params.item,
            embedding=embedding,
            embedding_text=item_index_text(params.item),
            model_id=adapter.config.model_id,
            dimension=adapter.config.dimension,
            input_sha256=input_sha256(content),
        ).model_dump(exclude_none=True)


@register_job_type
class AssetVectorUpsertJoinJob(JobExecutor):
    name = ASSET_VECTOR_UPSERT_JOIN_JOB_TYPE
    visibility = "internal"
    role = "leaf"
    params_schema = AssetVectorUpsertJoinParams
    runtime_fields_schema_name = "AssetVectorUpsertJoinRuntimeFields"
    canonical_result_schema = AssetVectorBatchUpsertResult
    public_result_schema = AssetVectorBatchUpsertResult
    allow_callback = False
    timeout_seconds = 120
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset({"INVALID_JOB_PARAMS"})

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        try:
            params = AssetVectorUpsertJoinParams.model_validate(job_params)
        except ValidationError as exc:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "asset_vector_upsert_join job_params does not match schema",
                {"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AssetVectorUpsertJoinParams.model_validate(job_params)
        return _build_join_runtime_fields(params).model_dump(exclude_none=True)

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = AssetVectorUpsertJoinParams.model_validate(job_params_from_job(job))
        try:
            runtime_fields = AssetVectorUpsertJoinRuntimeFields.model_validate(runtime_fields_from_job(job))
        except ValidationError as exc:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_vector_upsert_join runtime_fields does not match schema",
                details={"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        expected_runtime_fields = _build_join_runtime_fields(params)
        actual_runtime_payload = runtime_fields.model_dump(by_alias=True, exclude_none=True)
        actual_runtime_payload.pop("_system", None)
        if actual_runtime_payload != expected_runtime_fields.model_dump(by_alias=True, exclude_none=True):
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_vector_upsert_join runtime_fields does not match job_params",
                details={"job_type": self.name},
            )

        children = await _workflow_children(job, db)
        embedded_by_id: dict[str, AssetVectorEmbeddedItemResult] = {}
        for child in children:
            if child.job_type != ASSET_VECTOR_EMBED_ITEM_JOB_TYPE or child.status != "succeeded":
                continue
            if not isinstance(child.result, dict):
                raise AppError(
                    "RUNTIME_REF_INVALID",
                    "asset_vector embed child result is missing",
                    details={"child_job_id": str(child.id), "node_key": child.workflow_node_key},
                )
            embedded = AssetVectorEmbeddedItemResult.model_validate(child.result)
            embedded_by_id[embedded.item.item_id] = embedded

        missing_item_ids = [item_id for item_id in params.item_ids if item_id not in embedded_by_id]
        if missing_item_ids:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_vector_upsert_join is missing embed child results",
                details={"missing_item_ids": missing_item_ids},
            )

        embedded_items = [embedded_by_id[item_id] for item_id in params.item_ids]
        indexed_pairs = await upsert_embedded_items(db, caller_id=job.caller_id, items=embedded_items)
        indexed_at_by_id = dict(indexed_pairs)
        result_items = [
            AssetVectorUpsertResultItem(
                item_id=item.item.item_id,
                status="succeeded",
                indexed=AssetVectorIndexedInfo(indexed_at=indexed_at_by_id[item.item.item_id]),
            )
            for item in embedded_items
        ]
        return AssetVectorBatchUpsertResult(
            batch_summary=AssetVectorUpsertBatchSummary(total=len(result_items), succeeded=len(result_items)),
            items=result_items,
        ).model_dump(exclude_none=True)


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
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset(
        {
            "INVALID_JOB_PARAMS",
            ASSET_VECTOR_ITEMS_EXCEEDS_LIMIT,
        }
    )

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        try:
            params = AssetVectorBatchDeleteParams.model_validate(job_params)
        except ValidationError as exc:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "asset_vector_batch_delete job_params does not match schema",
                {"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        max_items = settings.job.asset_vector.delete_max_items
        if len(params.item_ids) > max_items:
            raise ValidationAppError(
                ASSET_VECTOR_ITEMS_EXCEEDS_LIMIT,
                "asset_vector_batch_delete item_ids exceed configured limit",
                {
                    "item_count": len(params.item_ids),
                    "max_items": max_items,
                    "job_type": self.name,
                },
            )
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
