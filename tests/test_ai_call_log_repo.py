import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.repositories.ai_call_log_repo import AiCallLogRepo


class _ScalarList:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarList(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.flushed = False
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _RowsResult(self.rows)

    async def flush(self):
        self.flushed = True


def _pending_row():
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="pending",
        failure_phase=None,
        error_code=None,
        error_message=None,
        billable_status="pending",
        cost_calculation_status="pending",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        completed_at=None,
        duration_ms=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_mark_stale_pending_failed_selects_stale_pending_rows_and_marks_failed_unknown():
    row = _pending_row()
    db = _FakeDB([row])

    count = await AiCallLogRepo.mark_stale_pending_failed(
        db,
        before=datetime.now(timezone.utc) - timedelta(minutes=5),
        limit=100,
    )

    assert count == 1
    assert db.flushed is True
    assert row.status == "failed"
    assert row.failure_phase == "recovery"
    assert row.error_code == "AI_CALL_PENDING_TIMEOUT"
    assert row.billable_status == "unknown"
    assert row.cost_calculation_status == "not_applicable"
    assert row.completed_at is not None
    assert row.duration_ms >= 0
    assert row.updated_at == row.completed_at
    statement_text = str(db.statement)
    assert "ai_call_ledger_entries.status" in statement_text
    assert "ai_call_ledger_entries.created_at" in statement_text
    assert "<=" in statement_text
    assert "FOR UPDATE" in statement_text


@pytest.mark.asyncio
async def test_mark_stale_pending_failed_without_rows_does_not_flush():
    db = _FakeDB([])

    count = await AiCallLogRepo.mark_stale_pending_failed(
        db,
        before=datetime.now(timezone.utc),
        limit=100,
    )

    assert count == 0
    assert db.flushed is False
