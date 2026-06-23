from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import StrictBaseModel

BillingStatus = Literal["estimated", "not_billable", "incomplete", "failed"]
BillingKind = Literal["cost_estimate"]


class BillingEnvelope(StrictBaseModel):
    schema_version: Literal["1"] = "1"
    scope_type: str = Field(min_length=1, max_length=32)
    scope_id: str = Field(min_length=1, max_length=128)
    status: BillingStatus
    kind: BillingKind = "cost_estimate"
    currency: str | None = Field(default=None, max_length=8)
    total_cost_amount: str
    usage_units: dict[str, int]
    pricing_refs: list[str]
    ai_call_count: int = Field(ge=0)
    billable_call_count: int = Field(ge=0)
    unbillable_call_count: int = Field(ge=0)
    failed_call_count: int = Field(ge=0)
    diagnostic_reason: str | None = None
    finalized_at: datetime | None = None


class JobBillingResponseData(StrictBaseModel):
    billing: BillingEnvelope


class ScopeBillingResponseData(StrictBaseModel):
    billing: BillingEnvelope
