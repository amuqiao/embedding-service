from __future__ import annotations

import json
import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.poc import short_drama_build_structured_inputs as builder
from scripts.poc import short_drama_tagging_poc as runner


def minimal_tag_schema() -> dict[str, object]:
    return {
        "version": "test",
        "generated_at": 1,
        "categories": [
            category("000001", "受众", 1, 1, [label("audience-male", "男频", "男性受众")]),
            category("000002", "时空", 1, 1, [label("space-city", "现代都市", "现代城市")]),
            category("000003", "题材", 1, 3, [label("genre-romance", "言情", "爱情关系")]),
            category(
                "000004",
                "情节",
                3,
                8,
                [
                    label("plot-system", "系统奇遇", "系统能力"),
                    label("plot-fantasy", "奇幻脑洞", "奇幻设定"),
                    label("plot-rise", "逆袭", "逆转处境"),
                ],
            ),
            category(
                "000005",
                "角色设定",
                2,
                4,
                [
                    label("role-male", "大男主", "男性主角"),
                    label("role-elite", "精英阶层", "精英角色"),
                ],
            ),
            category("000006", "情绪", 1, 1, [label("emotion-abuse", "虐", "痛苦压抑"), label("emotion-satisfy", "爽", "畅快解气")]),
        ],
        "audience_filter_rules": [],
    }


def category(category_id: str, name: str, min_items: int, max_items: int, labels: list[dict[str, str]]) -> dict[str, object]:
    return {
        "category_id": category_id,
        "name": name,
        "required": True,
        "min_items": min_items,
        "max_items": max_items,
        "labels": labels,
    }


def label(label_id: str, name: str, definition: str) -> dict[str, str]:
    return {"label_id": label_id, "label_key": label_id, "name": name, "definition": definition}


def input_payload() -> dict[str, object]:
    return {
        "job_id": "job-1",
        "client_request_id": "client-1",
        "job_type": "short_drama.tagging.initial",
        "job_params": {
            "t_book_id": "123",
            "work_context": {
                "title": "Title",
                "synopsis": "Synopsis",
                "subtitle_language": "en",
                "audio_language": "zh",
                "series_structure": "continuous_series",
                "content_type": "短剧",
                "episode_count": 1,
            },
            "assets": [
                {
                    "asset_type": "subtitle_srt",
                    "episode_no": 1,
                    "format": "srt",
                    "uri": "memory://episode-1.srt",
                    "text": "1\n00:00:01,000 --> 00:00:02,000\nHello\n",
                    "content_hash": "sha256:test",
                    "metadata": {"filename": "episode-1.srt", "is_preview": False},
                }
            ],
        },
        "rs_default_tag_bundle": {
            "tag_schema_snapshot": minimal_tag_schema(),
            "mutual_exclusion_rules": [],
        },
    }


def valid_final_tags(schema: dict[str, object]) -> dict[str, object]:
    categories = {item["name"]: item for item in schema["categories"]}

    def tag(category_name: str, label_name: str, weight: float = 1.0) -> dict[str, object]:
        category_item = categories[category_name]
        label_item = next(item for item in category_item["labels"] if item["name"] == label_name)
        return {
            "label_id": label_item["label_id"],
            "标签名": label_name,
            "权重": weight,
            "打标原因": "reason",
            "标签释义": label_item["definition"],
        }

    return {
        "t_book_id": "123",
        "tags": {
            "000001": [tag("受众", "男频")],
            "000002": [tag("时空", "现代都市")],
            "000003": [tag("题材", "言情")],
            "000004": [tag("情节", "系统奇遇"), tag("情节", "奇幻脑洞"), tag("情节", "逆袭")],
            "000005": [tag("角色设定", "大男主"), tag("角色设定", "精英阶层")],
            "000006": [tag("情绪", "爽")],
        },
    }


def selected_tags_from_final_tags(final_tags: dict[str, object]) -> dict[str, object]:
    return {
        category_id: [
            {
                "标签名": item["标签名"],
                "权重": item["权重"],
                "打标原因": item["打标原因"],
            }
            for item in items
        ]
        for category_id, items in final_tags["tags"].items()
    }


def workflow_definition() -> dict[str, object]:
    return builder.build_workflow_definition()


def prompt_templates() -> dict[str, dict[str, object]]:
    return {
        template["prompt_id"]: template
        for template in builder.build_prompt_templates()["templates"]
    }


