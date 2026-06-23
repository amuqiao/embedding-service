from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.job import Job
    from app.schemas.callbacks import CallbackResponseEnvelope
    from app.schemas.jobs import JobResult


EXECUTION_MODES = frozenset({"custom_executor", "builtin_llm_text_runtime"})
PLATFORM_RETRY_POLICIES = frozenset({"no_platform_retry", "retry_transient_platform_errors"})
SIDE_EFFECT_POLICIES = frozenset({"none", "success_side_effect"})


@dataclass(frozen=True)
class JobTypeSpec:
    job_type: str
    execution_mode: str
    platform_retry_policy: str
    side_effect_policy: str
    params_schema: str
    runtime_fields_schema: str
    canonical_result_schema: str
    public_result_schema: str
    callback_envelope_schema: str
    allow_callback: bool
    large_artifact_keys: frozenset[str]
    error_codes: frozenset[str]
    log_events: tuple[str, ...]
    max_attempts: int
    timeout_seconds: int


def _schema_name(schema: type[BaseModel] | None) -> str:
    return schema.__name__ if schema is not None else "null"


class JobExecutor(ABC):
    """ABC and template-method base class for one registered job_type."""

    name: str = ""
    allow_callback: bool = True
    max_attempts: int = 1
    timeout_seconds: int = 300
    platform_retry_policy: str | None = None
    params_schema: type[BaseModel] | None = None
    canonical_result_schema: type[BaseModel] | None = None
    public_result_schema: type[BaseModel] | None = None
    runtime_fields_schema_name: str = "dict"
    allowed_error_codes: frozenset[str] = frozenset(
        {
            "INVALID_INPUT",
            "JOB_STATE_TRANSITION_CONFLICT",
            "JOB_EXECUTION_FAILED",
            "WORKFLOW_AFTER_SUCCESS_FAILED",
            "JOB_RUNTIME_NOT_SUPPORTED",
            "MODEL_CALL_FAILED",
            "MODEL_OUTPUT_INVALID",
            "MODEL_CALL_TIMEOUT",
            "JOB_TIMEOUT",
        }
    )
    log_events: tuple[str, ...] = ()
    large_artifact_keys: frozenset[str] = frozenset()

    @property
    def job_type(self) -> str:
        return self.name

    def parse_output(self, text: str) -> JobResult:
        """Parse raw LLM output into JobResult. Required for built-in LLM runtime job types."""
        raise NotImplementedError(f"{self.__class__.__name__}.parse_output() not implemented")

    async def execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        """Template method called by the shared Job runner."""

        return await self._execute(job, db)

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        """Override for custom runtimes. Return None to use the built-in LLM text runtime."""
        return None

    async def run_success_side_effect(
        self,
        job: Job,
        canonical_result: dict[str, Any],
        db: AsyncSession,
    ) -> None:
        """Run optional side effects after canonical result validation and before marking success."""

    def build_callback_data(self, job: Job) -> dict[str, Any]:
        return job.result if isinstance(job.result, dict) else {}

    def validate_callback_response(self, response: CallbackResponseEnvelope) -> None:
        """Validate job-type-specific callback response data."""

    def validate_extra(self, extra: dict[str, Any] | None) -> None:
        """Validate legacy job-type-specific extra params. Prefer normalize_job_params()."""

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        if self.params_schema is not None:
            return self.params_schema.model_validate(job_params).model_dump()
        return job_params

    def canonical_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return self.normalize_job_params(job_params)

    def validate_normalized_job_params(self, job_params: dict[str, Any]) -> None:
        """Validate runtime prerequisites derived from already-normalized params."""

    @abstractmethod
    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        """Return fields required by the current executor/storage runtime."""

    def public_result(self, canonical_result: dict[str, Any]) -> dict[str, Any] | None:
        if self.public_result_schema is None:
            return self.validate_public_result(None)
        return self.validate_public_result(canonical_result)

    def validate_canonical_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if self.canonical_result_schema is None:
            return result
        return self.canonical_result_schema.model_validate(result).model_dump(exclude_none=True)

    def validate_public_result(self, result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            if self.public_result_schema is None:
                return None
            raise ValueError(f"{self.name} succeeded result is required")
        if self.public_result_schema is None:
            raise ValueError(f"{self.name} public result must be null")
        return self.public_result_schema.model_validate(result).model_dump(exclude_none=True)

    def _execution_mode(self) -> str:
        if type(self)._execute is JobExecutor._execute:
            return "builtin_llm_text_runtime"
        return "custom_executor"

    def _side_effect_policy(self) -> str:
        if type(self).run_success_side_effect is JobExecutor.run_success_side_effect:
            return "none"
        return "success_side_effect"

    def job_type_spec(self) -> JobTypeSpec:
        return JobTypeSpec(
            job_type=self.name,
            execution_mode=self._execution_mode(),
            platform_retry_policy=(
                self.platform_retry_policy if self.platform_retry_policy is not None else "no_platform_retry"
            ),
            side_effect_policy=self._side_effect_policy(),
            params_schema=_schema_name(self.params_schema),
            runtime_fields_schema=self.runtime_fields_schema_name,
            canonical_result_schema=_schema_name(self.canonical_result_schema),
            public_result_schema=_schema_name(self.public_result_schema),
            callback_envelope_schema="CallbackEnvelope[JobEnvelope]",
            allow_callback=self.allow_callback,
            large_artifact_keys=self.large_artifact_keys,
            error_codes=self.allowed_error_codes,
            log_events=self.log_events,
            max_attempts=self.max_attempts,
            timeout_seconds=self.timeout_seconds,
        )
