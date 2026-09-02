from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_packages.asset_image_tagging.errors import (
    ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED,
    ASSET_IMAGE_TAGGING_ITEMS_EXCEEDS_LIMIT,
)
from app.business_packages.asset_image_tagging.model_adapter import (
    asset_image_tagging_model_adapter_from_settings,
    build_batch_summary,
)
from app.business_packages.asset_image_tagging.schemas import (
    AssetImageTaggingLabelSnapshotGroup,
    AssetImageTaggingItemJobParams,
    AssetImageTaggingItemRuntimeFields,
    AssetImageTaggingJoinParams,
    AssetImageTaggingJoinRuntimeFields,
    AssetImageTaggingParams,
    AssetImageTaggingResult,
    AssetImageTaggingResultItem,
    AssetImageTaggingRuntimeFields,
)
from app.core.config import settings
from app.core.exceptions import AppError, ValidationAppError
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.models.job import Job
from app.repositories.job_repo import JobRepo
from app.services.job_runtime import job_params_from_job, runtime_fields_from_job
from app.workflows import WorkflowDefinition, chord, group, register as register_workflow, task


ASSET_IMAGE_TAGGING_JOB_TYPE = "asset_image_tagging"
ASSET_IMAGE_TAGGING_ITEM_JOB_TYPE = "asset_image_tagging_item"
ASSET_IMAGE_TAGGING_JOIN_JOB_TYPE = "asset_image_tagging_join"
ASSET_IMAGE_TAGGING_JOIN_NODE_KEY = "join"
_WORKFLOW_DEFINITION: WorkflowDefinition | None = None


def _build_runtime_fields(params: AssetImageTaggingParams) -> AssetImageTaggingRuntimeFields:
    category_ids = list(dict.fromkeys(item.category_id for item in params.items))
    return AssetImageTaggingRuntimeFields(
        tagging_language=params.tagging_language,
        item_count=len(params.items),
        label_group_count=len(params.label_snapshot),
        category_ids=category_ids,
    )


def _item_node_key(index: int) -> str:
    return f"item.{index}"


def _matching_label_groups_with_indexes(
    params: AssetImageTaggingParams,
    *,
    item_index: int,
) -> tuple[list[int], list[AssetImageTaggingLabelSnapshotGroup]]:
    item = params.items[item_index]
    pairs = [
        (index, group)
        for index, group in enumerate(params.label_snapshot)
        if group.category_id == item.category_id
    ]
    return [index for index, _group in pairs], [group for _index, group in pairs]


def _build_item_runtime_fields(params: AssetImageTaggingItemJobParams) -> AssetImageTaggingItemRuntimeFields:
    return AssetImageTaggingItemRuntimeFields(
        tagging_language=params.tagging_language,
        item_id=params.item.item_id,
        category_id=params.item.category_id,
        label_group_count=len(params.label_snapshot),
    )


def _build_join_runtime_fields(params: AssetImageTaggingJoinParams) -> AssetImageTaggingJoinRuntimeFields:
    return AssetImageTaggingJoinRuntimeFields(
        tagging_language=params.tagging_language,
        item_count=len(params.item_ids),
    )


def _remap_label_snapshot_indexes(
    item: AssetImageTaggingResultItem,
    *,
    label_snapshot_indexes: list[int],
) -> AssetImageTaggingResultItem:
    remapped_selections = [
        selection.model_copy(update={"label_snapshot_index": label_snapshot_indexes[selection.label_snapshot_index]})
        for selection in item.label_group_selections
    ]
    return item.model_copy(update={"label_group_selections": remapped_selections})


async def _workflow_children(job: Job, db: AsyncSession) -> list[Job]:
    if job.root_job_id is None:
        raise AppError(
            "RUNTIME_REF_MISSING",
            "asset_image_tagging workflow child is missing root_job_id",
            details={"job_id": str(job.id), "job_type": job.job_type},
        )
    return await JobRepo.list_internal_children(db, root_job_id=job.root_job_id)


