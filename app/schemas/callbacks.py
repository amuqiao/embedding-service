from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field
from pydantic import model_validator

from app.schemas.common import StrictBaseModel
from app.schemas.jobs import JobEnvelope


class CallbackEnvelope(StrictBaseModel):
    event: Literal["job.succeeded", "job.failed"]
    event_id: UUID
    attempt: int = Field(ge=1)
    sent_at: datetime
    trigger_request_id: str | None = None
    caller_id: str
    job: JobEnvelope

    @model_validator(mode="after")
    def validate_event_matches_job(self):
        if self.job.job_status not in {"succeeded", "failed"}:
            raise ValueError("callback job must be terminal")
        expected = "job.succeeded" if self.job.job_status == "succeeded" else "job.failed"
        if self.event != expected:
            raise ValueError("callback event must match job status")
        return self


class CallbackResponseEnvelope(StrictBaseModel):
    accepted: bool = True
    msg: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
