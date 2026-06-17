import copy
import json
import uuid
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.models.job import AIJob, AIJobWorkItem
from app.schemas.jobs import CreateJobRequest
from app.services.jobs import _validate_create_request
from app.workflows.short_drama_tagging.adapter import build_rs_tagging_payload
from app.workflows.short_drama_tagging.handler import InitialShortDramaTaggingHandler
from app.workflows.short_drama_tagging.rs_client import (
    FixtureTagSchemaProvider,
    assert_rs_write_accepted,
    normalize_tag_schema_response,
)
from app.workflows.short_drama_tagging.schemas import ShortDramaTaggingParams, TagSchemaTranslationParams
from app.workflows.short_drama_tagging.translation import parse_translation_output
from app.workflows.register import register_all_workflows

register_all_workflows()


class FakeDB:
    async def commit(self):
        pass


def fixture_schema() -> dict:
    return {
        "version": "v1.1",
        "generated_at": 1,
        "categories": [
            {
                "category_id": "000001",
                "name": "受众",
                "required": True,
                "min_items": 1,
                "max_items": 1,
                "labels": [
                    {
                        "label_id": "lbl-audience-female",
                        "name": "女频",
                        "definition": "女性受众。",
                    }
                ],
            },
            {
                "category_id": "000006",
                "name": "情绪",
                "required": True,
                "min_items": 1,
                "max_items": 2,
                "labels": [
                    {
                        "label_id": "lbl-emotion-abuse",
                        "name": "虐",
                        "definition": "压抑委屈。",
                    }
                ],
            },
        ],
        "mutual_exclusion_rules": [],
    }


def tagging_params() -> dict:
    return {
        "t_book_id": "300000000300000279",
        "work_context": {
            "title": "Title",
            "synopsis": "Synopsis",
            "subtitle_language": "zh",
            "series_structure": "continuous_series",
            "content_type": "短剧",
            "episode_count": 1,
        },
        "assets": [
            {
                "asset_type": "subtitle_srt",
                "episode_no": 1,
                "format": "srt",
                "text": "1\n00:00:01,000 --> 00:00:02,000\n她被误解。\n",
            }
        ],
    }


def test_short_drama_tagging_params_validate_business_language():
    params = ShortDramaTaggingParams.model_validate(tagging_params())
    assert params.work_context.subtitle_language == "zh"

    bad = tagging_params()
    bad["work_context"]["subtitle_language"] = "xx"
    with pytest.raises(Exception, match="unsupported business language"):
        ShortDramaTaggingParams.model_validate(bad)


@pytest.mark.asyncio
async def test_fixture_schema_provider_normalizes_rs_bundle(tmp_path):
    path = tmp_path / "schema.zh.json"
    path.write_text(json.dumps(fixture_schema(), ensure_ascii=False), encoding="utf-8")
    path_template = str(tmp_path / "schema.{lang}.json")

    bundle = await FixtureTagSchemaProvider(path_template).fetch("zh")

    assert bundle["tag_schema_snapshot"]["categories"][0]["category_id"] == "000001"
    assert bundle["mutual_exclusion_rules"] == []
    assert bundle["source"]["requested_language"] == "zh"

    with pytest.raises(AppError, match="fixture not found"):
        await FixtureTagSchemaProvider(path_template).fetch("en")


def test_rs_payload_adapter_uses_schema_text_and_keeps_partial_success():
    bundle = normalize_tag_schema_response(fixture_schema())
    payload, detail = build_rs_tagging_payload(
        t_book_id="300000000300000279",
        job_id="job-1",
        tag_schema=bundle["tag_schema_snapshot"],
        mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
        final_result={
            "selected_tags": {
                "000001": [
                    {
                        "标签名": "女频",
                        "权重": 0.9,
                        "打标原因": "剧情围绕女主受冤与成长展开。",
                    }
                ]
            },
            "tagging_detail": {"notes": []},
        },
    )

    assert payload["tags"]["000001"][0] == {
        "label_id": "lbl-audience-female",
        "name": "女频",
        "weight": 0.9,
        "reason": "剧情围绕女主受冤与成长展开。",
        "definition": "女性受众。",
    }
    assert payload["tags"]["000006"] == []
    assert detail["result_status"] == "partial_success"
    assert detail["validation_issues"][0]["issue"] == "missing_required_category"


