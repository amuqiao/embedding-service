import copy
import json
import uuid
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.models.job import AIJob, AIJobWorkItem
from app.schemas.jobs import CreateJobRequest
from app.services.jobs import _validate_create_request
from app.workflows.short_drama_tagging.adapter import (
    RS_AI_TAG_RESULTS_ARTIFACT_KEY,
    build_rs_ai_tag_results_payload,
    build_rs_tagging_payload,
    rs_ai_tag_results_payload_from_canonical_result,
)
from app.workflows.short_drama_tagging.handler import InitialShortDramaTaggingHandler
from app.workflows.short_drama_tagging.prompts import stage_messages
from app.workflows.short_drama_tagging.rs_client import (
    FixtureTagSchemaProvider,
    HttpTagSchemaProvider,
    HttpTaggingResultWriter,
    MockTaggingResultWriter,
    assert_rs_write_accepted,
    get_tag_schema_provider,
    get_tagging_result_writer,
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
async def test_mock_schema_provider_normalizes_rs_bundle(tmp_path):
    path = tmp_path / "schema.zh.json"
    path.write_text(json.dumps(fixture_schema(), ensure_ascii=False), encoding="utf-8")
    path_template = str(tmp_path / "schema.{lang}.json")

    bundle = await FixtureTagSchemaProvider(path_template).fetch("zh")

    assert bundle["tag_schema_snapshot"]["categories"][0]["category_id"] == "000001"
    assert bundle["mutual_exclusion_rules"] == []
    assert bundle["source"]["requested_language"] == "zh"

    with pytest.raises(AppError, match="mock tag schema not found"):
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


def test_short_drama_prompts_render_human_readable_rules_without_ids():
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
        {"label_id": "lbl-topic-thriller", "mutex_label_ids": ["lbl-topic-family"]},
    ]
    bundle = normalize_tag_schema_response(schema)
    artifacts = {
        "story_overview_result": {
            "t_book_id": "300000000300000279",
            "analysis_status": "ok",
            "characters": [],
            "world_setting": {},
            "plot_timeline": [],
            "main_conflicts": [],
            "uncertainties": [],
        },
        "candidate_tags": {
            "t_book_id": "300000000300000279",
            "category_decisions": [
                {
                    "category_id": "candidate-category-id",
                    "category_name": "题材",
                    "label_id": "candidate-label-id",
                    "标签名": "家庭伦理",
                    "definition": "candidate definition",
                }
            ],
            "raw_candidates": [
                {
                    "mutex_label_ids": ["candidate-mutex-id"],
                    "labels": [{"label_id": "nested-candidate-label-id", "name": "惊悚灵异"}],
                }
            ],
            "uncertainties": [],
        },
    }

    candidate_prompt = stage_messages(
        "candidate_tagging",
        job_params=tagging_params(),
        tag_schema=bundle["tag_schema_snapshot"],
        mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
        artifacts={"story_overview_result": artifacts["story_overview_result"]},
    )[0]["content"]
    finalize_prompt = stage_messages(
        "finalize",
        job_params=tagging_params(),
        tag_schema=bundle["tag_schema_snapshot"],
        mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
        artifacts=artifacts,
    )[0]["content"]

    for prompt in (candidate_prompt, finalize_prompt):
        assert "家庭伦理" in prompt
        assert "惊悚灵异" in prompt
        assert "do_not_select_together" in prompt
        assert "lbl-topic-family" not in prompt
        assert "lbl-topic-thriller" not in prompt
        assert "label_id" not in prompt
        assert "mutex_label_ids" not in prompt
        assert '"000003"' not in prompt
        assert "category_id" not in prompt
        assert "candidate-category-id" not in prompt
        assert "candidate-label-id" not in prompt
        assert "candidate-mutex-id" not in prompt
        assert "nested-candidate-label-id" not in prompt
        assert "candidate definition" not in prompt


