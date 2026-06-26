from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.jobs.base import JobExecutor, PromptSpec
from app.jobs.registry import register_job_type
from app.models.job import Job
from app.schemas.jobs import (
    JobRealLlmDoubleEchoParams,
    JobRealLlmDoubleEchoResult,
    JobRealLlmDoubleEchoRuntimeFields,
)
from app.services.ai_gateway_facade import generate_text_with_ledger
from app.services.job_runtime import ai_billing_scope_id_from_job, runtime_fields_from_job
from app.services.jobs import _load_input_text, trigger_request_id_from_job

FIRST_LLM_STEP_NAME = "first_llm_call"
SECOND_LLM_STEP_NAME = "second_llm_call"


def _prompt_payload(instruction: str) -> dict[str, Any]:
    return {
        "blocks": [
            {
                "key": "user",
                "role": "user",
                "content": instruction,
            }
        ]
    }


def _messages(prompt_payload: dict[str, Any], input_text: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for block in prompt_payload["blocks"]:
        content = f"{block['content'].strip()}\n\n===待处理文本开始===\n{input_text}\n===待处理文本结束==="
        messages.append({"role": block["role"], "content": content})
    return messages


@register_job_type
class JobRealLlmDoubleEchoJob(JobExecutor):
    name = "job_real_llm_double_echo"
    visibility = "demo"
    role = "root_or_leaf"
    params_schema = JobRealLlmDoubleEchoParams
    runtime_fields_schema_name = "JobRealLlmDoubleEchoRuntimeFields"
    canonical_result_schema = JobRealLlmDoubleEchoResult
    public_result_schema = JobRealLlmDoubleEchoResult
    allow_callback = False
    max_attempts = 1
    timeout_seconds = 240
    requires_text_generation_model = True
    prompt_specs = (
        PromptSpec(
            step_name=FIRST_LLM_STEP_NAME,
            runtime_field="first_prompt_payload",
            prompt_ref="job_real_llm_double_echo.first",
            output_schema_ref="JobRealLlmDoubleEchoResult",
        ),
        PromptSpec(
            step_name=SECOND_LLM_STEP_NAME,
            runtime_field="second_prompt_payload",
            prompt_ref="job_real_llm_double_echo.second",
            output_schema_ref="JobRealLlmDoubleEchoResult",
        ),
    )

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = JobRealLlmDoubleEchoParams.model_validate(job_params)
        return JobRealLlmDoubleEchoRuntimeFields(
            model_id=params.model_id,
            first_prompt_payload=_prompt_payload(params.first_instruction),
            second_prompt_payload=_prompt_payload(params.second_instruction),
        ).model_dump()

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        fields = runtime_fields_from_job(job)
        model_id = fields["model_id"]
        attempt_id = job.active_attempt_id
        if attempt_id is None:
            raise AppError(
                "JOB_RUNTIME_NOT_SUPPORTED",
                "job_real_llm_double_echo requires active_attempt_id",
            )
        input_text = _load_input_text(job)
        request_id = trigger_request_id_from_job(job)
        ai_scope_id = ai_billing_scope_id_from_job(job)

        first = await generate_text_with_ledger(
            caller_id=job.caller_id,
            scope_type="job",
            scope_id=str(ai_scope_id),
            operation="job_real_llm_double_echo.first",
            step_name=FIRST_LLM_STEP_NAME,
            request_id=request_id,
            job_id=job.id,
            attempt_id=attempt_id,
            job_type=job.job_type,
            model_id=model_id,
            messages=_messages(fields["first_prompt_payload"], input_text),
        )
        second = await generate_text_with_ledger(
            caller_id=job.caller_id,
            scope_type="job",
            scope_id=str(ai_scope_id),
            operation="job_real_llm_double_echo.second",
            step_name=SECOND_LLM_STEP_NAME,
            request_id=request_id,
            job_id=job.id,
            attempt_id=attempt_id,
            job_type=job.job_type,
            model_id=model_id,
            messages=_messages(fields["second_prompt_payload"], input_text),
        )
        return JobRealLlmDoubleEchoResult(
            artifacts=[],
            signals={
                "first_message": first.text,
                "second_message": second.text,
                "llm_call_count": 2,
            },
        ).model_dump()

    def validate_normalized_job_params(self, job_params: dict[str, Any]) -> None:
        JobRealLlmDoubleEchoParams.model_validate(job_params)

    def canonical_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return JobRealLlmDoubleEchoParams.model_validate(job_params).model_dump()
