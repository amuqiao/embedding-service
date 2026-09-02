from __future__ import annotations

import json

import pytest

from smoke.flows.asset import search_eval
from smoke.harness.errors import FlowError
from smoke.harness.service_runtime import RuntimeContext


def test_asset_search_eval_builtin_datasets_are_self_contained():
    for dataset_name, path in search_eval.DATASET_ALIASES.items():
        dataset = search_eval._load_dataset(path)
        search_eval._validate_dataset(dataset)

        assert dataset["dataset_range"] == dataset_name
        assert dataset["items"]
        assert dataset["label_snapshot"]
        assert dataset["search_cases"]
        assert all(item["asset"]["public_url"].startswith("https://") for item in dataset["items"])


def test_asset_search_eval_slices_items_labels_and_search_cases():
    dataset = search_eval._load_dataset(search_eval.DATASET_ALIASES["regression"])

    sliced = search_eval._slice_dataset(dataset, item_limit=1)
    search_eval._validate_dataset(sliced)

    item_ids = {item["item_id"] for item in sliced["items"]}
    category_ids = {item["category_id"] for item in sliced["items"]}
    assert len(sliced["items"]) == 1
    assert {group["category_id"] for group in sliced["label_snapshot"]} == category_ids
    for case in sliced["search_cases"]:
        assert set(case["expected_item_ids"]) <= item_ids


def test_asset_search_eval_requires_full_confirmation_before_service_context(tmp_path):
    output_dir = tmp_path / "full-guard"

    with pytest.raises(FlowError, match="--confirm-full-batch"):
        search_eval.run(
            confirm_run=True,
            confirm_cost=True,
            confirm_full_batch=False,
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            client_request_id=None,
            dataset="full",
            item_limit=None,
            batch_size=10,
            output_dir=str(output_dir),
            cleanup=True,
            json_output=True,
        )

    run_summary = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    assert run_summary["ok"] is False
    assert run_summary["phase"] == "load_dataset"
    assert run_summary["error"]["message"] == "--confirm-full-batch is required when running a full asset_search_eval dataset"


def test_asset_search_eval_builds_vector_upsert_from_tagging_result():
    dataset = search_eval._slice_dataset(search_eval._load_dataset(search_eval.DATASET_ALIASES["smoke"]), item_limit=1)
    item = dataset["items"][0]
    tagging_result = {
        "job_type": "asset_image_tagging",
        "items": [
            {
                "item_id": item["item_id"],
                "item_name": item["item_name"],
                "category_id": item["category_id"],
                "category_name": item["category_name"],
                "asset": item["asset"],
                "status": "succeeded",
                "label_group_selections": [
                    {
                        "label_snapshot_index": 0,
                        "category_id": item["category_id"],
                        "category_name": item["category_name"],
                        "selection_mode": "single",
                        "labels": [
                            {
                                "label_id": "object_type_gift",
                                "label_name": "礼物",
                                "definition": "物件为礼物、礼盒类",
                                "weight": 0.9,
                            }
                        ],
                    }
                ],
                "asset_description": {"language": "zh", "text": "一个礼物盒"},
                "validation_issues": [],
            }
        ],
    }

    vector_items = search_eval._vector_upsert_payload(dataset, tagging_result, run_id="run-1")

    assert vector_items == [
        {
            "item_id": item["item_id"],
            "item_name": item["item_name"],
            "asset": item["asset"],
            "labels": [
                {
                    "label_id": "object_type_gift",
                    "language": "zh",
                    "label_name": "礼物",
                    "definition": "物件为礼物、礼盒类",
                }
            ],
            "metadata": {
                "source": "scripts/smoke.sh asset-search-eval",
                "run_id": "run-1",
                "category_id": item["category_id"],
                "category_name": item["category_name"],
                "asset_description": {"language": "zh", "text": "一个礼物盒"},
            },
        }
    ]


