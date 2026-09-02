import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.business_packages.tagged_text_translation.executor import (
    TaggedTextTranslationJob,
    _build_result,
    _parse_model_json,
)
from app.models.job import Job
from app.business_packages.tagged_text_translation.schemas import TaggedTextTranslationParams
from app.schemas.jobs import CreateJobRequest
from app.services.jobs import validate_create_contract
from app.services.job_runtime import build_runtime_snapshot, payload_hash, write_runtime_json


def test_tagged_text_translation_params_accept_shared_language_codes():
    params = TaggedTextTranslationParams.model_validate(
        {
            "source_language": "en",
            "target_language": "zh",
            "items": [
                {
                    "id": "homepage.title",
                    "text": "<span>Hello {user_name}, welcome back!</span>",
                    "max_target_chars_hint": 30,
                }
            ],
        }
    )

    assert params.source_language == "en"
    assert params.target_language == "zh"
    assert params.items[0].id == "homepage.title"


def test_tagged_text_translation_normalizer_projects_invalid_params_to_contract_error():
    handler = TaggedTextTranslationJob()

    with pytest.raises(AppError) as exc:
        handler.normalize_job_params(
            {
                "target_language": "zh",
                "items": [
                    {"id": "dup", "text": "Hello"},
                    {"id": "dup", "text": "World"},
                ],
            }
        )

    assert exc.value.code == "INVALID_JOB_PARAMS"
    assert exc.value.details["job_type"] == "tagged_text_translation"


def test_tagged_text_translation_rejects_text_over_configured_default_limit():
    handler = TaggedTextTranslationJob()

    with pytest.raises(AppError) as exc:
        handler.normalize_job_params(
            {
                "target_language": "zh",
                "items": [{"id": "long", "text": "a" * 201}],
            }
        )

    assert exc.value.code == "INVALID_JOB_PARAMS"
    assert exc.value.details["field"] == "job_params.items[0].text"
    assert exc.value.details["max_text_length"] == 200
    assert exc.value.details["text_length"] == 201


def test_tagged_text_translation_rejects_text_over_custom_configured_limit(monkeypatch):
    import app.business_packages.tagged_text_translation.executor as executor

    monkeypatch.setattr(
        executor,
        "settings",
        SimpleNamespace(
            job=SimpleNamespace(
                tagged_text_translation=SimpleNamespace(
                    max_items=100,
                    max_text_length=5,
                    max_total_text_length=20_000,
                ),
            )
        ),
    )

    with pytest.raises(AppError) as exc:
        TaggedTextTranslationJob().normalize_job_params(
            {
                "target_language": "zh",
                "items": [{"id": "long", "text": "123456"}],
            }
        )

    assert exc.value.code == "INVALID_JOB_PARAMS"
    assert exc.value.details["field"] == "job_params.items[0].text"
    assert exc.value.details["max_text_length"] == 5
    assert exc.value.details["text_length"] == 6


def test_tagged_text_translation_rejects_items_over_configured_limit(monkeypatch):
    import app.business_packages.tagged_text_translation.executor as executor

    monkeypatch.setattr(
        executor,
        "settings",
        SimpleNamespace(
            job=SimpleNamespace(
                tagged_text_translation=SimpleNamespace(
                    max_items=1,
                    max_text_length=200,
                    max_total_text_length=20_000,
                ),
            )
        ),
    )

    with pytest.raises(AppError) as exc:
        TaggedTextTranslationJob().normalize_job_params(
            {
                "target_language": "zh",
                "items": [
                    {"id": "one", "text": "one"},
                    {"id": "two", "text": "two"},
                ],
            }
        )

    assert exc.value.code == "INVALID_JOB_PARAMS"
    assert exc.value.details["field"] == "job_params.items"
    assert exc.value.details["max_items"] == 1
    assert exc.value.details["item_count"] == 2


