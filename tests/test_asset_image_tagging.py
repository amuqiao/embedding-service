from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.business_packages.asset_image_tagging import model_adapter as asset_image_tagging_model_adapter
from app.business_packages.asset_image_tagging.errors import ASSET_IMAGE_TAGGING_ITEMS_EXCEEDS_LIMIT
from app.business_packages.asset_image_tagging.executor import (
    ASSET_IMAGE_TAGGING_ITEM_JOB_TYPE,
    ASSET_IMAGE_TAGGING_JOIN_JOB_TYPE,
    ASSET_IMAGE_TAGGING_JOIN_NODE_KEY,
    AssetImageTaggingItemJob,
    AssetImageTaggingJob,
    AssetImageTaggingJoinJob,
    register_asset_image_tagging_workflow,
)
from app.business_packages.asset_image_tagging.model_adapter import (
    OpenAIResponsesAssetImageTaggingModelAdapter,
    asset_image_tagging_model_adapter_from_settings,
    build_result_item,
    build_result_items_from_model_payload,
)
from app.business_packages.asset_image_tagging.prompt_builder import build_batch_prompt_payload
from app.business_packages.asset_image_tagging.schemas import (
    AssetImageTaggingAssetRef,
    AssetImageTaggingItemParams,
    AssetImageTaggingLabelSnapshotGroup,
    AssetImageTaggingParams,
    AssetImageTaggingResult,
)
from app.core.exceptions import AppError
from app.jobs.registry import register as register_job_executor
from app.workflows import compile_registered_workflow
from smoke.flows.asset import image_tagging as image_tagging_flow
from smoke.flows.asset.image_tagging import (
    DEFAULT_FIXTURE_PATH,
    _assert_result,
    _result_rows,
    build_payload,
)
from smoke.harness.errors import FlowError


def _item(category_id: str = "hair", item_id: str = "asset_001") -> AssetImageTaggingItemParams:
    return AssetImageTaggingItemParams(
        item_id=item_id,
        item_name="棕色中长卷发",
        category_id=category_id,
        category_name="发型",
        asset=AssetImageTaggingAssetRef(
            public_url="https://example.com/assets/hair_001.png",
            content_type="image/png",
        ),
    )


def _label_group(selection_mode: str = "single") -> AssetImageTaggingLabelSnapshotGroup:
    return AssetImageTaggingLabelSnapshotGroup(
        category_id="hair",
        category_name="发型",
        selection_mode=selection_mode,
        labels=[
            {
                "label_id": "hair_color_brown",
                "label_name": "棕色",
                "definition": "头发主体颜色为棕色或棕褐色",
            },
            {
                "label_id": "hair_color_black",
                "label_name": "黑色",
                "definition": "头发主体颜色为黑色或深黑色",
            },
        ],
    )


def _register_asset_image_tagging_workflow_for_test() -> None:
    register_job_executor(AssetImageTaggingJob())
    register_job_executor(AssetImageTaggingItemJob())
    register_job_executor(AssetImageTaggingJoinJob())
    register_asset_image_tagging_workflow()


def test_asset_image_tagging_selects_from_matching_category_only():
    item = build_result_item(
        item=_item(),
        tagging_language="zh",
        label_snapshot=[_label_group()],
    )

    assert item.status == "succeeded"
    assert item.category_id == "hair"
    assert item.label_group_selections[0].category_id == "hair"
    assert item.label_group_selections[0].labels[0].label_id == "hair_color_brown"


def test_asset_image_tagging_fails_item_without_matching_category():
    item = build_result_item(
        item=_item(category_id="clothes"),
        tagging_language="zh",
        label_snapshot=[_label_group()],
    )

    assert item.status == "failed"
    assert item.error is not None
    assert item.error.code == "LABEL_SNAPSHOT_NOT_FOUND"


def test_asset_image_tagging_params_reject_missing_category_snapshot():
    with pytest.raises(ValueError, match="items\\[\\]\\.category_id"):
        AssetImageTaggingParams(
            tagging_language="zh",
            items=[_item(category_id="clothes")],
            label_snapshot=[_label_group()],
        )