def test_asset_search_eval_rejects_unknown_tagging_item():
    dataset = search_eval._slice_dataset(search_eval._load_dataset(search_eval.DATASET_ALIASES["smoke"]), item_limit=1)
    tagging_result = {
        "job_type": "asset_image_tagging",
        "items": [{"item_id": "unknown", "status": "succeeded", "label_group_selections": []}],
    }

    with pytest.raises(FlowError, match="unknown item_id"):
        search_eval._vector_upsert_payload(dataset, tagging_result, run_id="run-1")


def test_asset_search_eval_batch_result_checks_job_contract():
    job = {
        "job_result": {
            "job_type": search_eval.UPSERT_JOB_TYPE,
            "batch_summary": {"total": 1, "succeeded": 1},
            "items": [{"item_id": "asset-1", "status": "succeeded"}],
        }
    }

    result = search_eval._assert_batch_result(
        job,
        job_type=search_eval.UPSERT_JOB_TYPE,
        item_ids=["asset-1"],
        count_key="succeeded",
    )

    assert result["job_type"] == search_eval.UPSERT_JOB_TYPE


def test_asset_search_eval_batch_result_rejects_item_mismatch():
    job = {
        "job_result": {
            "job_type": search_eval.UPSERT_JOB_TYPE,
            "batch_summary": {"total": 1, "succeeded": 1},
            "items": [{"item_id": "asset-2", "status": "succeeded"}],
        }
    }

    with pytest.raises(FlowError, match="item_ids mismatch"):
        search_eval._assert_batch_result(
            job,
            job_type=search_eval.UPSERT_JOB_TYPE,
            item_ids=["asset-1"],
            count_key="succeeded",
        )


def test_asset_search_eval_rejects_invalid_index_state_schema(tmp_path):
    path = tmp_path / "index-state.json"
    path.write_text(json.dumps({"item_ids": ["asset-1"]}), encoding="utf-8")

    with pytest.raises(FlowError, match="index-state schema mismatch"):
        search_eval._load_index_state(str(path))


def test_asset_search_eval_search_case_candidates_must_stay_inside_index_state():
    case = {
        "case_id": "case-1",
        "search_mode": "text",
        "text": {"query": "礼物"},
        "candidate_item_ids": ["asset-1", "asset-2"],
    }

    with pytest.raises(FlowError, match="outside index-state item_ids"):
        search_eval._search_payload(case, source_items={}, candidate_item_ids=["asset-1"])


