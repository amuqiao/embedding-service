from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING
from urllib.parse import urlparse

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.workflow_registry import WorkflowHandler, register
from app.integrations.storage import sha256_digest, storage
from app.schemas.jobs import JobResult
from app.services.job_planner import JobPlan, PlannedWorkItem
from app.services.job_runtime import job_params_from_job, model_id_from_job, payload_hash, work_item_payload
from app.workflows.short_drama_tagging.adapter import (
    RS_AI_TAG_RESULTS_ARTIFACT_KEY,
    build_rs_ai_tag_results_payload,
    rs_ai_tag_results_payload_from_canonical_result,
)
from app.workflows.short_drama_tagging.prompts import parse_model_json, stage_messages
from app.workflows.short_drama_tagging.rs_client import (
    assert_schema_fixture_available,
    get_tag_schema_provider,
    get_tagging_result_writer,
)
from app.workflows.short_drama_tagging.schemas import ShortDramaTaggingParams, TagSchemaTranslationParams
from app.workflows.short_drama_tagging.translation import parse_translation_output, translation_messages

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.job import AIJob, AIJobWorkItem


def _normalize_job_params(job_params: dict[str, Any]) -> dict[str, Any]:
    params = ShortDramaTaggingParams.model_validate(job_params).model_dump()
    if settings.SHORT_DRAMA_RS_SCHEMA_SOURCE == "fixture":
        assert_schema_fixture_available(
            settings.SHORT_DRAMA_RS_SCHEMA_FIXTURE_PATH,
            params["work_context"]["subtitle_language"],
        )
    return params


def _oss_text_from_uri(uri: str, expected_hash: str | None) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "oss" or not parsed.netloc or not parsed.path:
        raise AppError("INVALID_INPUT", f"unsupported subtitle uri: {uri}", status_code=422)
    text = storage.read_text(
        bucket=parsed.netloc,
        key=parsed.path.lstrip("/"),
        region=settings.OSS_REGION or "local",
    )
    if expected_hash:
        actual_hash = sha256_digest(text.encode("utf-8"))
        if actual_hash != expected_hash:
            raise AppError(
                "INPUT_HASH_MISMATCH",
                "subtitle asset content_hash mismatch",
                status_code=422,
                details={"uri": uri, "expected": expected_hash, "actual": actual_hash},
            )
    return text


def _hydrate_subtitle_texts(job_params: dict[str, Any]) -> dict[str, Any]:
    hydrated = json.loads(json.dumps(job_params, ensure_ascii=False))
    for asset in hydrated["assets"]:
        if asset["asset_type"] != "subtitle_srt":
            continue
        if asset.get("text"):
            continue
        asset["text"] = _oss_text_from_uri(asset["uri"], asset.get("content_hash"))
    return hydrated