def test_asset_image_tagging_rejects_non_image_asset():
    with pytest.raises(ValueError, match="content_type"):
        AssetImageTaggingAssetRef(
            public_url="https://example.com/assets/hair_001.txt",
            content_type="text/plain",
        )


def test_asset_image_tagging_prompt_payload_keeps_item_category_scope():
    payload = build_batch_prompt_payload(
        AssetImageTaggingParams(
            tagging_language="zh",
            items=[_item()],
            label_snapshot=[_label_group()],
        )
    )

    assert payload["tagging_language"] == "zh"
    assert payload["items"][0]["item"]["item_ref"] == "I1"
    assert "item_id" not in payload["items"][0]["item"]
    assert "category_id" not in payload["items"][0]["item"]
    assert payload["items"][0]["label_groups"][0]["selection_mode"] == "single"
    assert payload["items"][0]["label_groups"][0]["group_ref"] == "G1"
    assert "category_id" not in payload["items"][0]["label_groups"][0]
    assert payload["items"][0]["label_groups"][0]["labels"][0] == {
        "label_ref": "L1",
        "label_name": "棕色",
        "definition": "头发主体颜色为棕色或棕褐色",
    }


def test_asset_image_tagging_rejects_items_over_configured_limit(monkeypatch):
    monkeypatch.setattr(
        "app.business_packages.asset_image_tagging.executor.settings",
        SimpleNamespace(job=SimpleNamespace(asset_image_tagging=SimpleNamespace(max_items=1))),
    )
    params = AssetImageTaggingParams(
        tagging_language="zh",
        items=[_item(item_id="asset_001"), _item(item_id="asset_002")],
        label_snapshot=[_label_group()],
    )

    with pytest.raises(AppError) as exc:
        AssetImageTaggingJob().normalize_job_params(params.model_dump(exclude_none=True))

    assert exc.value.code == ASSET_IMAGE_TAGGING_ITEMS_EXCEEDS_LIMIT
    assert exc.value.details == {
        "item_count": 2,
        "max_items": 1,
        "job_type": "asset_image_tagging",
    }


def test_asset_image_tagging_workflow_compiles_one_child_per_item_and_join():
    _register_asset_image_tagging_workflow_for_test()
    params = AssetImageTaggingParams(
        tagging_language="zh",
        items=[_item(item_id="asset_001"), _item(item_id="asset_002")],
        label_snapshot=[_label_group()],
    )

    plan = compile_registered_workflow("asset_image_tagging", params.model_dump(exclude_none=True))
    nodes = {node["key"]: node for node in plan["nodes"]}

    assert plan["workflow_type"] == "asset_image_tagging"
    assert plan["node_count"] == 3
    assert nodes["item.0"]["job_type"] == ASSET_IMAGE_TAGGING_ITEM_JOB_TYPE
    assert nodes["item.1"]["job_type"] == ASSET_IMAGE_TAGGING_ITEM_JOB_TYPE
    assert nodes[ASSET_IMAGE_TAGGING_JOIN_NODE_KEY]["job_type"] == ASSET_IMAGE_TAGGING_JOIN_JOB_TYPE
    assert nodes[ASSET_IMAGE_TAGGING_JOIN_NODE_KEY]["depends_on"] == ["item.0", "item.1"]
    assert nodes["item.0"]["job_params"]["item"]["item_id"] == "asset_001"
    assert nodes["item.0"]["job_params"]["label_snapshot_indexes"] == [0]
    assert nodes[ASSET_IMAGE_TAGGING_JOIN_NODE_KEY]["job_params"]["item_ids"] == [
        "asset_001",
        "asset_002",
    ]


