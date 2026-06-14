from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.job import AIJob, AIJobWorkItem
    from app.schemas.jobs import JobResult

CanvasPattern = str  # "single" | "memory_fanout" | "plain_chord" | "scan_chord"


class WorkflowHandler:
    """
    Base class for all workflow handlers. Subclass to add a new job type.

    Steps:
    1. Subclass WorkflowHandler, set job_type, canvas_pattern, and chunking config
    2. Implement parse_output(), merge_chunks(), and execute_special_item() if needed
    3. Call workflow_registry.register(MyHandler()) in your module's register_all()
    4. Call register_all() in app/main.py startup
    """

    job_type: str = ""
    canvas_pattern: CanvasPattern = "single"
    chunking_enabled: bool = False
    max_single_chars: int = 20000
    chunk_size: int = 3000
    # Keys of artifacts whose content should be written to OSS (large outputs).
    # Artifacts not in this set keep their content inline in result_payload.
    large_artifact_keys: frozenset[str] = frozenset()

    def parse_output(self, text: str) -> JobResult:
        """Parse raw LLM output into JobResult. Required for all handlers."""
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

    def validate_extra(self, extra: dict[str, Any] | None) -> None:
        """Validate legacy job-type-specific extra params. Prefer normalize_job_params()."""

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize CreateJobRequest.job_params for this job_type."""
        return job_params

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        """Return fields required by the current executor/storage runtime."""
        raise NotImplementedError(f"{self.__class__.__name__}.runtime_job_fields() not implemented")


_registry: dict[str, WorkflowHandler] = {}


def register(handler: WorkflowHandler) -> None:
    _registry[handler.job_type] = handler


def get(job_type: str) -> WorkflowHandler:
    handler = _registry.get(job_type)
    if handler is None:
        raise KeyError(f"No workflow handler registered for job_type: {job_type!r}")
    return handler


def all_job_types() -> list[str]:
    return list(_registry.keys())
