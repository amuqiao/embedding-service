import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.integrations.ai_gateway import TextGenerationResult
from app.services import ai_gateway_facade


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _FakeSessionFactory:
    def __init__(self):
        self.sessions: list[_FakeDB] = []

    def __call__(self):
        session = _FakeDB()
        self.sessions.append(session)
        return _FakeSessionContext(session)


class _FakeSessionContext:
    def __init__(self, session: _FakeDB):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        id="custom-model",
        provider="openai",
        provider_model="custom-model",
        litellm_model="openai/custom-model",
        pricing_ref="openai:custom-model@2026-06-23",
        temperature=0.2,
        num_retries=1,
        drop_params=False,
    )


def _price() -> SimpleNamespace:
    return SimpleNamespace(
        ref="openai:custom-model@2026-06-23",
        version="2026-06-23",
        currency="USD",
        input_per_1m=Decimal("1.00"),
        cached_input_per_1m=Decimal("0.10"),
        output_per_1m=Decimal("2.00"),
    )


async def _call(session_factory: _FakeSessionFactory):
    return await ai_gateway_facade.generate_text_with_ledger(
        caller_id="caller-1",
        scope_type="job",
        scope_id="00000000-0000-0000-0000-000000000001",
        operation="job_type.execute",
        model_id="custom-model",
        messages=[{"role": "user", "content": "hello"}],
        job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        job_type="job_type",
        ledger_session_factory=session_factory,
    )


@pytest.mark.asyncio
async def test_gateway_does_not_call_provider_when_pending_ledger_write_fails(monkeypatch):
    session_factory = _FakeSessionFactory()

    async def fail_create_pending(*_args, **_kwargs):
        raise RuntimeError("ledger unavailable")

    async def fail_generate_text(*_args, **_kwargs):
        raise AssertionError("provider must not be called before pending ledger row exists")

    monkeypatch.setattr(ai_gateway_facade, "_require_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_gateway_facade, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_gateway_facade.AiCallLogRepo, "create_pending", fail_create_pending)
    monkeypatch.setattr(ai_gateway_facade, "generate_text", fail_generate_text)

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await _call(session_factory)

    assert [session.commits for session in session_factory.sessions] == [0]


@pytest.mark.asyncio
async def test_gateway_marks_usage_missing_as_failed_unknown_without_zero_cost(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        return TextGenerationResult(text="ok", prompt_tokens=None, completion_tokens=None, usage=None)

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("usage missing must not be marked succeeded")

    monkeypatch.setattr(ai_gateway_facade, "_require_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_gateway_facade, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_gateway_facade.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_gateway_facade.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_gateway_facade.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)
    monkeypatch.setattr(ai_gateway_facade, "generate_text", fake_generate_text)

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "MODEL_USAGE_MISSING"
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "usage"
    assert recorded["billable_status"] == "unknown"
    assert recorded["cost_calculation_status"] == "failed"
    assert [session.commits for session in session_factory.sessions] == [1, 1]


@pytest.mark.asyncio
async def test_gateway_freezes_usage_and_cost_on_success(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        return TextGenerationResult(
            text=" ok ",
            prompt_tokens=1000,
            completion_tokens=500,
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
        )

    async def fake_mark_succeeded(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    monkeypatch.setattr(ai_gateway_facade, "_require_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_gateway_facade, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_gateway_facade.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_gateway_facade.AiCallLogRepo, "mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr(ai_gateway_facade, "generate_text", fake_generate_text)

    result = await _call(session_factory)

    assert result.text == " ok "
    assert recorded["call_id"] == call_id
    assert recorded["usage_units"] == {
        "input_tokens": 1000,
        "cached_input_tokens": 0,
        "output_tokens": 500,
        "total_tokens": 1500,
    }
    assert recorded["cost_amount"] == Decimal("0.00200000")
    assert recorded["currency"] == "USD"
    assert [session.commits for session in session_factory.sessions] == [1, 1]