def test_rs_payload_adapter_accepts_category_names_and_resolves_ids():
    bundle = normalize_tag_schema_response(fixture_schema())
    payload, detail = build_rs_tagging_payload(
        t_book_id="300000000300000279",
        job_id="job-1",
        tag_schema=bundle["tag_schema_snapshot"],
        mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
        final_result={
            "selected_tags": {
                "受众": [{"标签名": "女频", "权重": 1, "打标原因": "女性主角。"}],
                "情绪": [{"标签名": "虐", "权重": 0.8, "打标原因": "压抑委屈。"}],
            },
            "tagging_detail": {"notes": []},
        },
    )

    assert payload["tags"]["000001"][0]["label_id"] == "lbl-audience-female"
    assert payload["tags"]["000006"][0]["label_id"] == "lbl-emotion-abuse"
    assert "受众" not in payload["tags"]
    assert detail["result_status"] == "success"


def test_rs_payload_adapter_rejects_unknown_category_name():
    bundle = normalize_tag_schema_response(fixture_schema())

    with pytest.raises(AppError, match="unknown category"):
        build_rs_tagging_payload(
            t_book_id="300000000300000279",
            job_id="job-1",
            tag_schema=bundle["tag_schema_snapshot"],
            mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
            final_result={
                "selected_tags": {
                    "不存在分类": [{"标签名": "女频", "权重": 1, "打标原因": "女性主角。"}],
                },
                "tagging_detail": {"notes": []},
            },
        )


def test_rs_payload_adapter_rejects_duplicate_category_keys():
    bundle = normalize_tag_schema_response(fixture_schema())

    with pytest.raises(AppError, match="duplicate category"):
        build_rs_tagging_payload(
            t_book_id="300000000300000279",
            job_id="job-1",
            tag_schema=bundle["tag_schema_snapshot"],
            mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
            final_result={
                "selected_tags": {
                    "000001": [{"标签名": "女频", "权重": 1, "打标原因": "女性主角。"}],
                    "受众": [{"标签名": "女频", "权重": 0.8, "打标原因": "重复分类。"}],
                    "情绪": [{"标签名": "虐", "权重": 0.8, "打标原因": "压抑委屈。"}],
                },
                "tagging_detail": {"notes": []},
            },
        )


def test_rs_payload_adapter_rejects_unknown_label_name():
    bundle = normalize_tag_schema_response(fixture_schema())

    with pytest.raises(AppError, match="label name is not in schema"):
        build_rs_tagging_payload(
            t_book_id="300000000300000279",
            job_id="job-1",
            tag_schema=bundle["tag_schema_snapshot"],
            mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
            final_result={
                "selected_tags": {
                    "受众": [{"标签名": "女频", "权重": 1, "打标原因": "女性主角。"}],
                    "情绪": [{"标签名": "不存在标签", "权重": 0.8, "打标原因": "错误标签。"}],
                },
                "tagging_detail": {"notes": []},
            },
        )


def test_rs_payload_adapter_rejects_unsupported_selected_tag_fields():
    bundle = normalize_tag_schema_response(fixture_schema())

    with pytest.raises(AppError, match="unsupported fields") as exc_info:
        build_rs_tagging_payload(
            t_book_id="300000000300000279",
            job_id="job-1",
            tag_schema=bundle["tag_schema_snapshot"],
            mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
            final_result={
                "selected_tags": {
                    "受众": [
                        {
                            "标签名": "女频",
                            "权重": 1,
                            "打标原因": "女性主角。",
                            "label_id": "lbl-audience-female",
                            "category_id": "000001",
                            "definition": "女性受众。",
                            "标签释义": "女性受众。",
                        }
                    ],
                    "情绪": [{"标签名": "虐", "权重": 0.8, "打标原因": "压抑委屈。"}],
                },
                "tagging_detail": {"notes": []},
            },
        )

    assert exc_info.value.details["field_path"] == "selected_tags.000001[0]"
    assert exc_info.value.details["fields"] == ["category_id", "definition", "label_id", "标签释义"]


