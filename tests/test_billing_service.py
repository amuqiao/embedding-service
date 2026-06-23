from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.services import billing as billing_service
from app.services.billing import build_scope_billing_envelope


def _row(
    *,
    status: str = "succeeded",
    billable_status: str = "billable",
    cost_calculation_status: str = "estimated",
    cost_amount: Decimal | None = Decimal("0.00000000"),
    currency: str | None = "USD",
    usage_units: dict | None = None,
    pricing_ref: str | None = "openai:gpt-test@2026-06-23",
    completed_at: datetime | None = None,
):
    return SimpleNamespace(
        status=status,
        billable_status=billable_status,
        cost_calculation_status=cost_calculation_status,
        cost_amount=cost_amount,
        currency=currency,
        usage_units=usage_units if usage_units is not None else {},
        pricing_ref=pricing_ref,
        completed_at=completed_at,
    )


def test_scope_billing_without_ai_call_logs_is_not_billable():
    billing = build_scope_billing_envelope(scope_type="job", scope_id="job-1", rows=[])

    assert billing.scope_type == "job"
    assert billing.scope_id == "job-1"
    assert billing.status == "not_billable"
    assert billing.currency == "USD"
    assert billing.total_cost_amount == "0.00000000"
    assert billing.usage_units == {}
    assert billing.pricing_refs == []
    assert billing.ai_call_count == 0
    assert billing.billable_call_count == 0
    assert billing.unbillable_call_count == 0
    assert billing.failed_call_count == 0
    assert billing.diagnostic_reason is None
    assert billing.finalized_at is None


def test_scope_billing_without_ai_call_logs_uses_pricing_default_currency(monkeypatch):
    monkeypatch.setattr(billing_service, "default_currency", lambda: "EUR")

    billing = build_scope_billing_envelope(scope_type="job", scope_id="job-1", rows=[])

    assert billing.status == "not_billable"
    assert billing.currency == "EUR"


def test_scope_billing_sums_decimal_cost_usage_and_unique_pricing_refs():
    completed_first = datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc)
    completed_second = datetime(2026, 6, 23, 10, 1, tzinfo=timezone.utc)
    rows = [
        _row(
            cost_amount=Decimal("0.10000001"),
            usage_units={
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "output_tokens": 25,
            },
            pricing_ref="openai:gpt-b@2026-06-23",
            completed_at=completed_first,
        ),
        _row(
            cost_amount=Decimal("0.20000002"),
            usage_units={
                "input_tokens": 50,
                "cached_input_tokens": 10,
                "output_tokens": 5,
            },
            pricing_ref="openai:gpt-a@2026-06-23",
            completed_at=completed_second,
        ),
        _row(
            billable_status="not_billable",
            cost_calculation_status="not_applicable",
            cost_amount=None,
            usage_units={"input_tokens": 7, "ignored_negative": -1},
            pricing_ref="openai:gpt-a@2026-06-23",
            completed_at=completed_first,
        ),
    ]

    billing = build_scope_billing_envelope(scope_type="job", scope_id="job-2", rows=rows)

    assert billing.status == "estimated"
    assert billing.currency == "USD"
    assert billing.total_cost_amount == "0.30000003"
    assert billing.usage_units == {
        "cached_input_tokens": 50,
        "input_tokens": 157,
        "output_tokens": 30,
    }
    assert billing.pricing_refs == [
        "openai:gpt-a@2026-06-23",
        "openai:gpt-b@2026-06-23",
    ]
    assert billing.ai_call_count == 3
    assert billing.billable_call_count == 2
    assert billing.unbillable_call_count == 1
    assert billing.failed_call_count == 0
    assert billing.diagnostic_reason is None
    assert billing.finalized_at == completed_second


def test_scope_billing_with_pending_or_unknown_rows_is_incomplete():
    for row in (
        _row(status="pending", billable_status="pending"),
        _row(status="succeeded", billable_status="unknown"),
        _row(status="failed", billable_status="unknown", cost_calculation_status="not_applicable"),
    ):
        billing = build_scope_billing_envelope(scope_type="job", scope_id="job-3", rows=[row])

        assert billing.status == "incomplete"
        assert billing.diagnostic_reason == "contains_pending_or_unknown_ai_call"


def test_scope_billing_with_failed_cost_calculation_is_failed():
    billing = build_scope_billing_envelope(
        scope_type="job",
        scope_id="job-4",
        rows=[
            _row(
                status="failed",
                billable_status="billable",
                cost_calculation_status="failed",
                cost_amount=None,
                usage_units={"input_tokens": 100},
            )
        ],
    )

    assert billing.status == "failed"
    assert billing.diagnostic_reason == "cost_calculation_failed"
    assert billing.failed_call_count == 1
    assert billing.billable_call_count == 1


def test_scope_billing_with_billable_row_missing_currency_is_failed():
    billing = build_scope_billing_envelope(
        scope_type="job",
        scope_id="job-5",
        rows=[_row(billable_status="billable", cost_amount=Decimal("0.00010000"), currency=None)],
    )

    assert billing.status == "failed"
    assert billing.currency is None
    assert billing.diagnostic_reason == "billable_call_missing_currency"


@pytest.mark.asyncio
async def test_job_billing_can_return_billable_envelope_for_failed_job(monkeypatch):
    job_id = uuid.uuid4()

    async def fake_get_for_caller(_db, received_job_id, caller_id):
        assert received_job_id == job_id
        assert caller_id == "caller-1"
        return SimpleNamespace(id=job_id, status="failed")

    async def fake_list_for_scope(_db, *, scope_type, scope_id, caller_id):
        assert scope_type == "job"
        assert scope_id == str(job_id)
        assert caller_id == "caller-1"
        return [
            _row(
                status="succeeded",
                billable_status="billable",
                cost_calculation_status="estimated",
                cost_amount=Decimal("0.00012000"),
                usage_units={"input_tokens": 100, "output_tokens": 20},
                completed_at=datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc),
            )
        ]

    monkeypatch.setattr(billing_service.JobRepo, "get_for_caller", fake_get_for_caller)
    monkeypatch.setattr(billing_service.AiCallLogRepo, "list_for_scope", fake_list_for_scope)

    billing = await billing_service.get_job_billing(object(), job_id, "caller-1")

    assert billing.status == "estimated"
    assert billing.total_cost_amount == "0.00012000"
    assert billing.billable_call_count == 1
    assert billing.failed_call_count == 0
