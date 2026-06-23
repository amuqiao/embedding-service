from __future__ import annotations

import json
from typing import Any

from app.core.exceptions import AppError
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.schemas.jobs import JobRealLlmEchoParams, JobRealLlmEchoResult, JobRealLlmEchoRuntimeFields, JobResult


@register_job_type
class JobRealLlmEchoJob(JobExecutor):
    name = "job_real_llm_echo"
    params_schema = JobRealLlmEchoParams
    runtime_fields_schema_name = "JobRealLlmEchoRuntimeFields"
    canonical_result_schema = JobRealLlmEchoResult
    public_result_schema = JobRealLlmEchoResult
    allow_callback = False
    max_attempts = 1
    timeout_seconds = 180

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
        return JobRealLlmEchoRuntimeFields(model_id=params.model_id, prompt_payload=prompt_payload).model_dump()

    def parse_output(self, text: str) -> JobResult:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError("MODEL_OUTPUT_INVALID", "job_real_llm_echo output must be JSON") from exc
        if not isinstance(payload, dict):
            raise AppError("MODEL_OUTPUT_INVALID", "job_real_llm_echo output must be a JSON object")
        return JobResult.model_validate(payload)