def test_rs_payload_adapter_keeps_quantity_issues_as_partial_success():
    schema = fixture_schema()
    schema["categories"][1]["max_items"] = 1
    schema["categories"][1]["labels"].append(
        {
            "label_id": "lbl-emotion-revenge",
            "name": "爽",
            "definition": "畅快反击。",
        }
    )
    bundle = normalize_tag_schema_response(schema)

    below_payload, below_detail = build_rs_tagging_payload(
        t_book_id="300000000300000279",
        job_id="job-1",
        tag_schema=bundle["tag_schema_snapshot"],
        mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
        final_result={
            "selected_tags": {
                "受众": [{"标签名": "女频", "权重": 1, "打标原因": "女性主角。"}],
                "情绪": [],
            },
            "tagging_detail": {"notes": []},
        },
    )
    above_payload, above_detail = build_rs_tagging_payload(
        t_book_id="300000000300000279",
        job_id="job-1",
        tag_schema=bundle["tag_schema_snapshot"],
        mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
        final_result={
            "selected_tags": {
                "受众": [{"标签名": "女频", "权重": 1, "打标原因": "女性主角。"}],
                "情绪": [
                    {"标签名": "虐", "权重": 0.8, "打标原因": "压抑委屈。"},
                    {"标签名": "爽", "weight": 0.7, "reason": "畅快反击。"},
                ],
            },
            "tagging_detail": {"notes": []},
        },
    )

    assert below_payload["tags"]["000006"] == []
    assert below_detail["result_status"] == "partial_success"
    assert below_detail["validation_issues"][0]["issue"] == "below_min_items"
    assert above_payload["tags"]["000006"][0]["label_id"] == "lbl-emotion-abuse"
    assert above_payload["tags"]["000006"][1]["label_id"] == "lbl-emotion-revenge"
    assert above_detail["result_status"] == "partial_success"
    assert above_detail["validation_issues"][0]["issue"] == "above_max_items"


def test_rs_ai_tag_results_adapter_builds_compatibility_payload():
    schema = fixture_schema()
    schema.pop("version")
    bundle = normalize_tag_schema_response(schema)
    payload, detail = build_rs_ai_tag_results_payload(
        t_book_id="300000000300000279",
        job_id="0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
        tag_schema_version="v1.1",
        tag_schema=bundle["tag_schema_snapshot"],
        mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
        final_result={
            "selected_tags": {
                "000001": [{"标签名": "女频", "权重": 1, "打标原因": "剧情以女主视角展开。"}],
                "000006": [{"标签名": "虐", "权重": 0.9, "打标原因": "女主遭受冤屈羞辱。"}],
            },
            "tagging_detail": {"notes": []},
        },
    )

    assert payload["status"] == "success"
    assert payload["msg"] is None
    assert payload["tag_schema_version"] == "v1.1"
    assert payload["tags"]["000001"][0]["label_id"] == "lbl-audience-female"
    assert detail["result_status"] == "success"


def runtime_fields(**overrides) -> dict:
    fields = {
        "model_id": "fake",
        "rs_schema_mock_enabled": True,
        "rs_result_mock_enabled": True,
        "rs_base_url": "",
        "rs_timeout_seconds": 10,
        "rs_schema_mock_path": "mock/short_drama_tagging/tag_schema_snapshot.{lang}.json",
        "rs_result_response_mock_path": "mock/short_drama_tagging/rs_write_result_response.success.json",
        "rs_tag_schema_version": "v1.1",
    }
    fields.update(overrides)
    return fields


def test_rs_runtime_factories_support_split_mock_modes():
    schema_mock_result_http = runtime_fields(
        rs_schema_mock_enabled=True,
        rs_result_mock_enabled=False,
        rs_base_url="https://rs.example.com/",
    )
    assert isinstance(get_tag_schema_provider(schema_mock_result_http), FixtureTagSchemaProvider)
    assert isinstance(get_tagging_result_writer(schema_mock_result_http), HttpTaggingResultWriter)

    schema_http_result_mock = runtime_fields(
        rs_schema_mock_enabled=False,
        rs_result_mock_enabled=True,
        rs_base_url="https://rs.example.com/",
    )
    assert isinstance(get_tag_schema_provider(schema_http_result_mock), HttpTagSchemaProvider)
    assert isinstance(get_tagging_result_writer(schema_http_result_mock), MockTaggingResultWriter)


