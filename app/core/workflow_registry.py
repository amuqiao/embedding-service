from __future__ import annotations

from typing import Any, TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.job import AIJob, AIJobWorkItem
    from app.schemas.callbacks import CallbackResponseEnvelope
    from app.schemas.jobs import JobEnvelope, JobResult
    from app.services.job_planner import JobPlan

CanvasPattern = str  # "single" | "memory_fanout" | "plain_chord" | "scan_chord"


class WorkflowHandler:
    """
    Base class for all workflow handlers. Subclass to add a new job type.

    Steps:
    1. Subclass WorkflowHandler, set job_type, canvas_pattern, and chunking config
    2. Implement parse_output() for LLM text runtime, or execute_standard_item() for custom runtime
    3. Implement merge_chunks() / execute_special_item() when the chosen canvas needs them
    4. Call workflow_registry.register(MyHandler()) in your module's register_all()
    5. Call register_all() in app/main.py startup
    """

    job_type: str = ""
    canvas_pattern: CanvasPattern = "single"
    chunking_enabled: bool = False
    max_single_chars: int = 20000
    chunk_size: int = 3000
    allow_callback: bool = True
    params_schema: type[BaseModel] | None = None
    canonical_result_schema: type[BaseModel] | None = None
    public_result_schema: type[BaseModel] | None = None
    # Keys of artifacts whose content should be written to OSS (large outputs).
    # Artifacts not in this set keep their content inline in the public result.
    large_artifact_keys: frozenset[str] = frozenset()

    def parse_output(self, text: str) -> JobResult:
        """Parse raw LLM output into JobResult. Required for handlers using the built-in LLM runtime."""
        raise NotImplementedError(f"{self.__class__.__name__}.parse_output() not implemented")

    def merge_chunks(self, items: list[AIJobWorkItem]) -> JobResult:
        """Merge completed chunk work items into a final JobResult. Required for chunked handlers."""
        raise NotImplementedError(f"{self.__class__.__name__}.merge_chunks() not implemented")

    async def execute_special_item(
        self,
        item: AIJobWorkItem,
        job: AIJob,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Execute special work items (kind='memory', 'scan', etc.).
        Only called when item.kind not in ('whole', 'chunk', 'merge')."""
        raise NotImplementedError(
            f"{self.__class__.__name__} has no special item handler for kind={item.kind!r}"
        )

    async def execute_standard_item(
        self,
        item: AIJobWorkItem,
        job: AIJob,
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        """Execute non-LLM standard items for custom runtimes.

        Return None to use the built-in LLM text runtime.
        """
        return None

    async def run_success_side_effect(
        self,
        job: AIJob,
        canonical_result: dict[str, Any],
        db: AsyncSession,
    ) -> None:
        """Run idempotent workflow-specific side effects before the job is marked succeeded."""

        await self.after_success_callback(job, canonical_result, db)

    async def after_success_callback(
        self,
        job: AIJob,
        canonical_result: dict[str, Any],
        db: AsyncSession,
    ) -> None:
        """Deprecated compatibility hook. New handlers should override run_success_side_effect()."""

    def build_callback_data(self, job: AIJob) -> dict[str, Any]:
        """Return job-type-specific callback envelope data."""
        return job.result if isinstance(job.result, dict) else {}

    def validate_callback_response(self, response: CallbackResponseEnvelope) -> None:
        """Validate job-type-specific callback response data."""

    def validate_extra(self, extra: dict[str, Any] | None) -> None:
        """Validate legacy job-type-specific extra params. Prefer normalize_job_params()."""

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize CreateJobRequest.job_params for this job_type."""
        if self.params_schema is not None:
            return self.params_schema.model_validate(job_params).model_dump()
        return job_params

    def validate_normalized_job_params(self, job_params: dict[str, Any]) -> None:
        """Validate runtime prerequisites derived from already-normalized params."""

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        """Return fields required by the current executor/storage runtime."""
        raise NotImplementedError(f"{self.__class__.__name__}.runtime_job_fields() not implemented")

    def build_execution_plan(self, job: AIJob) -> JobPlan | None:
        """Build a custom execution plan. Return None to use text chunk planning."""
        return None

    def public_result(self, canonical_result: dict[str, Any]) -> dict[str, Any] | None:
        """Return the JobView.result value for a completed job."""
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
            raise ValueError(f"{self.job_type} succeeded result is required")
        if self.public_result_schema is None:
            raise ValueError(f"{self.job_type} public result must be null")
        return self.public_result_schema.model_validate(result).model_dump(exclude_none=True)


_registry: dict[str, WorkflowHandler] = {}


def register(handler: WorkflowHandler) -> None:
    if not handler.job_type:
        raise ValueError("workflow handler must declare job_type")
    _registry[handler.job_type] = handler


def get(job_type: str) -> WorkflowHandler:
    handler = _registry.get(job_type)
    if handler is None:
        raise KeyError(f"No workflow handler registered for job_type: {job_type!r}")
    return handler


def all_job_types() -> list[str]:
    return list(_registry.keys())


def validate_job_view_payload(payload: dict[str, Any]):
    from app.schemas.jobs import JobEnvelope

    job_view = JobEnvelope.model_validate(payload)
    handler = get(job_view.job_type)
    data = job_view.model_dump()
    if job_view.job_status == "succeeded":
        data["job_result"] = handler.validate_public_result(data["job_result"])
    return JobEnvelope.model_validate(data)