def test_rs_payload_adapter_rejects_mutual_exclusion_conflicts():
    schema = fixture_schema()
    schema["categories"].append(
        {
            "category_id": "000003",
            "name": "题材",
            "required": True,
            "min_items": 1,
            "max_items": 3,
            "labels": [
                {"label_id": "lbl-topic-family", "name": "家庭伦理", "definition": "家庭冲突。"},
                {"label_id": "lbl-topic-thriller", "name": "惊悚灵异", "definition": "惊悚刺激。"},
            ],
        }
    )
    schema["mutual_exclusion_rules"] = [
        {"label_id": "lbl-topic-family", "mutex_label_ids": ["lbl-topic-thriller"]},
    ]
    bundle = normalize_tag_schema_response(schema)

    with pytest.raises(AppError, match="mutual exclusion"):
        build_rs_tagging_payload(
            t_book_id="300000000300000279",
            job_id="job-1",
            tag_schema=bundle["tag_schema_snapshot"],
            mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
            final_result={
                "selected_tags": {
                    "000001": [{"标签名": "女频", "权重": 0.9, "打标原因": "女主视角。"}],
                    "000006": [{"标签名": "虐", "权重": 0.7, "打标原因": "委屈压抑。"}],
                    "000003": [
                        {"标签名": "家庭伦理", "权重": 0.8, "打标原因": "家庭矛盾。"},
                        {"标签名": "惊悚灵异", "权重": 0.6, "打标原因": "惊悚桥段。"},
                    ],
                },
                "tagging_detail": {"notes": []},
            },
        )


def test_rs_write_response_requires_explicit_success_contract():
    assert_rs_write_accepted({"code": 0, "msg": "ok", "data": {}})

    for response in ({}, {"msg": "ok"}, {"code": 1, "msg": "bad", "data": {}}):
        with pytest.raises(AppError, match="RS"):
            assert_rs_write_accepted(response)


def test_short_drama_create_validation_uses_registered_handler(monkeypatch):
    payload = CreateJobRequest.model_validate(
        {"job_type": "short_drama.tagging.initial", "job_params": tagging_params()}
    )
    monkeypatch.setattr("app.services.jobs.get_enabled_model", lambda model_id: object())

    handler, normalized, runtime_fields = _validate_create_request(payload)

    assert isinstance(handler, InitialShortDramaTaggingHandler)
    assert normalized["work_context"]["subtitle_language"] == "zh"
    assert runtime_fields["model_id"]
    assert handler.public_result({"artifacts": [], "signals": {}}) is None


def test_short_drama_create_validation_checks_fixture_language_before_queue(monkeypatch, tmp_path):
    payload = tagging_params()
    payload["work_context"]["subtitle_language"] = "en"
    request = CreateJobRequest.model_validate(
        {"job_type": "short_drama.tagging.initial", "job_params": payload}
    )
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.settings.SHORT_DRAMA_RS_SCHEMA_SOURCE", "fixture")
    monkeypatch.setattr(
        "app.workflows.short_drama_tagging.handler.settings.SHORT_DRAMA_RS_SCHEMA_FIXTURE_PATH",
        str(tmp_path / "schema.{lang}.json"),
    )

    with pytest.raises(AppError, match="job_params does not match job_type schema"):
        _validate_create_request(request)