def test_rs_ai_tag_results_adapter_writes_partial_success_msg():
    bundle = normalize_tag_schema_response(fixture_schema())
    payload, detail = build_rs_ai_tag_results_payload(
        t_book_id="300000000300000279",
        job_id="0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
        tag_schema_version="v1.1",
        tag_schema=bundle["tag_schema_snapshot"],
        mutual_exclusion_rules=bundle["mutual_exclusion_rules"],
        final_result={
            "selected_tags": {
                "000001": [{"标签名": "女频", "权重": 1, "打标原因": "剧情以女主视角展开。"}],
            },
            "tagging_detail": {"notes": []},
        },
    )

    assert payload["status"] == "success"
    assert payload["msg"].startswith("partial_success:")
    assert "missing_required_category" in payload["msg"]
    assert detail["result_status"] == "partial_success"


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
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.settings.ENABLE_MOCK_INTERFACES", True)
    monkeypatch.setattr("app.services.jobs.get_enabled_model", lambda model_id: object())

    handler, normalized, runtime_fields = _validate_create_request(payload)

    assert isinstance(handler, InitialShortDramaTaggingHandler)
    assert normalized["work_context"]["subtitle_language"] == "zh"
    assert runtime_fields["model_id"]
    assert handler.public_result({"artifacts": [], "signals": {}}) is None


def test_short_drama_handler_allows_rs_mock_when_public_mock_interfaces_disabled(monkeypatch):
    handler = InitialShortDramaTaggingHandler()
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.settings.ENABLE_MOCK_INTERFACES", False)
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.settings.SHORT_DRAMA_RS_SCHEMA_MOCK_ENABLED", True)
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.settings.SHORT_DRAMA_RS_RESULT_MOCK_ENABLED", True)

    handler.validate_normalized_job_params(tagging_params())


def test_short_drama_create_validation_checks_schema_mock_language_before_queue(monkeypatch, tmp_path):
    payload = tagging_params()
    payload["work_context"]["subtitle_language"] = "en"
    request = CreateJobRequest.model_validate(
        {"job_type": "short_drama.tagging.initial", "job_params": payload}
    )
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.settings.ENABLE_MOCK_INTERFACES", True)
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.settings.SHORT_DRAMA_RS_SCHEMA_MOCK_ENABLED", True)
    monkeypatch.setattr(
        "app.workflows.short_drama_tagging.handler.settings.SHORT_DRAMA_RS_SCHEMA_MOCK_PATH",
        str(tmp_path / "schema.{lang}.json"),
    )

    with pytest.raises(AppError) as exc:
        _validate_create_request(request)

    assert exc.value.code == "TAG_SCHEMA_UNAVAILABLE"
    assert exc.value.status_code == 500
    assert exc.value.details["requested_language"] == "en"


@pytest.mark.asyncio
async def test_short_drama_handler_builds_rs_payload_without_writing(monkeypatch):
    handler = InitialShortDramaTaggingHandler()
    job_id = uuid.uuid4()
    item_id = uuid.uuid4()
    job = AIJob(id=job_id, job_type="short_drama.tagging.initial")
    item = AIJobWorkItem(id=item_id, job_id=job_id, name="whole", kind="whole", chunk_index=0)
    bundle = normalize_tag_schema_response(fixture_schema())
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
            raise AssertionError("RS write must run after succeeded callback, not during work item execution")

    async def fake_generate_text(model_id, messages):
        return SimpleNamespace(text=json.dumps(next(responses), ensure_ascii=False))

    async def fake_update_progress(*_args, **_kwargs):
        pass

    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.work_item_payload", lambda _item: tagging_params())
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.runtime_fields_from_job", lambda _job: runtime_fields())
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.model_id_from_job", lambda _job: "fake")
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.get_tag_schema_provider", lambda _runtime_fields: Provider())
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.get_tagging_result_writer", lambda _runtime_fields: Writer())
    monkeypatch.setattr("app.integrations.ai_gateway.generate_text", fake_generate_text)
    monkeypatch.setattr("app.repositories.job_repo.JobRepo.update_progress", fake_update_progress)

    result = await handler.execute_standard_item(item, job, FakeDB())

    rs_payload = rs_ai_tag_results_payload_from_canonical_result(result)
    assert rs_payload["job_id"] == str(job_id)
    assert rs_payload["tag_schema_version"] == "v1.1"
    assert rs_payload["msg"].startswith("partial_success:")
    assert rs_payload["tags"]["000006"] == []
    assert result["signals"]["success"] is False
    assert result["signals"]["result_status"] == "partial_success"
    assert result["signals"]["rs_write_before_callback"] is True