def test_build_prompts_consumes_structured_input_and_config() -> None:
    prompts = runner.build_prompts(
        input_payload(),
        workflow_definition(),
        prompt_templates(),
        strict=True,
        preview_outputs=True,
    )

    assert set(prompts) == {"story_overview", "candidate_tagging", "finalize"}
    assert "Title" in prompts["story_overview"][0]["content"]
    assert "现代都市" in prompts["candidate_tagging"][0]["content"]
    assert "dry_run_placeholder" in prompts["candidate_tagging"][0]["content"]
    rendered_text = json.dumps(prompts, ensure_ascii=False)
    assert "虐-紧张-爽" not in rendered_text
    assert "emotion_sequence_prompt_v1" not in rendered_text
    assert "emotion_sequence_finalize_prompt_v1" not in rendered_text


def test_finalize_prompt_caps_weights_at_one() -> None:
    prompts = runner.build_prompts(
        input_payload(),
        workflow_definition(),
        prompt_templates(),
        strict=True,
        preview_outputs=True,
    )

    finalize_prompt = prompts["finalize"][0]["content"]
    assert "标签权重 = min(标签浓度 * 标签初始权重, 1.0)" in finalize_prompt
    assert "任何“权重”都不得大于 1" in finalize_prompt
    assert "不要输出 1.1、1.2" in finalize_prompt


def test_prompts_keep_label_ids_out_of_model_contract() -> None:
    prompts = runner.build_prompts(
        input_payload(),
        workflow_definition(),
        prompt_templates(),
        strict=True,
        preview_outputs=True,
    )

    assert "不要输出 label_id" in prompts["candidate_tagging"][0]["content"]
    assert "AI 不要生成、复制、缩写或猜测" in prompts["finalize"][0]["content"]
    assert "selected_tags" in prompts["finalize"][0]["content"]


def test_disabled_prompt_block_can_be_enabled_explicitly() -> None:
    templates = prompt_templates()
    templates["candidate_tagging_v1"]["messages"][0]["blocks"][0]["enabled"] = True
    templates["finalize_v1"]["messages"][0]["blocks"][0]["enabled"] = True

    prompts = runner.build_prompts(
        input_payload(),
        workflow_definition(),
        templates,
        strict=True,
        preview_outputs=True,
    )

    assert "虐-紧张-爽" in prompts["candidate_tagging"][0]["content"]
    assert "虐-紧张-爽" in prompts["finalize"][0]["content"]


def test_prompt_template_unknown_variable_fails_fast() -> None:
    templates = prompt_templates()
    templates["story_overview_v1"] = {
        **templates["story_overview_v1"],
        "messages": [{"role": "user", "template": "{{unknown_variable}}"}],
    }

    with pytest.raises(ValueError, match="unknown variable"):
        runner.build_prompts(
            input_payload(),
            workflow_definition(),
            templates,
            strict=True,
            preview_outputs=True,
        )


def test_validate_input_payload_rejects_missing_required_category() -> None:
    payload = input_payload()
    payload["rs_default_tag_bundle"]["tag_schema_snapshot"]["categories"] = [
        category
        for category in payload["rs_default_tag_bundle"]["tag_schema_snapshot"]["categories"]
        if category["category_id"] != "000006"
    ]

    with pytest.raises(ValueError, match="missing required category_id"):
        runner.validate_input_payload(payload, Path("input.json"))


def test_validate_input_payload_rejects_duplicate_label_name_in_category() -> None:
    payload = input_payload()
    labels = payload["rs_default_tag_bundle"]["tag_schema_snapshot"]["categories"][0]["labels"]
    labels.append({**labels[0], "label_id": "another-label-id"})

    with pytest.raises(ValueError, match="duplicate name"):
        runner.validate_input_payload(payload, Path("input.json"))


def test_validate_final_tags_rejects_bad_weight() -> None:
    schema = minimal_tag_schema()
    final_tags = valid_final_tags(schema)
    final_tags["tags"]["000001"][0]["权重"] = 1.5

    with pytest.raises(ValueError, match="invalid 权重"):
        runner.validate_final_tags(final_tags, schema)

    final_tags = valid_final_tags(schema)
    final_tags["tags"]["000001"][0]["权重"] = 0

    with pytest.raises(ValueError, match="invalid 权重"):
        runner.validate_final_tags(final_tags, schema)


def test_validate_final_tags_rejects_item_count_outside_category_limits() -> None:
    schema = minimal_tag_schema()
    final_tags = valid_final_tags(schema)
    final_tags["tags"]["000004"] = final_tags["tags"]["000004"][:2]

    with pytest.raises(ValueError, match="item count must be between"):
        runner.validate_final_tags(final_tags, schema)

    final_tags = valid_final_tags(schema)
    final_tags["tags"]["000003"] = [
        final_tags["tags"]["000003"][0],
        final_tags["tags"]["000003"][0],
        final_tags["tags"]["000003"][0],
        final_tags["tags"]["000003"][0],
    ]

    with pytest.raises(ValueError, match="item count must be between"):
        runner.validate_final_tags(final_tags, schema)