def test_tagged_text_translation_rejects_total_text_over_configured_limit(monkeypatch):
    import app.business_packages.tagged_text_translation.executor as executor

    monkeypatch.setattr(
        executor,
        "settings",
        SimpleNamespace(
            job=SimpleNamespace(
                tagged_text_translation=SimpleNamespace(
                    max_items=100,
                    max_text_length=200,
                    max_total_text_length=10,
                ),
            )
        ),
    )

    with pytest.raises(AppError) as exc:
        TaggedTextTranslationJob().normalize_job_params(
            {
                "target_language": "zh",
                "items": [
                    {"id": "one", "text": "12345"},
                    {"id": "two", "text": "123456"},
                ],
            }
        )

    assert exc.value.code == "INVALID_JOB_PARAMS"
    assert exc.value.details["field"] == "job_params.items[].text"
    assert exc.value.details["max_total_text_length"] == 10
    assert exc.value.details["total_text_length"] == 11


def test_tagged_text_translation_create_contract_preserves_config_limit_error_code():
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "translate-too-long",
            "job_type": "tagged_text_translation",
            "job_params": {
                "target_language": "zh",
                "items": [{"id": "long", "text": "a" * 201}],
            },
        }
    )

    with pytest.raises(AppError) as exc:
        validate_create_contract(payload)

    assert exc.value.code == "INVALID_JOB_PARAMS"
    assert exc.value.details["field"] == "job_params.items[0].text"


def test_tagged_text_translation_runtime_fields_omit_empty_system(monkeypatch):
    route_hash = "sha256:" + "a" * 64
    monkeypatch.setattr("app.business_packages.tagged_text_translation.executor.resolve_route_config_hash", lambda **_kwargs: route_hash)
    fields = TaggedTextTranslationJob().runtime_job_fields(
        {
            "target_language": "zh",
            "items": [{"id": "title", "text": "Hello"}],
        }
    )

    assert fields["operation"] == "tagged_text_translation"
    assert isinstance(fields["model_id"], str)
    assert fields["model_id"]
    assert fields["model_route_config_hash"] == route_hash
    assert "system" not in fields
    assert "_system" not in fields


def test_tagged_text_translation_result_preserves_item_order_tags_and_placeholders():
    params = TaggedTextTranslationParams.model_validate(
        {
            "source_language": "en",
            "target_language": "zh",
            "items": [
                {"id": "a", "text": "<span>Hello {user_name}</span>", "max_target_chars_hint": 3},
                {"id": "b", "text": "<p>Your order {{order_id}} is ready.</p>"},
            ],
        }
    )
    result = _build_result(
        params,
        {
            "source_language": "en",
            "target_language": "zh",
            "items": [
                {"id": "b", "translated_text": "<p>你的订单 {{order_id}} 已准备好。</p>"},
                {"id": "a", "translated_text": "<span>你好 {user_name}</span>"},
            ],
        },
    )

    assert [item.id for item in result.items] == ["a", "b"]
    assert result.items[0].translated_text == "<span>你好 {user_name}</span>"
    assert result.items[0].char_count.source == len("Hello ")
    assert result.items[0].char_count.target == len("你好 ")
    assert result.items[0].char_count.within_hint is True
    assert result.items[1].char_count.target_limit_hint is None
    assert result.items[1].char_count.within_hint is None


def test_tagged_text_translation_rejects_model_output_that_changes_placeholder():
    params = TaggedTextTranslationParams.model_validate(
        {
            "target_language": "zh",
            "items": [{"id": "title", "text": "<span>Hello {user_name}</span>"}],
        }
    )

    with pytest.raises(AppError) as exc:
        _build_result(
            params,
            {
                "source_language": "en",
                "target_language": "zh",
                "items": [{"id": "title", "translated_text": "<span>你好 {username}</span>"}],
            },
        )

    assert exc.value.code == "MODEL_OUTPUT_INVALID"


def test_tagged_text_translation_rejects_output_that_changes_tag_scope():
    params = TaggedTextTranslationParams.model_validate(
        {
            "target_language": "zh",
            "items": [{"id": "title", "text": "<b>Hello</b> world"}],
        }
    )

    with pytest.raises(AppError) as exc:
        _build_result(
            params,
            {
                "source_language": "en",
                "target_language": "zh",
                "items": [{"id": "title", "translated_text": "你好 <b>世界</b>"}],
            },
        )

    assert exc.value.code == "MODEL_OUTPUT_INVALID"