@pytest.mark.asyncio
async def test_short_drama_success_side_effect_writes_rs_payload(monkeypatch):
    handler = InitialShortDramaTaggingHandler()
    job_id = uuid.uuid4()
    job = AIJob(id=job_id, job_type="short_drama.tagging.initial")
    payload = {
        "status": "success",
        "msg": None,
        "t_book_id": "300000000300000279",
        "job_id": str(job_id),
        "tag_schema_version": "v1.1",
        "tags": {"000001": []},
    }
    canonical_result = {
        "artifacts": [
            {"key": RS_AI_TAG_RESULTS_ARTIFACT_KEY, "type": "json", "label": "RS", "content": payload}
        ],
        "signals": {},
    }
    written = {}

    class Writer:
        async def write(self, rs_payload):
            written["payload"] = rs_payload
            return {"code": 0, "msg": "ok", "data": {"saved": True}}

    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.runtime_fields_from_job", lambda _job: runtime_fields())
    monkeypatch.setattr("app.workflows.short_drama_tagging.handler.get_tagging_result_writer", lambda _runtime_fields: Writer())

    await handler.run_success_side_effect(job, canonical_result, FakeDB())

    assert written["payload"] == payload


def test_translation_job_params_are_object_and_ordered():
    params = {
        "labels": [
            {
                "label_id": "lbl-audience-male",
                "source_language": "zh",
                "target_languages": ["en", "es", "pt"],
                "display_name": "男频",
                "definition": "男性受众。",
            }
        ]
    }
    assert TagSchemaTranslationParams.model_validate(params).labels[0].target_languages == ["en", "es", "pt"]

    with pytest.raises(Exception, match="business language order"):
        bad_order = copy.deepcopy(params)
        bad_order["labels"][0]["target_languages"] = ["pt", "en"]
        TagSchemaTranslationParams.model_validate(bad_order)

    with pytest.raises(Exception, match="duplicate label_id"):
        TagSchemaTranslationParams.model_validate({"labels": params["labels"] + copy.deepcopy(params["labels"])})

    with pytest.raises(Exception):
        TagSchemaTranslationParams.model_validate([
            {"source_language": "zh", "target_languages": ["en"]}
        ])


def test_translation_output_returns_label_artifacts():
    params = {
        "labels": [
            {
                "label_id": "lbl-audience-male",
                "source_language": "zh",
                "target_languages": ["en", "es"],
                "display_name": "男频",
                "definition": "男性受众。",
            }
        ]
    }
    result = parse_translation_output(
        json.dumps(
            {
                "artifacts": [
                    {
                        "label_id": "lbl-audience-male",
                        "langs": {
                            "en": {"name": "Male-oriented", "definition": "Male audience."},
                            "es": {"name": "Orientado a hombres", "definition": "Audiencia masculina."},
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        params,
    )

    assert result["artifacts"][0] == {
        "label_id": "lbl-audience-male",
        "langs": {
            "en": {"name": "Male-oriented", "definition": "Male audience."},
            "es": {"name": "Orientado a hombres", "definition": "Audiencia masculina."},
        },
    }
    assert result["signals"]["source_schema_hash"].startswith("sha256:")
    assert result["signals"]["translated_schemas_hash"].startswith("sha256:")

    changed_label_id = {
        "artifacts": [
            {
                "label_id": "rewritten-label",
                "langs": {"en": {"name": "Male-oriented", "definition": "Male audience."}},
            }
        ]
    }
    with pytest.raises(AppError, match="label_id changed"):
        parse_translation_output(json.dumps(changed_label_id), params)

    missing_language = {
        "artifacts": [
            {
                "label_id": "lbl-audience-male",
                "langs": {"en": {"name": "Male-oriented", "definition": "Male audience."}},
            }
        ]
    }
    with pytest.raises(AppError, match="languages do not match"):
        parse_translation_output(json.dumps(missing_language), params)
