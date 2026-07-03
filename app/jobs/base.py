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
ATTEMPT_PURPOSES = frozenset({"workflow_orchestration", "business_execution"})
RETRY_BACKOFF_KINDS = frozenset({"none", "fixed", "exponential"})
SIDE_EFFECT_POLICIES = frozenset({"none", "success_side_effect"})
JOB_TYPE_VISIBILITIES = frozenset({"public", "internal", "demo"})
JOB_TYPE_ROLES = frozenset({"root", "leaf", "root_or_leaf"})
JOB_RESULT_SNAPSHOT_STATUSES = frozenset({"running", "failed"})


@dataclass(frozen=True)
class PromptSpec:
    step_name: str
    runtime_field: str
    prompt_ref: str
    output_schema_ref: str


@dataclass(frozen=True)
class JobTypeSpec:
    job_type: str
    visibility: str
    role: str
    execution_mode: str
    retry_policy: dict[str, Any]
    side_effect_policy: str
    params_schema: str
    runtime_fields_schema: str
    canonical_result_schema: str
    public_result_schema: str
    callback_envelope_schema: str
    allow_callback: bool
    result_snapshot_statuses: frozenset[str]
    large_artifact_keys: frozenset[str]
    error_codes: frozenset[str]
    log_events: tuple[str, ...]
    timeout_seconds: int
    prompt_specs: tuple[PromptSpec, ...] = ()
    prompt_template_required_blocks: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ExecutionRetryPolicy:
    domain: str
    max_attempts: int
    retry_delay_seconds: int | None
    backoff_kind: str
    retryable_error_codes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.domain not in ATTEMPT_PURPOSES:
            raise ValueError(f"invalid retry policy domain: {self.domain}")
        if self.max_attempts < 1:
            raise ValueError("retry policy max_attempts must be >= 1")
        if self.retry_delay_seconds is not None and self.retry_delay_seconds < 0:
            raise ValueError("retry policy retry_delay_seconds must be >= 0")
        if self.backoff_kind not in RETRY_BACKOFF_KINDS:
            raise ValueError(f"invalid retry policy backoff_kind: {self.backoff_kind}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "max_attempts": self.max_attempts,
            "retry_delay_seconds": self.retry_delay_seconds,
            "backoff_kind": self.backoff_kind,
            "retryable_error_codes": sorted(self.retryable_error_codes),
        }


@dataclass(frozen=True)
class JobRetryPolicy:
    workflow_orchestration: ExecutionRetryPolicy = ExecutionRetryPolicy(
        domain="workflow_orchestration",
        max_attempts=3,
        retry_delay_seconds=5,
        backoff_kind="fixed",
        retryable_error_codes=frozenset({"JOB_STATE_TRANSITION_CONFLICT", "TASKIQ_PUBLISH_FAILED"}),
    )
    business_execution: ExecutionRetryPolicy = ExecutionRetryPolicy(
        domain="business_execution",
        max_attempts=1,
        retry_delay_seconds=None,
        backoff_kind="none",
        retryable_error_codes=frozenset(),
    )

    def for_purpose(self, purpose: str) -> ExecutionRetryPolicy:
        if purpose == "workflow_orchestration":
            return self.workflow_orchestration
        if purpose == "business_execution":
            return self.business_execution
        raise ValueError(f"unknown attempt purpose: {purpose}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "workflow_orchestration": self.workflow_orchestration.snapshot(),
            "business_execution": self.business_execution.snapshot(),
        }


def _schema_name(schema: type[BaseModel] | None) -> str:
    return schema.__name__ if schema is not None else "null"


class JobExecutor(ABC):
    """ABC and template-method base class for one registered job_type."""

    name: str = ""
    visibility: str = ""
    role: str = ""
    allow_callback: bool = True
    result_snapshot_statuses: frozenset[str] = frozenset()
    timeout_seconds: int = 300
    retry_policy: JobRetryPolicy | None = None
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
            "AI_PROVIDER_FAILED",
            "MODEL_CALL_FAILED",
            "MODEL_OUTPUT_INVALID",
            "MODEL_CALL_TIMEOUT",
            "JOB_TIMEOUT",
        }
    )
    log_events: tuple[str, ...] = ()
    large_artifact_keys: frozenset[str] = frozenset()
    prompt_specs: tuple[PromptSpec, ...] = ()
    prompt_template_required_blocks: frozenset[str] = frozenset()
    requires_text_generation_model: bool = False

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

    def supports_result_snapshot(self, status: str) -> bool:
        return status in self.result_snapshot_statuses

    def validate_result_snapshot(self, status: str, result: dict[str, Any] | None) -> dict[str, Any] | None:
        if status not in JOB_RESULT_SNAPSHOT_STATUSES:
            raise ValueError(f"{self.name} does not support {status} result snapshots")
        if result is None:
            return None
        if not self.supports_result_snapshot(status):
            raise ValueError(f"{self.name} {status} result must be null")
        return self.validate_public_result(result)

    async def build_result_snapshot(self, status: str, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        if status not in JOB_RESULT_SNAPSHOT_STATUSES:
            raise ValueError(f"{self.name} does not support {status} result snapshots")
        return job.result

    def _execution_mode(self) -> str:
        if type(self)._execute is JobExecutor._execute:
            return "builtin_llm_text_runtime"
        return "custom_executor"

    def _side_effect_policy(self) -> str:
        if type(self).run_success_side_effect is JobExecutor.run_success_side_effect:
            return "none"
        return "success_side_effect"

    def effective_retry_policy(self) -> JobRetryPolicy:
        return self.retry_policy if self.retry_policy is not None else JobRetryPolicy()

    def job_type_spec(self) -> JobTypeSpec:
        if "visibility" not in type(self).__dict__:
            raise ValueError(f"{self.name} must declare visibility")
        if "role" not in type(self).__dict__:
            raise ValueError(f"{self.name} must declare role")
        if self.visibility not in JOB_TYPE_VISIBILITIES:
            raise ValueError(f"{self.name} declares invalid visibility: {self.visibility}")
        if self.role not in JOB_TYPE_ROLES:
            raise ValueError(f"{self.name} declares invalid role: {self.role}")
        invalid_snapshot_statuses = self.result_snapshot_statuses - JOB_RESULT_SNAPSHOT_STATUSES
        if invalid_snapshot_statuses:
            raise ValueError(
                f"{self.name} declares invalid result snapshot statuses: {sorted(invalid_snapshot_statuses)}"
            )
        return JobTypeSpec(
            job_type=self.name,
            visibility=self.visibility,
            role=self.role,
            execution_mode=self._execution_mode(),
            retry_policy=self.effective_retry_policy().snapshot(),
            side_effect_policy=self._side_effect_policy(),
            params_schema=_schema_name(self.params_schema),
            runtime_fields_schema=self.runtime_fields_schema_name,
            canonical_result_schema=_schema_name(self.canonical_result_schema),
            public_result_schema=_schema_name(self.public_result_schema),
            callback_envelope_schema="CallbackEnvelope[JobEnvelope]",
            allow_callback=self.allow_callback,
            result_snapshot_statuses=self.result_snapshot_statuses,
            large_artifact_keys=self.large_artifact_keys,
            error_codes=self.allowed_error_codes,
            log_events=self.log_events,
            timeout_seconds=self.timeout_seconds,
            prompt_specs=self.prompt_specs,
            prompt_template_required_blocks=self.prompt_template_required_blocks,
        )