def test_tagged_text_translation_rejects_model_output_item_set_mismatch():
    params = TaggedTextTranslationParams.model_validate(
        {
            "target_language": "zh",
            "items": [
                {"id": "a", "text": "Hello"},
                {"id": "b", "text": "World"},
            ],
        }
    )

    with pytest.raises(AppError) as exc:
        _build_result(
            params,
            {
                "source_language": "en",
                "target_language": "zh",
                "items": [{"id": "a", "translated_text": "你好"}],
            },
        )

    assert exc.value.code == "MODEL_OUTPUT_INVALID"


def test_tagged_text_translation_rejects_model_output_target_language_mismatch():
    params = TaggedTextTranslationParams.model_validate(
        {
            "target_language": "zh",
            "items": [{"id": "title", "text": "Hello"}],
        }
    )

    with pytest.raises(AppError) as exc:
        _build_result(
            params,
            {
                "source_language": "en",
                "target_language": "ja",
                "items": [{"id": "title", "translated_text": "你好"}],
            },
        )

    assert exc.value.code == "MODEL_OUTPUT_INVALID"


def test_tagged_text_translation_rejects_model_output_unsupported_source_language():
    params = TaggedTextTranslationParams.model_validate(
        {
            "target_language": "zh",
            "items": [{"id": "title", "text": "Hello"}],
        }
    )

    with pytest.raises(AppError) as exc:
        _build_result(
            params,
            {
                "source_language": "xx",
                "target_language": "zh",
                "items": [{"id": "title", "translated_text": "你好"}],
            },
        )

    assert exc.value.code == "MODEL_OUTPUT_INVALID"


def test_tagged_text_translation_rejects_non_json_model_output():
    with pytest.raises(AppError) as exc:
        _parse_model_json("not json")

    assert exc.value.code == "MODEL_OUTPUT_INVALID"


def _job(params: dict, runtime_fields: dict) -> Job:
    job_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    params_hash = payload_hash(params)
    return Job(
        id=job_id,
        caller_id="caller-1",
        client_request_id="client-translate-1",
        job_type="tagged_text_translation",
        status="running",
        progress_percent=30,
        progress_stage="calling_model",
        active_attempt_id=attempt_id,
        job_params_ref=write_runtime_json(None, "job_params", params),
        job_params_hash=params_hash,
        runtime_ref=write_runtime_json(
            None,
            "runtime",
            build_runtime_snapshot(
                job_type="tagged_text_translation",
                job_params_hash=params_hash,
                runtime_fields=runtime_fields,
                output_target={
                    "type": "oss_prefix",
                    "oss_bucket": "local-dev",
                    "oss_prefix": f"{job_id}/",
                    "oss_region": "local",
                },
            ),
        ),
        created_at=datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_tagged_text_translation_executor_calls_text_ledger(monkeypatch):
    params = {
        "target_language": "zh",
        "items": [{"id": "title", "text": "<span>Hello {user_name}</span>"}],
    }
    job = _job(
        params,
        {
            "operation": "tagged_text_translation",
            "model_id": "gpt-5.5",
            "model_route_config_hash": "sha256:" + "a" * 64,
        },
    )
    captured = {}

    async def fake_generate_text_with_ledger(**kwargs):
        captured.update(kwargs)
        return type(
            "Result",
            (),
            {
                "text": (
                    '{"source_language":"en","target_language":"zh","items":'
                    '[{"id":"title","translated_text":"<span>你好 {user_name}</span>"}]}'
                )
            },
        )()

    monkeypatch.setattr(
        "app.business_packages.tagged_text_translation.executor.generate_text_with_ledger",
        fake_generate_text_with_ledger,
    )

    result = await TaggedTextTranslationJob()._execute(job, object())

    assert captured["operation"] == "tagged_text_translation.translate"
    assert captured["step_name"] == "calling_model"
    assert captured["job_type"] == "tagged_text_translation"
    assert captured["model_id"] == "gpt-5.5"
    assert captured["attempt_id"] == job.active_attempt_id
    assert result["items"][0]["id"] == "title"
    assert result["items"][0]["translated_text"] == "<span>你好 {user_name}</span>"