def _workflow_expr(job_params: dict[str, Any]) -> Any:
    params = AssetImageTaggingParams.model_validate(job_params)
    item_tasks = []
    for index, item in enumerate(params.items):
        label_snapshot_indexes, label_snapshot = _matching_label_groups_with_indexes(params, item_index=index)
        item_tasks.append(
            task(
                _item_node_key(index),
                ASSET_IMAGE_TAGGING_ITEM_JOB_TYPE,
                {
                    "tagging_language": params.tagging_language,
                    "item": item.model_dump(exclude_none=True),
                    "label_snapshot": [group.model_dump(exclude_none=True) for group in label_snapshot],
                    "label_snapshot_indexes": label_snapshot_indexes,
                },
            )
        )
    return chord(
        group(*item_tasks),
        task(
            ASSET_IMAGE_TAGGING_JOIN_NODE_KEY,
            ASSET_IMAGE_TAGGING_JOIN_JOB_TYPE,
            {
                "tagging_language": params.tagging_language,
                "item_ids": [item.item_id for item in params.items],
            },
        ),
    )


def _workflow_definition() -> WorkflowDefinition:
    global _WORKFLOW_DEFINITION
    if _WORKFLOW_DEFINITION is None:
        _WORKFLOW_DEFINITION = WorkflowDefinition(
            workflow_type=ASSET_IMAGE_TAGGING_JOB_TYPE,
            root_job_type=ASSET_IMAGE_TAGGING_JOB_TYPE,
            build=_workflow_expr,
            max_nodes=settings.job.asset_image_tagging.max_items + 1,
            runtime_job_type_dependencies=frozenset(
                {
                    ASSET_IMAGE_TAGGING_ITEM_JOB_TYPE,
                    ASSET_IMAGE_TAGGING_JOIN_JOB_TYPE,
                }
            ),
        )
    return _WORKFLOW_DEFINITION


def register_asset_image_tagging_workflow() -> None:
    register_workflow(_workflow_definition())


def _extract_join_result(result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        raise ValueError("asset_image_tagging succeeded result is required")
    if result.get("job_type") == ASSET_IMAGE_TAGGING_JOB_TYPE and "batch_summary" in result:
        return AssetImageTaggingResult.model_validate(result).model_dump(exclude_none=True)
    workflow = result.get("workflow")
    if not isinstance(workflow, dict):
        raise ValueError("asset_image_tagging workflow result is missing workflow")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("asset_image_tagging workflow result is missing nodes")
    for node in nodes:
        if isinstance(node, dict) and node.get("node_key") == ASSET_IMAGE_TAGGING_JOIN_NODE_KEY:
            join_result = node.get("result")
            if not isinstance(join_result, dict):
                raise ValueError("asset_image_tagging workflow result is missing join result")
            return AssetImageTaggingResult.model_validate(join_result).model_dump(exclude_none=True)
    raise ValueError("asset_image_tagging workflow result is missing join node")


@register_job_type
class AssetImageTaggingJob(JobExecutor):
    name = ASSET_IMAGE_TAGGING_JOB_TYPE
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
            ASSET_IMAGE_TAGGING_ITEMS_EXCEEDS_LIMIT,
            "WORKFLOW_CHILD_FAILED",
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
        max_items = settings.job.asset_image_tagging.max_items
        if len(params.items) > max_items:
            raise ValidationAppError(
                ASSET_IMAGE_TAGGING_ITEMS_EXCEEDS_LIMIT,
                "asset_image_tagging items exceed configured limit",
                {
                    "item_count": len(params.items),
                    "max_items": max_items,
                    "job_type": self.name,
                },
            )
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AssetImageTaggingParams.model_validate(job_params)
        return _build_runtime_fields(params).model_dump(by_alias=True, exclude_none=True)

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
            "asset_image_tagging root must be executed by workflow orchestration",
            details={"job_id": str(job.id), "job_type": job.job_type},
        )