def test_validate_final_tags_normalizes_label_text_from_schema() -> None:
    schema = minimal_tag_schema()
    final_tags = valid_final_tags(schema)
    final_tags["tags"]["000001"][0]["标签名"] = "女频"
    final_tags["tags"]["000001"][0]["标签释义"] = "wrong"

    warnings = runner.validate_final_tags(final_tags, schema)

    assert final_tags["tags"]["000001"][0]["标签名"] == "男频"
    assert final_tags["tags"]["000001"][0]["标签释义"] == "男性受众"
    assert warnings == [
        {
            "stage": "final_tags",
            "field": "tags.000001.audience-male.标签名",
            "from_type": "str",
            "from_value": "女频",
            "to_type": "str",
            "to_value": "男频",
        },
        {
            "stage": "final_tags",
            "field": "tags.000001.audience-male.标签释义",
            "from_type": "str",
            "from_value": "wrong",
            "to_type": "str",
            "to_value": "男性受众",
        },
    ]


def test_validate_final_tags_rejects_invalid_label_id() -> None:
    schema = minimal_tag_schema()
    final_tags = valid_final_tags(schema)
    final_tags["tags"]["000001"][0]["label_id"] = "missing-label"

    with pytest.raises(ValueError, match="invalid label_id"):
        runner.validate_final_tags(final_tags, schema)


def test_validate_final_tags_normalizes_unique_label_id_prefix() -> None:
    schema = minimal_tag_schema()
    final_tags = valid_final_tags(schema)
    final_tags["tags"]["000001"][0]["label_id"] = "audience-mal"

    warnings = runner.validate_final_tags(final_tags, schema)

    assert final_tags["tags"]["000001"][0]["label_id"] == "audience-male"
    assert warnings == [
        {
            "stage": "final_tags",
            "field": "tags.000001.label_id",
            "from_type": "str",
            "from_value": "audience-mal",
            "to_type": "str",
            "to_value": "audience-male",
        }
    ]


def test_validate_final_tags_rejects_incomplete_emotion_item() -> None:
    schema = minimal_tag_schema()
    final_tags = valid_final_tags(schema)
    final_tags["tags"]["000006"] = [
        {
            "emotion_label_ids": ["emotion-abuse", "emotion-satisfy"],
            "标签名": "虐-爽",
            "打标原因": "reason",
            "标签释义": "sequence",
        }
    ]

    with pytest.raises(ValueError, match="missing keys"):
        runner.validate_final_tags(final_tags, schema)


def test_build_final_tags_rejects_legacy_final_tags_t_book_id_mismatch() -> None:
    schema = minimal_tag_schema()
    final_tags = valid_final_tags(schema)
    final_tags["t_book_id"] = "456"

    with pytest.raises(ValueError, match="finalize.final_tags.t_book_id must be 123: 456"):
        runner.build_final_tags({"final_tags": final_tags}, {}, schema, expected_t_book_id="123")


def test_construct_final_tags_from_selected_tags_rejects_unknown_category_id() -> None:
    schema = minimal_tag_schema()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(schema))
    selected_tags["999999"] = []

    with pytest.raises(ValueError, match="unknown category_id"):
        runner.construct_final_tags_from_selected_tags(
            selected_tags,
            schema,
            expected_t_book_id="123",
            allow_label_id=False,
        )


def test_construct_final_tags_from_selected_tags_rejects_unknown_label_name() -> None:
    schema = minimal_tag_schema()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(schema))
    selected_tags["000001"][0]["标签名"] = "未知标签"

    with pytest.raises(ValueError, match="unknown 标签名"):
        runner.construct_final_tags_from_selected_tags(
            selected_tags,
            schema,
            expected_t_book_id="123",
            allow_label_id=False,
        )


def test_construct_final_tags_from_selected_tags_rejects_malformed_category_items() -> None:
    schema = minimal_tag_schema()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(schema))
    selected_tags["000001"] = {"标签名": "男频"}

    with pytest.raises(ValueError, match="selected_tags.000001 must be an array"):
        runner.construct_final_tags_from_selected_tags(
            selected_tags,
            schema,
            expected_t_book_id="123",
            allow_label_id=False,
        )


def test_construct_final_tags_from_selected_tags_rejects_over_max_items() -> None:
    schema = minimal_tag_schema()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(schema))
    selected_tags["000003"] = selected_tags["000003"] * 4

    with pytest.raises(ValueError, match="item count must be between"):
        runner.construct_final_tags_from_selected_tags(
            selected_tags,
            schema,
            expected_t_book_id="123",
            allow_label_id=False,
        )