@pytest.mark.asyncio
async def test_short_drama_handler_writes_rs_and_returns_false_success_signal(monkeypatch):
    handler = InitialShortDramaTaggingHandler()
    job_id = uuid.uuid4()
    item_id = uuid.uuid4()
    job = AIJob(id=job_id, job_type="short_drama.tagging.initial")
    item = AIJobWorkItem(id=item_id, job_id=job_id, name="whole", kind="whole", chunk_index=0)
    bundle = normalize_tag_schema_response(fixture_schema())
    written = {}
    responses = iter(
        [
            {"t_book_id": "300000000300000279", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": "300000000300000279", "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            {
                "selected_tags": {
                    "000001": [
                        {
                            "标签名": "女频",
                            "权重": 0.8,
                            "打标原因": "女主视角突出。",
                        }
                    ]
                },
                "tagging_detail": {"notes": []},
            },
        ]
    )

    class Provider:
        async def fetch(self, language):
            assert language == "zh"
            return bundle

    class Writer:
        async def write(self, payload):
            written["payload"] = payload
            return {"code": 0, "msg": "ok", "data": {"saved": True}}

    async def fake_generate_text(model_id, messages):
        return SimpleNamespace(text=json.dumps(next(responses), ensure_ascii=False))

    async def fake_update_progress(*_args, **_kwargs):
        pass

    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.work_item_payload", lambda _item: tagging_params())
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.model_id_from_job", lambda _job: "fake")
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.get_tag_schema_provider", lambda: Provider())
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.get_tagging_result_writer", lambda: Writer())
    monkeypatch.setattr("app.integrations.ai_gateway.generate_text", fake_generate_text)
    monkeypatch.setattr("app.repositories.job_repo.JobRepo.update_progress", fake_update_progress)

    result = await handler.execute_standard_item(item, job, FakeDB())

    assert written["payload"]["job_id"] == str(job_id)
    assert written["payload"]["tags"]["000006"] == []
    assert result["signals"]["success"] is False
    assert result["signals"]["result_status"] == "partial_success"
    assert result["signals"]["rs_write_accepted"] is True


def test_translation_job_params_are_object_and_ordered():
    params = {
        "source_language": "zh",
        "target_languages": ["en", "es", "pt"],
        "source_schema": {"categories": fixture_schema()["categories"]},
        "source_mutual_exclusion_rules": [],
    }
    assert TagSchemaTranslationParams.model_validate(params).target_languages == ["en", "es", "pt"]

    with pytest.raises(Exception, match="business language order"):
        TagSchemaTranslationParams.model_validate({**params, "target_languages": ["pt", "en"]})

    with pytest.raises(Exception):
        TagSchemaTranslationParams.model_validate([
            {"source_language": "zh", "target_languages": ["en"]}
        ])


def test_translation_output_preserves_non_translated_schema_fields():
    params = {
        "source_language": "zh",
        "target_languages": ["en"],
        "source_schema": {
            "categories": [
                {
                    "category_id": "000001",
                    "name": "受众",
                    "required": True,
                    "min_items": 1,
                    "max_items": 1,
                    "labels": [
                        {
                            "label_id": "lbl-audience-male",
                            "label_key": "audience_male",
                            "name": "男频",
                            "definition": "男性受众。",
                        }
                    ],
                }
            ]
        },
        "source_mutual_exclusion_rules": [],
    }
    translated_schema = copy.deepcopy(params["source_schema"])
    translated_schema["language"] = "en"
    translated_schema["categories"][0]["name"] = "Audience"
    translated_schema["categories"][0]["labels"][0]["name"] = "Male-oriented"
    translated_schema["categories"][0]["labels"][0]["definition"] = "Male audience."

    result = parse_translation_output(
        json.dumps({"translated_schemas": [translated_schema]}, ensure_ascii=False),
        params,
    )

    assert result["artifacts"][0]["content"][0]["categories"][0]["labels"][0]["label_key"] == "audience_male"

    changed_cardinality = copy.deepcopy(translated_schema)
    changed_cardinality["categories"][0]["max_items"] = 2
    with pytest.raises(AppError, match="category field changed"):
        parse_translation_output(json.dumps({"translated_schemas": [changed_cardinality]}), params)

    missing_label_key = copy.deepcopy(translated_schema)
    del missing_label_key["categories"][0]["labels"][0]["label_key"]
    with pytest.raises(AppError, match="label keys changed"):
        parse_translation_output(json.dumps({"translated_schemas": [missing_label_key]}), params)