@register_job_type
class AssetImageTaggingItemJob(JobExecutor):
    name = ASSET_IMAGE_TAGGING_ITEM_JOB_TYPE
    visibility = "internal"
    role = "leaf"
    params_schema = AssetImageTaggingItemJobParams
    runtime_fields_schema_name = "AssetImageTaggingItemRuntimeFields"
    canonical_result_schema = AssetImageTaggingResultItem
    public_result_schema = AssetImageTaggingResultItem
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
            params = AssetImageTaggingItemJobParams.model_validate(job_params)
        except ValidationError as exc:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "asset_image_tagging_item job_params does not match schema",
                {"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AssetImageTaggingItemJobParams.model_validate(job_params)
        return _build_item_runtime_fields(params).model_dump(by_alias=True, exclude_none=True)

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = AssetImageTaggingItemJobParams.model_validate(job_params_from_job(job))
        try:
            runtime_fields = AssetImageTaggingItemRuntimeFields.model_validate(runtime_fields_from_job(job))
        except ValidationError as exc:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_image_tagging_item runtime_fields does not match schema",
                details={"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        expected_runtime_fields = _build_item_runtime_fields(params)
        actual_runtime_payload = runtime_fields.model_dump(by_alias=True, exclude_none=True)
        actual_runtime_payload.pop("_system", None)
        expected_runtime_payload = expected_runtime_fields.model_dump(by_alias=True, exclude_none=True)
        if actual_runtime_payload != expected_runtime_payload:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_image_tagging_item runtime_fields does not match job_params",
                details={
                    "job_type": self.name,
                    "item_id": params.item.item_id,
                },
            )

        batch_params = AssetImageTaggingParams(
            tagging_language=params.tagging_language,
            items=[params.item],
            label_snapshot=params.label_snapshot,
        )
        result_items = await asset_image_tagging_model_adapter_from_settings().tag(batch_params)
        if len(result_items) != 1:
            raise AppError(
                "MODEL_OUTPUT_INVALID",
                "asset_image_tagging_item model returned unexpected item count",
                details={"job_type": self.name, "expected": 1, "actual": len(result_items)},
            )
        return _remap_label_snapshot_indexes(
            result_items[0],
            label_snapshot_indexes=params.label_snapshot_indexes,
        ).model_dump(exclude_none=True)


@register_job_type
class AssetImageTaggingJoinJob(JobExecutor):
    name = ASSET_IMAGE_TAGGING_JOIN_JOB_TYPE
    visibility = "internal"
    role = "leaf"
    params_schema = AssetImageTaggingJoinParams
    runtime_fields_schema_name = "AssetImageTaggingJoinRuntimeFields"
    canonical_result_schema = AssetImageTaggingResult
    public_result_schema = AssetImageTaggingResult
    allow_callback = False
    timeout_seconds = 120
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset(
        {
            "INVALID_JOB_PARAMS",
            ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED,
        }
    )

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        try:
            params = AssetImageTaggingJoinParams.model_validate(job_params)
        except ValidationError as exc:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "asset_image_tagging_join job_params does not match schema",
                {"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AssetImageTaggingJoinParams.model_validate(job_params)
        return _build_join_runtime_fields(params).model_dump(by_alias=True, exclude_none=True)

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = AssetImageTaggingJoinParams.model_validate(job_params_from_job(job))
        try:
            runtime_fields = AssetImageTaggingJoinRuntimeFields.model_validate(runtime_fields_from_job(job))
        except ValidationError as exc:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_image_tagging_join runtime_fields does not match schema",
                details={"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        expected_runtime_fields = _build_join_runtime_fields(params)
        actual_runtime_payload = runtime_fields.model_dump(by_alias=True, exclude_none=True)
        actual_runtime_payload.pop("_system", None)
        expected_runtime_payload = expected_runtime_fields.model_dump(by_alias=True, exclude_none=True)
        if actual_runtime_payload != expected_runtime_payload:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_image_tagging_join runtime_fields does not match job_params",
                details={"job_type": self.name},
            )

        children = await _workflow_children(job, db)
        item_results_by_id: dict[str, AssetImageTaggingResultItem] = {}
        for child in children:
            if child.job_type != ASSET_IMAGE_TAGGING_ITEM_JOB_TYPE or child.status != "succeeded":
                continue
            if not isinstance(child.result, dict):
                raise AppError(
                    "RUNTIME_REF_INVALID",
                    "asset_image_tagging item child result is missing",
                    details={"child_job_id": str(child.id), "node_key": child.workflow_node_key},
                )
            result_item = AssetImageTaggingResultItem.model_validate(child.result)
            item_results_by_id[result_item.item_id] = result_item

        missing_item_ids = [item_id for item_id in params.item_ids if item_id not in item_results_by_id]
        if missing_item_ids:
            raise AppError(
                "RUNTIME_REF_INVALID",
                "asset_image_tagging join is missing item child results",
                details={"missing_item_ids": missing_item_ids},
            )

        result_items = [item_results_by_id[item_id] for item_id in params.item_ids]
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