def test_asset_search_eval_cleans_up_successful_upserts_when_later_batch_fails(tmp_path, monkeypatch):
    dataset = search_eval._slice_dataset(search_eval._load_dataset(search_eval.DATASET_ALIASES["smoke"]), item_limit=2)
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "reports"
    cleanup_calls: list[list[str]] = []

    def fake_context(**_kwargs):
        return RuntimeContext(
            app_env={"DISABLE_HTTP_AUTH_HEADER": "true"},
            summary={
                "ready": True,
                "problems": [],
                "api_url": "http://127.0.0.1:18210",
                "api_prefix": "/api/v1",
                "jobs_url": "http://127.0.0.1:18210/api/v1/ai-jobs/jobs",
            },
        )

    def fake_tagging_batches(*, request_path, **_kwargs):
        result_items = []
        for item in dataset["items"]:
            result_items.append(
                {
                    "item_id": item["item_id"],
                    "item_name": item["item_name"],
                    "category_id": item["category_id"],
                    "category_name": item["category_name"],
                    "asset": item["asset"],
                    "status": "succeeded",
                    "label_group_selections": [
                        {
                            "label_snapshot_index": 0,
                            "category_id": item["category_id"],
                            "category_name": item["category_name"],
                            "selection_mode": "single",
                            "labels": [
                                {
                                    "label_id": "label-1",
                                    "label_name": "标签",
                                    "definition": "测试标签",
                                    "weight": 1,
                                }
                            ],
                        }
                    ],
                    "asset_description": {"language": "zh", "text": "测试描述"},
                    "validation_issues": [],
                }
            )
        payloads = [{"job_type": search_eval.TAGGING_JOB_TYPE}]
        request_path.write_text(json.dumps({"job_payloads": payloads}), encoding="utf-8")
        return payloads, {"job_type": search_eval.TAGGING_JOB_TYPE, "job_ids": ["tag-1"], "items": result_items}

    def fake_upsert_batches(*, vector_items, request_path, indexed_item_ids, **_kwargs):
        first_item_id = str(vector_items[0]["item_id"])
        indexed_item_ids.append(first_item_id)
        request_path.write_text(json.dumps({"items": vector_items}), encoding="utf-8")
        raise FlowError("second upsert batch failed", exit_code=1)

    def fake_cleanup(*, item_ids, **_kwargs):
        cleanup_calls.append(list(item_ids))
        return {
            "job_type": search_eval.DELETE_JOB_TYPE,
            "job_ids": ["delete-1"],
            "batch_summary": {"total": len(item_ids), "deleted": len(item_ids)},
            "items": [{"item_id": item_id, "status": "deleted"} for item_id in item_ids],
        }

    monkeypatch.setattr(search_eval.job_runtime, "resolve_job_context", fake_context)
    monkeypatch.setattr(search_eval.service_runtime, "build_headers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(search_eval, "_submit_tagging_batches", fake_tagging_batches)
    monkeypatch.setattr(search_eval, "_submit_vector_upsert_batches", fake_upsert_batches)
    monkeypatch.setattr(search_eval, "_cleanup", fake_cleanup)

    with pytest.raises(FlowError, match="second upsert batch failed"):
        search_eval.run(
            confirm_run=True,
            confirm_cost=True,
            confirm_full_batch=False,
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            client_request_id=None,
            dataset=str(dataset_path),
            item_limit=None,
            batch_size=1,
            output_dir=str(output_dir),
            cleanup=True,
            json_output=True,
        )

    assert cleanup_calls == [[dataset["items"][0]["item_id"]]]
    run_summary = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    assert run_summary["ok"] is False
    assert run_summary["phase"] == "vector_upsert"
    assert run_summary["indexed_item_ids"] == [dataset["items"][0]["item_id"]]
    assert run_summary["cleanup_error"] is None
    assert (output_dir / "tagging-request.json").is_file()
    assert (output_dir / "vector-upsert-input.json").is_file()


def test_asset_search_eval_preserves_original_error_when_cleanup_fails(tmp_path, monkeypatch):
    dataset = search_eval._slice_dataset(search_eval._load_dataset(search_eval.DATASET_ALIASES["smoke"]), item_limit=1)
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "reports"

    def fake_context(**_kwargs):
        return RuntimeContext(
            app_env={"DISABLE_HTTP_AUTH_HEADER": "true"},
            summary={
                "ready": True,
                "problems": [],
                "api_url": "http://127.0.0.1:18210",
                "api_prefix": "/api/v1",
                "jobs_url": "http://127.0.0.1:18210/api/v1/ai-jobs/jobs",
            },
        )

    def fake_tagging_batches(**_kwargs):
        item = dataset["items"][0]
        return [], {
            "job_type": search_eval.TAGGING_JOB_TYPE,
            "job_ids": ["tag-1"],
            "items": [
                {
                    "item_id": item["item_id"],
                    "item_name": item["item_name"],
                    "category_id": item["category_id"],
                    "category_name": item["category_name"],
                    "asset": item["asset"],
                    "status": "succeeded",
                    "label_group_selections": [],
                    "asset_description": {"language": "zh", "text": "测试描述"},
                    "validation_issues": [],
                }
            ],
        }

    def fake_upsert_batches(*, vector_items, indexed_item_ids, **_kwargs):
        indexed_item_ids.append(str(vector_items[0]["item_id"]))
        raise FlowError("search setup failed", exit_code=1)

    def fake_cleanup(**_kwargs):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(search_eval.job_runtime, "resolve_job_context", fake_context)
    monkeypatch.setattr(search_eval.service_runtime, "build_headers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(search_eval, "_submit_tagging_batches", fake_tagging_batches)
    monkeypatch.setattr(search_eval, "_submit_vector_upsert_batches", fake_upsert_batches)
    monkeypatch.setattr(search_eval, "_cleanup", fake_cleanup)

    with pytest.raises(FlowError, match="search setup failed; cleanup_failed=RuntimeError: cleanup failed"):
        search_eval.run(
            confirm_run=True,
            confirm_cost=True,
            confirm_full_batch=False,
            api_url=None,
            env_file=None,
            allow_remote_api=False,
            service_api_key=None,
            caller_id="smoke-cli",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
            client_request_id=None,
            dataset=str(dataset_path),
            item_limit=None,
            batch_size=1,
            output_dir=str(output_dir),
            cleanup=True,
            json_output=True,
        )

    run_summary = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    assert run_summary["error"]["message"] == "search setup failed"
    assert run_summary["cleanup_error"] == "RuntimeError: cleanup failed"


def test_asset_search_eval_metrics_and_html_outputs(tmp_path):
    dataset = search_eval._slice_dataset(search_eval._load_dataset(search_eval.DATASET_ALIASES["smoke"]), item_limit=1)
    item = dataset["items"][0]
    tagging_rows = [
        {
            "item_id": item["item_id"],
            "item_name": item["item_name"],
            "category_id": item["category_id"],
            "category_name": item["category_name"],
            "asset": item["asset"],
            "selected_label_ids": ["object_type_gift"],
            "selected_label_names": ["礼物"],
            "expected_label_ids": ["object_type_gift"],
            "matched_expected_labels": True,
            "missing_label_ids": [],
            "extra_label_ids": [],
        }
    ]
    search_rows = [
        {
            "case_id": "text-gift-box",
            "search_mode": "text",
            "query": {"text": {"query": "礼物"}},
            "expected_item_ids": [item["item_id"]],
            "returned_item_ids": [item["item_id"]],
            "hit_at_1": True,
            "hit_at_k": True,
            "best_rank": 1,
            "reciprocal_rank": 1,
        }
    ]

    metrics = search_eval._metrics(tagging_rows, search_rows)
    artifacts = search_eval._artifacts(tmp_path / "eval-run")
    search_eval._write_html_reports(
        artifacts,
        run_summary={
            "run_id": "run-1",
            "dataset": "smoke",
            "finished_at": "2026-09-02T00:00:00Z",
        },
        dataset=dataset,
        tagging_rows=tagging_rows,
        search_rows=search_rows,
        metrics=metrics,
    )

    assert metrics["tagging"]["required_match_rate"] == 1
    assert metrics["search"]["hit_at_1"] == 1
    assert artifacts.index_html.is_file()
    assert artifacts.tagging_report_html.is_file()
    assert artifacts.search_report_html.is_file()
    assert "Asset Search Eval" in artifacts.index_html.read_text(encoding="utf-8")
    assert json.loads(json.dumps(metrics)) == metrics


def test_asset_search_eval_single_html_writers_create_parent_dirs(tmp_path):
    dataset = search_eval._slice_dataset(search_eval._load_dataset(search_eval.DATASET_ALIASES["smoke"]), item_limit=1)
    item = dataset["items"][0]
    tagging_rows = [
        {
            "item_id": item["item_id"],
            "item_name": item["item_name"],
            "category_name": item["category_name"],
            "asset": item["asset"],
            "selected_label_names": ["礼物"],
            "expected_label_ids": ["object_type_gift"],
            "matched_expected_labels": True,
        }
    ]
    search_rows = [
        {
            "case_id": "text-gift-box",
            "search_mode": "text",
            "query": {"text": {"query": "礼物"}},
            "expected_item_ids": [item["item_id"]],
            "returned_item_ids": [item["item_id"]],
            "best_rank": 1,
        }
    ]

    search_eval._write_tagging_html(tmp_path / "new-html" / "tagging.html", tagging_rows=tagging_rows)
    search_eval._write_search_html(
        tmp_path / "another-html" / "search.html",
        search_rows=search_rows,
        item_by_id={item["item_id"]: item},
    )

    assert (tmp_path / "new-html" / "tagging.html").is_file()
    assert (tmp_path / "another-html" / "search.html").is_file()
