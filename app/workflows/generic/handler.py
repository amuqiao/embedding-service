from __future__ import annotations

from typing import Any, TYPE_CHECKING

from pydantic import Field

from app.core.workflow_registry import WorkflowHandler, register
from app.schemas.common import StrictBaseModel
from app.services.job_planner import JobPlan, PlannedWorkItem
from app.services.job_runtime import job_params_from_job, work_item_payload

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.job import AIJob, AIJobWorkItem
    from app.schemas.jobs import JobResult


class GenericEchoParams(StrictBaseModel):
    value: Any
    label: str = Field(default="Echo Result", min_length=1, max_length=120)


class GenericEchoHandler(WorkflowHandler):
    job_type = "generic.echo"
    canvas_pattern = "single"
    chunking_enabled = False

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return GenericEchoParams.model_validate(job_params).model_dump()

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def build_execution_plan(self, job: AIJob) -> JobPlan:
        params = job_params_from_job(job)
        return JobPlan(
            execution_mode="single",
            chunk_count=1,
            chunk_registry=[{"chunk_index": 1, "kind": "echo"}],
            work_items=[
                PlannedWorkItem(
                    name=f"{self.job_type}.whole",
                    kind="whole",
                    chunk_index=0,
                    input_payload=params,
                )
            ],
        )

    async def execute_standard_item(
        self,
        item: AIJobWorkItem,
        job: AIJob,
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        from app.schemas.jobs import JobResult

        params = GenericEchoParams.model_validate(work_item_payload(item))
        return JobResult(
            artifacts=[
                {"key": "echo", "type": "json", "label": params.label, "content": params.value}
            ],
            signals={"echoed": True},
        ).model_dump()

    def parse_output(self, text: str) -> JobResult:
        raise NotImplementedError("generic.echo uses execute_standard_item instead of LLM parsing")


def register_all() -> None:
    register(GenericEchoHandler())