def test_asset_image_tagging_public_result_extracts_join_result_from_workflow_envelope():
    result_item = build_result_item(
        item=_item(),
        tagging_language="zh",
        label_snapshot=[_label_group()],
    )
    join_result = AssetImageTaggingResult(
        tagging_language="zh",
        batch_summary={"total": 1, "succeeded": 1, "partial_success": 0, "failed": 0},
        items=[result_item],
    ).model_dump(exclude_none=True)
    canonical_result = {
        "schema_version": 1,
        "job_type": "asset_image_tagging",
        "workflow": {
            "workflow_type": "asset_image_tagging",
            "workflow_version": 1,
            "outcome": "success",
            "failure_policy": "fail_fast",
            "node_count": 2,
            "succeeded": 2,
            "failed": 0,
            "nodes": [
                {
                    "node_key": "item.0",
                    "job_id": "item-job-id",
                    "job_type": ASSET_IMAGE_TAGGING_ITEM_JOB_TYPE,
                    "status": "succeeded",
                    "result": result_item.model_dump(exclude_none=True),
                },
                {
                    "node_key": ASSET_IMAGE_TAGGING_JOIN_NODE_KEY,
                    "job_id": "join-job-id",
                    "job_type": ASSET_IMAGE_TAGGING_JOIN_JOB_TYPE,
                    "status": "succeeded",
                    "result": join_result,
                },
            ],
        },
    }

    assert AssetImageTaggingJob().public_result(canonical_result) == join_result


def test_asset_image_tagging_adapter_uses_openai_settings(monkeypatch):
    fake_settings = SimpleNamespace(
        ai_provider=SimpleNamespace(
            openai_api_key_value="openai-key",
            openai_base_url="https://openai.example/v1",
            model_call_timeout_seconds=42,
        ),
        job=SimpleNamespace(
            asset_image_tagging=SimpleNamespace(
                model_adapter="openai_responses",
                model_id="gpt-4o",
            )
        ),
    )
    monkeypatch.setattr("app.business_packages.asset_image_tagging.model_adapter.settings", fake_settings)

    adapter = asset_image_tagging_model_adapter_from_settings()

    assert isinstance(adapter, OpenAIResponsesAssetImageTaggingModelAdapter)
    assert adapter.api_key == "openai-key"
    assert adapter.base_url == "https://openai.example/v1"
    assert adapter.model_id == "gpt-4o"
    assert adapter.timeout_seconds == 42
    assert adapter.batch_size == 1


async def test_asset_image_tagging_adapter_requires_openai_api_key():
    adapter = OpenAIResponsesAssetImageTaggingModelAdapter(
        api_key="",
        base_url=None,
        model_id="gpt-4o",
        timeout_seconds=42,
        batch_size=1,
    )

    with pytest.raises(AppError, match="OPENAI_API_KEY"):
        await adapter.tag(AssetImageTaggingParams(tagging_language="zh", items=[_item()], label_snapshot=[_label_group()]))