def test_construct_final_tags_from_selected_tags_rejects_invalid_weight() -> None:
    schema = minimal_tag_schema()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(schema))
    selected_tags["000001"][0]["权重"] = 1.1

    with pytest.raises(ValueError, match="invalid 权重"):
        runner.construct_final_tags_from_selected_tags(
            selected_tags,
            schema,
            expected_t_book_id="123",
            allow_label_id=False,
        )


@pytest.mark.asyncio
async def test_run_model_flow_writes_validated_job_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    final_tags = valid_final_tags(payload["rs_default_tag_bundle"]["tag_schema_snapshot"])
    responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": "123", "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            {"final_tags": final_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    job_result = json.loads((tmp_path / "outputs" / "job_result.json").read_text(encoding="utf-8"))
    checksum = job_result["signals"].pop("result_checksum")
    assert checksum == runner.checksum_job_result(job_result)
    assert job_result["signals"]["result_status"] == "success"
    assert job_result["signals"]["validation_issue_count"] == 0
    assert (tmp_path / "intermediate" / "story_overview_result.json").exists()
    assert (tmp_path / "intermediate" / "candidate_tags.json").exists()
    assert (tmp_path / "intermediate" / "prompts.json").exists()
    assert (tmp_path / "outputs" / "final_tags.json").exists()
    assert (tmp_path / "outputs" / "tagging_detail.json").exists()


@pytest.mark.asyncio
async def test_run_model_flow_builds_final_tags_from_selected_tags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(payload["rs_default_tag_bundle"]["tag_schema_snapshot"]))
    responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": "123", "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            {"selected_tags": selected_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    final_tags = json.loads((tmp_path / "outputs" / "final_tags.json").read_text(encoding="utf-8"))
    assert final_tags["tags"]["000001"][0]["label_id"] == "audience-male"
    assert final_tags["tags"]["000001"][0]["标签释义"] == "男性受众"
    assert json.loads((tmp_path / "intermediate" / "normalization_warnings.json").read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_run_model_flow_rejects_label_id_in_selected_tags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(payload["rs_default_tag_bundle"]["tag_schema_snapshot"]))
    selected_tags["000001"][0]["label_id"] = "audience-male"
    responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": "123", "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            {"selected_tags": selected_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    with pytest.raises(ValueError, match="must not contain label_id"):
        await runner.run_model_flow(
            payload,
            tmp_path,
            workflow_definition(),
            prompt_templates(),
            model="fake",
            temperature=0,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_run_model_flow_returns_partial_success_for_below_min_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(payload["rs_default_tag_bundle"]["tag_schema_snapshot"]))
    selected_tags["000004"] = selected_tags["000004"][:1]
    responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": "123", "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            {"selected_tags": selected_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    final_tags = json.loads((tmp_path / "outputs" / "final_tags.json").read_text(encoding="utf-8"))
    tagging_detail = json.loads((tmp_path / "outputs" / "tagging_detail.json").read_text(encoding="utf-8"))
    job_result = json.loads((tmp_path / "outputs" / "job_result.json").read_text(encoding="utf-8"))
    assert len(final_tags["tags"]["000004"]) == 1
    assert tagging_detail["validation_issues"] == [
        {
            "category_id": "000004",
            "category_name": "情节",
            "issue": "below_min_items",
            "min_items": 3,
            "max_items": 8,
            "actual_items": 1,
            "selected_labels": ["系统奇遇"],
            "message": "情节 至少需要 3 个标签，当前仅 1 个，按 partial_success 返回现有标签。",
        }
    ]
    assert tagging_detail["result_status"] == "partial_success"
    assert job_result["signals"]["result_status"] == "partial_success"
    assert job_result["signals"]["validation_issue_count"] == 1


@pytest.mark.asyncio
async def test_run_model_flow_returns_partial_success_for_missing_required_category(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(payload["rs_default_tag_bundle"]["tag_schema_snapshot"]))
    selected_tags.pop("000006")
    responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": "123", "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            {"selected_tags": selected_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    final_tags = json.loads((tmp_path / "outputs" / "final_tags.json").read_text(encoding="utf-8"))
    tagging_detail = json.loads((tmp_path / "outputs" / "tagging_detail.json").read_text(encoding="utf-8"))
    job_result = json.loads((tmp_path / "outputs" / "job_result.json").read_text(encoding="utf-8"))
    assert final_tags["tags"]["000006"] == []
    assert tagging_detail["validation_issues"] == [
        {
            "category_id": "000006",
            "category_name": "情绪",
            "issue": "missing_required_category",
            "min_items": 1,
            "max_items": 1,
            "actual_items": 0,
            "selected_labels": [],
            "message": "情绪 是必填分类，当前未返回标签，按 partial_success 返回空数组。",
        }
    ]
    assert tagging_detail["result_status"] == "partial_success"
    assert job_result["signals"]["result_status"] == "partial_success"
    assert job_result["signals"]["validation_issue_count"] == 1


@pytest.mark.asyncio
async def test_run_model_flow_does_not_fill_required_category_from_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    selected_tags = selected_tags_from_final_tags(valid_final_tags(payload["rs_default_tag_bundle"]["tag_schema_snapshot"]))
    selected_tags.pop("000006")
    candidate_emotion = {"category_id": "000006", "label_id": "emotion-satisfy", "weight": 1.0}
    responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {
                "t_book_id": "123",
                "category_decisions": {"情绪": candidate_emotion},
                "raw_candidates": [],
                "uncertainties": [],
            },
            {"selected_tags": selected_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    final_tags = json.loads((tmp_path / "outputs" / "final_tags.json").read_text(encoding="utf-8"))
    tagging_detail = json.loads((tmp_path / "outputs" / "tagging_detail.json").read_text(encoding="utf-8"))
    normalization_warnings = json.loads((tmp_path / "intermediate" / "normalization_warnings.json").read_text(encoding="utf-8"))
    assert final_tags["tags"]["000006"] == []
    assert tagging_detail["validation_issues"][0]["issue"] == "missing_required_category"
    assert normalization_warnings == []


@pytest.mark.asyncio
async def test_run_model_flow_normalizes_numeric_t_book_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    final_tags = valid_final_tags(payload["rs_default_tag_bundle"]["tag_schema_snapshot"])
    final_tags["t_book_id"] = 123
    responses = iter(
        [
            {"t_book_id": 123, "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": 123, "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            {"final_tags": final_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    story_overview = json.loads((tmp_path / "intermediate" / "story_overview_result.json").read_text(encoding="utf-8"))
    candidate_tags = json.loads((tmp_path / "intermediate" / "candidate_tags.json").read_text(encoding="utf-8"))
    written_final_tags = json.loads((tmp_path / "outputs" / "final_tags.json").read_text(encoding="utf-8"))
    normalization_warnings = json.loads((tmp_path / "intermediate" / "normalization_warnings.json").read_text(encoding="utf-8"))
    assert story_overview["t_book_id"] == "123"
    assert candidate_tags["t_book_id"] == "123"
    assert written_final_tags["t_book_id"] == "123"
    assert normalization_warnings == [
        {
            "stage": "story_overview",
            "field": "t_book_id",
            "from_type": "int",
            "from_value": "123",
            "to_type": "str",
            "to_value": "123",
        },
        {
            "stage": "candidate_tagging",
            "field": "t_book_id",
            "from_type": "int",
            "from_value": "123",
            "to_type": "str",
            "to_value": "123",
        },
        {
            "stage": "finalize.final_tags",
            "field": "t_book_id",
            "from_type": "int",
            "from_value": "123",
            "to_type": "str",
            "to_value": "123",
        },
    ]


@pytest.mark.asyncio
async def test_run_model_flow_writes_label_text_normalization_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    final_tags = valid_final_tags(payload["rs_default_tag_bundle"]["tag_schema_snapshot"])
    final_tags["tags"]["000001"][0]["标签释义"] = "model paraphrase"
    responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": "123", "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            {"final_tags": final_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    written_final_tags = json.loads((tmp_path / "outputs" / "final_tags.json").read_text(encoding="utf-8"))
    normalization_warnings = json.loads((tmp_path / "intermediate" / "normalization_warnings.json").read_text(encoding="utf-8"))
    assert written_final_tags["tags"]["000001"][0]["标签释义"] == "男性受众"
    assert normalization_warnings == []


@pytest.mark.asyncio
async def test_run_model_flow_normalizes_candidate_label_id_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    final_tags = valid_final_tags(payload["rs_default_tag_bundle"]["tag_schema_snapshot"])
    responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {
                "t_book_id": "123",
                "category_decisions": {
                    "受众": {"category_id": "000001", "label_id": "audience-mal", "weight": 1.0},
                },
                "raw_candidates": [],
                "uncertainties": [],
            },
            {"final_tags": final_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    candidate_tags = json.loads((tmp_path / "intermediate" / "candidate_tags.json").read_text(encoding="utf-8"))
    normalization_warnings = json.loads((tmp_path / "intermediate" / "normalization_warnings.json").read_text(encoding="utf-8"))
    assert candidate_tags["category_decisions"]["受众"]["label_id"] == "audience-male"
    assert normalization_warnings == [
        {
            "stage": "candidate_tagging",
            "field": "category_decisions.受众.label_id",
            "from_type": "str",
            "from_value": "audience-mal",
            "to_type": "str",
            "to_value": "audience-male",
        }
    ]


@pytest.mark.asyncio
async def test_run_model_flow_overwrites_stale_normalization_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    schema = payload["rs_default_tag_bundle"]["tag_schema_snapshot"]

    first_final_tags = valid_final_tags(schema)
    first_responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {
                "t_book_id": "123",
                "category_decisions": {
                    "受众": {"category_id": "000001", "label_id": "audience-mal", "weight": 1.0},
                },
                "raw_candidates": [],
                "uncertainties": [],
            },
            {"final_tags": first_final_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def first_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(first_responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", first_call_model)
    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )
    assert json.loads((tmp_path / "intermediate" / "normalization_warnings.json").read_text(encoding="utf-8"))

    second_final_tags = valid_final_tags(schema)
    second_responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": "123", "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            {"final_tags": second_final_tags, "tagging_detail": {"rule_applications": []}},
        ]
    )

    async def second_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(second_responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", second_call_model)
    await runner.run_model_flow(
        payload,
        tmp_path,
        workflow_definition(),
        prompt_templates(),
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    assert json.loads((tmp_path / "intermediate" / "normalization_warnings.json").read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_run_model_flow_writes_prompts_before_stage_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return "not json"

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    with pytest.raises(ValueError, match="not valid JSON"):
        await runner.run_model_flow(
            payload,
            tmp_path,
            workflow_definition(),
            prompt_templates(),
            model="fake",
            temperature=0,
            timeout_seconds=1,
        )

    prompts = json.loads((tmp_path / "intermediate" / "prompts.json").read_text(encoding="utf-8"))
    assert set(prompts) == {"story_overview"}
    assert (tmp_path / "intermediate" / "story_overview_raw_output.txt").read_text(encoding="utf-8") == "not json"


@pytest.mark.asyncio
async def test_run_model_flow_writes_raw_output_for_non_object_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return "[]"

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    with pytest.raises(ValueError, match="must be a JSON object"):
        await runner.run_model_flow(
            payload,
            tmp_path,
            workflow_definition(),
            prompt_templates(),
            model="fake",
            temperature=0,
            timeout_seconds=1,
        )

    assert (tmp_path / "intermediate" / "story_overview_raw_output.txt").read_text(encoding="utf-8") == "[]"


@pytest.mark.asyncio
async def test_call_model_requests_json_object_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_acompletion(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"ok": true}'),
                )
            ]
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=fake_acompletion))

    result = await runner.call_model(
        "gpt-4o-mini",
        [{"role": "user", "content": "Return JSON"}],
        temperature=0,
        timeout_seconds=1,
    )

    assert result == '{"ok": true}'
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["model"] == "openai/gpt-4o-mini"
    assert "drop_params" not in captured


@pytest.mark.asyncio
async def test_run_model_flow_rejects_mismatched_t_book_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    responses = iter(
        [
            {"t_book_id": "wrong", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    with pytest.raises(ValueError, match="story_overview.t_book_id"):
        await runner.run_model_flow(
            payload,
            tmp_path,
            workflow_definition(),
            prompt_templates(),
            model="fake",
            temperature=0,
            timeout_seconds=1,
        )
    assert (tmp_path / "intermediate" / "story_overview_raw_output.txt").exists()


@pytest.mark.asyncio
async def test_run_model_flow_writes_raw_output_for_final_validation_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    bad_final_result = {"final_tags": {}, "tagging_detail": {"rule_applications": []}}
    responses = iter(
        [
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            {"t_book_id": "123", "category_decisions": [], "raw_candidates": [], "uncertainties": []},
            bad_final_result,
        ]
    )

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    with pytest.raises(ValueError, match="finalize.final_tags.t_book_id must be 123: None"):
        await runner.run_model_flow(
            payload,
            tmp_path,
            workflow_definition(),
            prompt_templates(),
            model="fake",
            temperature=0,
            timeout_seconds=1,
        )

    raw_output = (tmp_path / "intermediate" / "finalize_raw_output.txt").read_text(encoding="utf-8")
    assert json.loads(raw_output) == bad_final_result


@pytest.mark.asyncio
async def test_run_model_flow_rejects_artifact_name_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = input_payload()
    workflow = workflow_definition()
    workflow["stages"][0]["output_artifact"] = "renamed_story.json"

    async def fake_call_model(*args: object, **kwargs: object) -> str:
        return json.dumps(
            {"t_book_id": "123", "analysis_status": "ok", "characters": [], "world_setting": {}, "plot_timeline": [], "main_conflicts": [], "uncertainties": []},
            ensure_ascii=False,
        )

    monkeypatch.setattr(runner, "call_model", fake_call_model)

    with pytest.raises(ValueError, match="missing input artifact"):
        await runner.run_model_flow(
            payload,
            tmp_path,
            workflow,
            prompt_templates(),
            model="fake",
            temperature=0,
            timeout_seconds=1,
        )


def test_builder_output_can_feed_runner_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_root = tmp_path / "structured"
    schema = minimal_tag_schema()
    material = input_payload()["job_params"]
    builder.write_book_inputs(
        output_root,
        [
            {
                "t_book_id": material["t_book_id"],
                "work_context": material["work_context"],
                "assets": material["assets"],
            }
        ],
        schema,
        [],
    )

    async def fail_call_model(*args: object, **kwargs: object) -> str:
        raise AssertionError("dry-run must not call model")

    monkeypatch.setattr(runner, "call_model", fail_call_model)

    result = runner.run_one_input(
        output_root / "jobs" / "per_book" / "123" / "input.json",
        tmp_path / "model_runs",
        workflow_definition(),
        prompt_templates(),
        run_model=False,
        model="fake",
        temperature=0,
        timeout_seconds=1,
    )

    assert result["t_book_id"] == "123"
    assert (tmp_path / "model_runs" / "per_book" / "123" / "intermediate" / "prompts.json").exists()
    assert not (tmp_path / "model_runs" / "per_book" / "123" / "outputs").exists()


def test_dry_run_writes_prompts_before_later_stage_failure(tmp_path: Path) -> None:
    output_root = tmp_path / "structured"
    material = input_payload()["job_params"]
    builder.write_book_inputs(
        output_root,
        [
            {
                "t_book_id": material["t_book_id"],
                "work_context": material["work_context"],
                "assets": material["assets"],
            }
        ],
        minimal_tag_schema(),
        [],
    )
    templates = prompt_templates()
    templates["candidate_tagging_v1"] = {
        **templates["candidate_tagging_v1"],
        "messages": [{"role": "user", "template": "{{unknown_after_story}}"}],
    }

    with pytest.raises(ValueError, match="unknown variable"):
        runner.run_one_input(
            output_root / "jobs" / "per_book" / "123" / "input.json",
            tmp_path / "model_runs",
            workflow_definition(),
            templates,
            run_model=False,
            model="fake",
            temperature=0,
            timeout_seconds=1,
        )

    prompts = json.loads(
        (tmp_path / "model_runs" / "per_book" / "123" / "intermediate" / "prompts.json").read_text(encoding="utf-8")
    )
    assert set(prompts) == {"story_overview"}


def test_run_inputs_respects_concurrency_and_preserves_result_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_paths = [
        tmp_path / "inputs" / "jobs" / "per_book" / t_book_id / "input.json"
        for t_book_id in ["1", "2", "3"]
    ]
    for input_path in input_paths:
        payload = input_payload()
        payload["job_params"]["t_book_id"] = input_path.parent.name
        input_path.parent.mkdir(parents=True)
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    active_count = 0
    max_active_count = 0

    async def fake_run_one_input_async(
        input_path: Path,
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0.01)
        active_count -= 1
        return {"t_book_id": input_path.parent.name, "output_dir": str(tmp_path / "runs" / input_path.parent.name)}

    monkeypatch.setattr(runner, "run_one_input_async", fake_run_one_input_async)

    results = runner.run_inputs(
        input_paths,
        tmp_path / "runs",
        workflow_definition(),
        prompt_templates(),
        run_model=True,
        model="fake",
        temperature=0,
        timeout_seconds=1,
        concurrency=2,
    )

    assert max_active_count == 2
    assert [result["t_book_id"] for result in results] == ["1", "2", "3"]


def test_run_inputs_rejects_invalid_concurrency(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--concurrency must be >= 1"):
        runner.run_inputs(
            [],
            tmp_path / "runs",
            workflow_definition(),
            prompt_templates(),
            run_model=False,
            model="fake",
            temperature=0,
            timeout_seconds=1,
            concurrency=0,
        )


def test_run_inputs_rejects_duplicate_t_book_id_output_dirs(tmp_path: Path) -> None:
    payload = input_payload()
    input_paths = [
        tmp_path / "inputs" / "jobs" / "per_book" / "copy-a" / "input.json",
        tmp_path / "inputs" / "jobs" / "per_book" / "copy-b" / "input.json",
    ]
    for input_path in input_paths:
        input_path.parent.mkdir(parents=True)
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate t_book_id would write the same output directory"):
        runner.run_inputs(
            input_paths,
            tmp_path / "runs",
            workflow_definition(),
            prompt_templates(),
            run_model=False,
            model="fake",
            temperature=0,
            timeout_seconds=1,
            concurrency=2,
        )


def test_validate_input_payload_rejects_non_string_t_book_id() -> None:
    payload = input_payload()
    payload["job_params"]["t_book_id"] = 123

    with pytest.raises(ValueError, match="job_params.t_book_id must be a non-empty string"):
        runner.validate_input_payload(payload, Path("input.json"))


def test_run_inputs_adds_input_context_to_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path = tmp_path / "inputs" / "jobs" / "per_book" / "123" / "input.json"
    input_path.parent.mkdir(parents=True)
    input_path.write_text(json.dumps(input_payload(), ensure_ascii=False), encoding="utf-8")

    async def fake_run_one_input_async(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ValueError("stage failed")

    monkeypatch.setattr(runner, "run_one_input_async", fake_run_one_input_async)

    with pytest.raises(RuntimeError, match=r"input_path=.*input\.json, t_book_id=123: stage failed"):
        runner.run_inputs(
            [input_path],
            tmp_path / "runs",
            workflow_definition(),
            prompt_templates(),
            run_model=True,
            model="fake",
            temperature=0,
            timeout_seconds=1,
            concurrency=2,
        )


def test_main_requires_explicit_config_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(input_payload(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "parse_args",
        lambda: SimpleNamespace(
            poc_root=tmp_path / "poc",
            input_jsons=[input_path],
            input_dir=None,
            output_dir=tmp_path / "runs",
            config_json=tmp_path / "missing_config.json",
            workflow_json=None,
            prompt_templates_json=None,
            limit=None,
            concurrency=None,
            dry_run=True,
            run_model=False,
            model=None,
            temperature=None,
            timeout_seconds=None,
        ),
    )

    with pytest.raises(FileNotFoundError, match="JSON file not found"):
        runner.main()


def test_main_uses_poc_root_runtime_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    poc_root = tmp_path / "short_drama_tagging"
    input_path = poc_root / "inputs" / "jobs" / "per_book" / "123" / "input.json"
    input_path.parent.mkdir(parents=True)
    input_path.write_text(json.dumps(input_payload(), ensure_ascii=False), encoding="utf-8")
    config_path = poc_root / "config" / "ai_tagging_poc_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "default_model": "fake",
                "temperature": 0,
                "timeout_seconds": 1,
                "workflow_definition": "workflow_definition.json",
                "prompt_templates": "prompt_templates.json",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (poc_root / "config" / "workflow_definition.json").write_text(
        json.dumps(builder.build_workflow_definition(), ensure_ascii=False),
        encoding="utf-8",
    )
    (poc_root / "config" / "prompt_templates.json").write_text(
        json.dumps(builder.build_prompt_templates(), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "parse_args",
        lambda: SimpleNamespace(
            poc_root=poc_root,
            input_jsons=None,
            input_dir=None,
            output_dir=None,
            config_json=None,
            workflow_json=None,
            prompt_templates_json=None,
            limit=None,
            concurrency=None,
            dry_run=True,
            run_model=False,
            model=None,
            temperature=None,
            timeout_seconds=None,
        ),
    )

    assert runner.main() == 0
    assert (poc_root / "runs" / "latest" / "per_book" / "123" / "intermediate" / "prompts.json").exists()


def test_main_uses_workflow_and_prompt_paths_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    poc_root = tmp_path / "short_drama_tagging"
    input_path = poc_root / "inputs" / "jobs" / "per_book" / "123" / "input.json"
    input_path.parent.mkdir(parents=True)
    input_path.write_text(json.dumps(input_payload(), ensure_ascii=False), encoding="utf-8")
    config_path = poc_root / "config" / "ai_tagging_poc_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "default_model": "fake",
                "temperature": 0,
                "timeout_seconds": 1,
                "workflow_definition": "custom_workflow.json",
                "prompt_templates": "custom_prompt_templates.json",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (poc_root / "config" / "custom_workflow.json").write_text(
        json.dumps(builder.build_workflow_definition(), ensure_ascii=False),
        encoding="utf-8",
    )
    custom_templates = builder.build_prompt_templates()
    custom_templates["templates"][0]["messages"][0]["template"] = "CUSTOM_MARKER {{material_text}}"
    (poc_root / "config" / "custom_prompt_templates.json").write_text(
        json.dumps(custom_templates, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "parse_args",
        lambda: SimpleNamespace(
            poc_root=poc_root,
            input_jsons=None,
            input_dir=None,
            output_dir=None,
            config_json=None,
            workflow_json=None,
            prompt_templates_json=None,
            limit=None,
            concurrency=None,
            dry_run=True,
            run_model=False,
            model=None,
            temperature=None,
            timeout_seconds=None,
        ),
    )

    assert runner.main() == 0
    prompts = json.loads(
        (poc_root / "runs" / "latest" / "per_book" / "123" / "intermediate" / "prompts.json").read_text(encoding="utf-8")
    )
    assert "CUSTOM_MARKER" in prompts["story_overview"][0]["content"]