class ShortDramaTaggingHandler(WorkflowHandler):
    canvas_pattern = "single"
    chunking_enabled = False
    expose_result_in_job_view = False

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return _normalize_job_params(job_params)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_id": settings.DEFAULT_MODEL_ID,
            "rs_schema_source": settings.SHORT_DRAMA_RS_SCHEMA_SOURCE,
            "rs_result_sink": settings.SHORT_DRAMA_RS_RESULT_SINK,
        }

    def build_execution_plan(self, job: AIJob) -> JobPlan:
        params = job_params_from_job(job)
        return JobPlan(
            execution_mode="single",
            chunk_count=1,
            chunk_registry=[{"chunk_index": 1, "kind": "short_drama_tagging"}],
            work_items=[
                PlannedWorkItem(
                    name=f"{self.job_type}.whole",
                    kind="whole",
                    chunk_index=0,
                    input_data=params,
                )
            ],
        )

    async def execute_standard_item(
        self,
        item: AIJobWorkItem,
        job: AIJob,
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        from app.integrations.ai_gateway import generate_text
        from app.repositories.job_repo import JobRepo

        params = ShortDramaTaggingParams.model_validate(work_item_payload(item)).model_dump()
        language = params["work_context"]["subtitle_language"]
        await JobRepo.update_progress(db, job.id, progress_percent=35, progress_text="正在获取 RS 标签体系")
        await db.commit()
        rs_default_tag_bundle = await get_tag_schema_provider().fetch(language)
        tag_schema = rs_default_tag_bundle["tag_schema_snapshot"]
        mutual_exclusion_rules = rs_default_tag_bundle["mutual_exclusion_rules"]
        params = _hydrate_subtitle_texts(params)

        model_id = model_id_from_job(job) or settings.DEFAULT_MODEL_ID
        artifacts: dict[str, Any] = {}
        rendered_prompts: dict[str, list[dict[str, str]]] = {}
        for percent, stage, output_key in (
            (45, "story_overview", "story_overview_result"),
            (60, "candidate_tagging", "candidate_tags"),
            (75, "finalize", "final_result"),
        ):
            await JobRepo.update_progress(db, job.id, progress_percent=percent, progress_text=f"正在执行 {stage}")
            await db.commit()
            messages = stage_messages(
                stage,
                job_params=params,
                tag_schema=tag_schema,
                mutual_exclusion_rules=mutual_exclusion_rules,
                artifacts=artifacts,
            )
            rendered_prompts[stage] = messages
            result = await generate_text(model_id, messages)
            parsed = parse_model_json(result.text, stage)
            artifacts[output_key] = parsed

        rs_payload, tagging_detail = build_rs_ai_tag_results_payload(
            t_book_id=params["t_book_id"],
            job_id=str(job.id),
            tag_schema=tag_schema,
            mutual_exclusion_rules=mutual_exclusion_rules,
            final_result=artifacts["final_result"],
        )
        result_status = tagging_detail["result_status"]
        await JobRepo.update_progress(db, job.id, progress_percent=85, progress_text="正在生成 RS 兼容打标结果")
        await db.commit()

        success = result_status == "success"
        return JobResult(
            artifacts=[
                {"key": RS_AI_TAG_RESULTS_ARTIFACT_KEY, "type": "json", "label": "RS ai-tag-results 请求 payload", "content": rs_payload},
                {"key": "story_overview", "type": "json", "label": "剧情概览", "content": artifacts["story_overview_result"]},
                {"key": "tagging_detail", "type": "json", "label": "打标明细", "content": tagging_detail},
                {"key": "rs_default_tag_bundle", "type": "json", "label": "RS 标签体系快照", "content": rs_default_tag_bundle},
                {"key": "prompts", "type": "json", "label": "模型 Prompt", "content": rendered_prompts},
            ],
            signals={
                "success": success,
                "result_status": result_status,
                "validation_issue_count": len(tagging_detail.get("validation_issues") or []),
                "validation_issues": tagging_detail.get("validation_issues") or [],
                "reason_codes": [
                    issue.get("issue")
                    for issue in (tagging_detail.get("validation_issues") or [])
                    if isinstance(issue, dict) and issue.get("issue")
                ],
                "t_book_id": params["t_book_id"],
                "subtitle_language": language,
                "requested_schema_language": language,
                "source_schema_hash": payload_hash(tag_schema),
                "rs_write_after_callback": True,
            },
        ).model_dump()

    async def after_success_callback(
        self,
        job: AIJob,
        canonical_result: dict[str, Any],
        db: AsyncSession,
    ) -> None:
        rs_payload = rs_ai_tag_results_payload_from_canonical_result(canonical_result)
        await get_tagging_result_writer().write(rs_payload)

    def parse_output(self, text: str) -> JobResult:
        raise NotImplementedError("short drama tagging uses execute_standard_item")


class InitialShortDramaTaggingHandler(ShortDramaTaggingHandler):
    job_type = "short_drama.tagging.initial"


class IncrementalShortDramaTaggingHandler(ShortDramaTaggingHandler):
    job_type = "short_drama.tagging.incremental"


class TagSchemaTranslationHandler(WorkflowHandler):
    job_type = "short_drama.tag_schema.translation"
    canvas_pattern = "single"
    chunking_enabled = False

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return TagSchemaTranslationParams.model_validate(job_params).model_dump()

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return {"model_id": settings.DEFAULT_MODEL_ID}

    def build_execution_plan(self, job: AIJob) -> JobPlan:
        params = job_params_from_job(job)
        return JobPlan(
            execution_mode="single",
            chunk_count=1,
            chunk_registry=[{"chunk_index": 1, "kind": "tag_schema_translation"}],
            work_items=[
                PlannedWorkItem(
                    name=f"{self.job_type}.whole",
                    kind="whole",
                    chunk_index=0,
                    input_data=params,
                )
            ],
        )

    async def execute_standard_item(
        self,
        item: AIJobWorkItem,
        job: AIJob,
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        from app.integrations.ai_gateway import generate_text

        params = TagSchemaTranslationParams.model_validate(work_item_payload(item)).model_dump()
        model_id = model_id_from_job(job) or settings.DEFAULT_MODEL_ID
        result = await generate_text(model_id, translation_messages(params))
        return parse_translation_output(result.text, params)

    def parse_output(self, text: str) -> JobResult:
        raise NotImplementedError("tag schema translation uses execute_standard_item")


def register_all() -> None:
    register(InitialShortDramaTaggingHandler())
    register(IncrementalShortDramaTaggingHandler())
    register(TagSchemaTranslationHandler())
