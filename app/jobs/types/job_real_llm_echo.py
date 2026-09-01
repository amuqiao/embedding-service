from __future__ import annotations

import json
from typing import Any

from app.ai.capabilities import TEXT_GENERATION
from app.ai.resolver import resolve_route_config_hash
from app.core.exceptions import AppError
from app.jobs.base import JobExecutor, PromptSpec
from app.jobs.registry import register_job_type
from app.business_packages.base import BusinessPackage
from app.jobs.types._registrar import RegisterExecutor
from app.schemas.jobs import JobRealLlmEchoParams, JobRealLlmEchoResult, JobRealLlmEchoRuntimeFields, JobResult


@register_job_type
class JobRealLlmEchoJob(JobExecutor):
    name = "job_real_llm_echo"
    visibility = "demo"
    role = "root_or_leaf"
    params_schema = JobRealLlmEchoParams
    runtime_fields_schema_name = "JobRealLlmEchoRuntimeFields"
    canonical_result_schema = JobRealLlmEchoResult
    public_result_schema = JobRealLlmEchoResult
    allow_callback = False
    timeout_seconds = 180
    prompt_specs = (
        PromptSpec(
            step_name="calling_model",
            runtime_field="prompt_payload",
            prompt_ref="job_real_llm_echo.calling_model",
            output_schema_ref="JobRealLlmEchoResult",
        ),
    )

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = JobRealLlmEchoParams.model_validate(job_params)
        prompt_payload = {
            "blocks": [
                {
                    "key": "user",
                    "role": "user",
                    "content": (
                        f"{params.instruction}\n\n"
                        "只输出一个 JSON object，不要输出 Markdown。格式：\n"
                        '{"artifacts":[],"signals":{"message":"<一句话结果>"}}'
                    ),
                }
            ]
        }
        return JobRealLlmEchoRuntimeFields(
            model_id=params.model_id,
            model_route_config_hash=resolve_route_config_hash(
                capability=TEXT_GENERATION,
                requested_model_id=params.model_id,
            ),
            prompt_payload=prompt_payload,
        ).model_dump()

    def parse_output(self, text: str) -> JobResult:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError("MODEL_OUTPUT_INVALID", "job_real_llm_echo output must be JSON") from exc
        if not isinstance(payload, dict):
            raise AppError("MODEL_OUTPUT_INVALID", "job_real_llm_echo output must be a JSON object")
        return JobResult.model_validate(payload)


def register_job_package(register: RegisterExecutor) -> None:
    register(JobRealLlmEchoJob())


PACKAGE = BusinessPackage(name="job_real_llm_echo", register=register_job_package)