async def test_asset_image_tagging_openai_adapter_uses_structured_output_and_single_item_batches(monkeypatch):
    calls: list[dict] = []

    class FakeResponses:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output_text=json.dumps(
                    {
                        "items": [
                            {
                                "item_ref": "I1",
                                "asset_description": "一张棕色中长卷发素材。",
                                "label_group_selections": [
                                    {
                                        "group_ref": "G1",
                                        "labels": [
                                            {
                                                "label_ref": "L1",
                                                "weight": 0.91,
                                                "reason": "图片主体发色偏棕。",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(asset_image_tagging_model_adapter, "_client", lambda **_kwargs: FakeClient())

    adapter = OpenAIResponsesAssetImageTaggingModelAdapter(
        api_key="openai-key",
        base_url="https://openai.example/v1",
        model_id="gpt-4o",
        timeout_seconds=42,
        batch_size=1,
    )
    first_item = _item()
    second_item = _item().model_copy(update={"item_id": "asset_002", "item_name": "黑色中长卷发"})

    result_items = await adapter.tag(
        AssetImageTaggingParams(
            tagging_language="zh",
            items=[first_item, second_item],
            label_snapshot=[_label_group()],
        )
    )

    assert len(calls) == 2
    assert all(call["text"]["format"]["type"] == "json_schema" for call in calls)
    assert all(call["text"]["format"]["strict"] is True for call in calls)
    rendered_input = json.dumps(calls[0]["input"], ensure_ascii=False)
    assert "item_id" not in rendered_input
    assert "category_id" not in rendered_input
    assert "label_id" not in rendered_input
    assert "item_ref" in rendered_input
    assert "label_ref" in rendered_input
    assert [item.item_id for item in result_items] == ["asset_001", "asset_002"]
    assert [item.label_group_selections[0].labels[0].label_id for item in result_items] == [
        "hair_color_brown",
        "hair_color_brown",
    ]


def test_asset_image_tagging_maps_model_label_refs_back_to_snapshot():
    params = AssetImageTaggingParams(
        tagging_language="zh",
        items=[_item()],
        label_snapshot=[_label_group()],
    )

    result_items = build_result_items_from_model_payload(
        params=params,
        payload={
            "items": [
                {
                    "item_ref": "I1",
                    "asset_description": "一张棕色中长卷发素材。",
                    "label_group_selections": [
                        {
                            "group_ref": "G1",
                            "labels": [
                                {
                                    "label_ref": "L1",
                                    "weight": 0.91,
                                    "reason": "图片主体发色偏棕。",
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )

    assert result_items[0].status == "succeeded"
    label = result_items[0].label_group_selections[0].labels[0]
    assert label.label_id == "hair_color_brown"
    assert label.label_name == "棕色"
    assert label.definition == "头发主体颜色为棕色或棕褐色"


def test_asset_image_tagging_marks_extra_group_ref_as_partial_success():
    params = AssetImageTaggingParams(
        tagging_language="zh",
        items=[_item()],
        label_snapshot=[_label_group()],
    )

    result_items = build_result_items_from_model_payload(
        params=params,
        payload={
            "items": [
                {
                    "item_ref": "I1",
                    "asset_description": "一张棕色中长卷发素材。",
                    "label_group_selections": [
                        {
                            "group_ref": "G1",
                            "labels": [
                                {
                                    "label_ref": "L1",
                                    "weight": 0.91,
                                    "reason": "图片主体发色偏棕。",
                                }
                            ],
                        },
                        {
                            "group_ref": "G99",
                            "labels": [
                                {
                                    "label_ref": "made_up",
                                    "weight": 0.8,
                                    "reason": "非法跨分类标签组。",
                                }
                            ],
                        },
                    ],
                }
            ]
        },
    )

    assert result_items[0].status == "partial_success"
    assert result_items[0].validation_issues[0].issue == "model_response_invalid"
    assert result_items[0].validation_issues[0].details["group_ref"] == "G99"


def test_asset_image_tagging_rejects_empty_single_selection():
    params = AssetImageTaggingParams(
        tagging_language="zh",
        items=[_item()],
        label_snapshot=[_label_group(selection_mode="single")],
    )

    result_items = build_result_items_from_model_payload(
        params=params,
        payload={
            "items": [
                {
                    "item_ref": "I1",
                    "asset_description": "一张棕色中长卷发素材。",
                    "label_group_selections": [{"group_ref": "G1", "labels": []}],
                }
            ]
        },
    )

    assert result_items[0].status == "failed"
    assert result_items[0].error is not None
    assert result_items[0].error.code == "NO_LABEL_SELECTED"
    assert result_items[0].validation_issues[0].issue == "model_single_selection_invalid"


def test_asset_image_tagging_default_smoke_fixture_is_valid_batch_payload():
    payload, fixture_path = build_payload(
        client_request_id="test-asset-image-tagging",
        fixture_path=None,
        item_limit=2,
    )

    assert fixture_path == DEFAULT_FIXTURE_PATH
    assert payload["job_type"] == "asset_image_tagging"
    params = AssetImageTaggingParams.model_validate(payload["job_params"])
    assert len(params.items) == 2
    item_category_ids = {item.category_id for item in params.items}
    snapshot_category_ids = {group.category_id for group in params.label_snapshot}
    assert snapshot_category_ids == item_category_ids


def test_asset_image_tagging_smoke_payload_does_not_send_fixture_path():
    payload, _fixture_path = build_payload(
        client_request_id="test-asset-image-tagging",
        fixture_path=None,
        item_limit=1,
    )

    assert payload["metadata"] == {"source": "scripts/smoke.sh asset-image-tagging"}


def test_asset_image_tagging_smoke_human_output_prints_fixture_path_outside_summary_table(monkeypatch, capsys):
    captured: dict[str, dict] = {}

    monkeypatch.setattr(
        image_tagging_flow.job_runtime,
        "resolve_job_context",
        lambda **_kwargs: SimpleNamespace(
            app_env="test",
            summary={
                "ready": True,
                "problems": [],
                "jobs_url": "http://127.0.0.1:8100/api/v1/ai-jobs/jobs",
                "api_url": "http://127.0.0.1:8100",
            },
        ),
    )
    monkeypatch.setattr(image_tagging_flow.service_runtime, "build_headers", lambda *_args, **_kwargs: {})

    def fake_request_json(url, *, method, headers, payload=None, timeout_seconds=10):
        captured["request"] = payload
        return {
            "code": "0",
            "data": {"job": {"job_id": "job-asset-tagging-1", "job_status": "queued"}},
        }

    def fake_poll_job_envelope(**_kwargs):
        request_payload = captured["request"]
        item = request_payload["job_params"]["items"][0]
        return {
            "code": "0",
            "data": {
                "job": {
                    "job_id": "job-asset-tagging-1",
                    "job_status": "succeeded",
                    "job_result": {
                        "job_type": "asset_image_tagging",
                        "batch_summary": {"total": 1, "succeeded": 1, "partial_success": 0, "failed": 0},
                        "items": [
                            {
                                "item_id": item["item_id"],
                                "status": "succeeded",
                                "asset_description": {"text": "一张礼物物件素材。"},
                                "label_group_selections": [
                                    {
                                        "label_snapshot_index": 0,
                                        "category_id": item["category_id"],
                                        "labels": [{"label_id": "object_type_gift", "label_name": "礼物"}],
                                    }
                                ],
                                "validation_issues": [],
                            }
                        ],
                    },
                }
            },
        }

    monkeypatch.setattr(image_tagging_flow.http_runtime, "request_json", fake_request_json)
    monkeypatch.setattr(image_tagging_flow.job_runtime, "poll_job_envelope", fake_poll_job_envelope)

    image_tagging_flow.run(
        confirm_run=True,
        confirm_cost=True,
        api_url=None,
        env_file=None,
        allow_remote_api=False,
        service_api_key=None,
        caller_id="smoke-cli",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        client_request_id="test-asset-image-tagging",
        fixture_path=None,
        item_limit=1,
        json_output=False,
    )

    output = capsys.readouterr().out
    assert f"fixture: {DEFAULT_FIXTURE_PATH}" in output
    assert not any(line.startswith("fixture") and "job_id" in line for line in output.splitlines())
    assert "input_relative_path" in output
    assert "tagging_labels" in output
    assert "物件/CBTB_Cris_7_p1" in output
    assert "礼物" in output


def test_asset_image_tagging_smoke_result_rows_include_relative_path_and_label_names():
    rows = _result_rows(
        [
            {
                "item_id": "物件/CBTB_Cris_7_p1",
                "status": "succeeded",
                "validation_issues": [],
                "label_group_selections": [
                    {
                        "labels": [
                            {
                                "label_id": "object_type_gift",
                                "label_name": "礼物",
                            }
                        ]
                    }
                ],
            }
        ]
    )

    assert rows == [
        {
            "item_id": "物件/CBTB_Cris_7_p1",
            "input_relative_path": "物件/CBTB_Cris_7_p1",
            "status": "succeeded",
            "selected": "object_type_gift",
            "tagging_labels": "礼物",
            "issue_count": 0,
        }
    ]


def test_asset_image_tagging_smoke_rejects_cross_category_selection():
    request_payload, _fixture_path = build_payload(
        client_request_id="test-asset-image-tagging",
        fixture_path=None,
        item_limit=2,
    )
    first_item = request_payload["job_params"]["items"][0]
    second_item = request_payload["job_params"]["items"][1]
    second_group_index = next(
        index
        for index, group in enumerate(request_payload["job_params"]["label_snapshot"])
        if group["category_id"] == second_item["category_id"]
    )
    second_label = request_payload["job_params"]["label_snapshot"][second_group_index]["labels"][0]

    with pytest.raises(FlowError, match="cross-category selection"):
        _assert_result(
            {
                "job_status": "succeeded",
                "job_result": {
                    "job_type": "asset_image_tagging",
                    "batch_summary": {"total": 2, "succeeded": 2, "partial_success": 0, "failed": 0},
                    "items": [
                        {
                            "item_id": first_item["item_id"],
                            "status": "succeeded",
                            "asset_description": {"text": "第一张素材描述"},
                            "label_group_selections": [
                                {
                                    "label_snapshot_index": second_group_index,
                                    "category_id": second_item["category_id"],
                                    "labels": [{"label_id": second_label["label_id"]}],
                                }
                            ],
                            "validation_issues": [],
                        },
                        {
                            "item_id": second_item["item_id"],
                            "status": "succeeded",
                            "asset_description": {"text": "第二张素材描述"},
                            "label_group_selections": [
                                {
                                    "label_snapshot_index": second_group_index,
                                    "category_id": second_item["category_id"],
                                    "labels": [{"label_id": second_label["label_id"]}],
                                }
                            ],
                            "validation_issues": [],
                        },
                    ],
                },
            },
            request_payload=request_payload,
        )


def test_asset_image_tagging_smoke_rejects_partial_success_result():
    request_payload, _fixture_path = build_payload(
        client_request_id="test-asset-image-tagging",
        fixture_path=None,
        item_limit=1,
    )
    request_item = request_payload["job_params"]["items"][0]
    group = request_payload["job_params"]["label_snapshot"][0]
    label = group["labels"][0]

    with pytest.raises(FlowError, match="did not succeed"):
        _assert_result(
            {
                "job_status": "succeeded",
                "job_result": {
                    "job_type": "asset_image_tagging",
                    "batch_summary": {"total": 1, "succeeded": 0, "partial_success": 1, "failed": 0},
                    "items": [
                        {
                            "item_id": request_item["item_id"],
                            "status": "partial_success",
                            "asset_description": {"text": "素材描述"},
                            "label_group_selections": [
                                {
                                    "label_snapshot_index": 0,
                                    "category_id": request_item["category_id"],
                                    "labels": [{"label_id": label["label_id"]}],
                                }
                            ],
                            "validation_issues": [{"issue": "model_response_invalid"}],
                        }
                    ],
                },
            },
            request_payload=request_payload,
        )


def test_asset_image_tagging_smoke_rejects_missing_asset_description():
    request_payload, _fixture_path = build_payload(
        client_request_id="test-asset-image-tagging",
        fixture_path=None,
        item_limit=1,
    )
    request_item = request_payload["job_params"]["items"][0]
    group = request_payload["job_params"]["label_snapshot"][0]
    label = group["labels"][0]

    with pytest.raises(FlowError, match="missing asset_description"):
        _assert_result(
            {
                "job_status": "succeeded",
                "job_result": {
                    "job_type": "asset_image_tagging",
                    "batch_summary": {"total": 1, "succeeded": 1, "partial_success": 0, "failed": 0},
                    "items": [
                        {
                            "item_id": request_item["item_id"],
                            "status": "succeeded",
                            "label_group_selections": [
                                {
                                    "label_snapshot_index": 0,
                                    "category_id": request_item["category_id"],
                                    "labels": [{"label_id": label["label_id"]}],
                                }
                            ],
                            "validation_issues": [],
                        }
                    ],
                },
            },
            request_payload=request_payload,
        )
