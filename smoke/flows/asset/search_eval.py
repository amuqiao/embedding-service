from __future__ import annotations

import copy
import html
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smoke.harness import formatters, http_runtime, service_runtime
from smoke.harness.errors import FlowError
from smoke.jobs import runtime as job_runtime

SCENARIO_NAME = "asset-search-eval"
TAGGING_JOB_TYPE = "asset_image_tagging"
UPSERT_JOB_TYPE = "asset_vector_batch_upsert"
DELETE_JOB_TYPE = "asset_vector_batch_delete"
ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_ROOT = ROOT_DIR / "poc/asset-vector/reports/evals"
INDEX_STATE_SCHEMA = "asset_search_eval_index_state.v1"
DATASET_ALIASES = {
    "smoke": ROOT_DIR / "smoke/fixtures/asset_search_eval/smoke.zh.json",
    "regression": ROOT_DIR / "smoke/fixtures/asset_search_eval/regression.zh.json",
    "full": ROOT_DIR / "smoke/fixtures/asset_search_eval/full.zh.json",
}
DEFAULT_BATCH_SIZE = 10


@dataclass(frozen=True)
class SearchEvalArtifacts:
    output_dir: Path
    html_dir: Path
    run_json: Path
    dataset_snapshot_json: Path
    tagging_request_json: Path
    tagging_result_json: Path
    tagging_items_jsonl: Path
    vector_upsert_input_json: Path
    vector_upsert_result_json: Path
    index_state_json: Path
    vector_exists_result_json: Path
    search_cases_jsonl: Path
    search_results_jsonl: Path
    vector_delete_result_json: Path
    metrics_json: Path
    index_html: Path
    tagging_report_html: Path
    search_report_html: Path


def _api_url(context: service_runtime.RuntimeContext, path: str) -> str:
    return f"{context.summary['api_url']}{context.summary['api_prefix']}{path}"


def _run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _resolve_dataset_path(dataset: str) -> tuple[str, Path, bool]:
    alias_path = DATASET_ALIASES.get(dataset)
    if alias_path is not None:
        return dataset, alias_path, dataset == "full"
    path = Path(dataset).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return "custom", path, False


def _resolve_output_dir(output_dir: str | None, *, run_id: str) -> Path:
    if output_dir is None:
        return DEFAULT_REPORTS_ROOT / run_id
    path = Path(output_dir).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _artifacts(output_dir: Path) -> SearchEvalArtifacts:
    html_dir = output_dir / "html"
    return SearchEvalArtifacts(
        output_dir=output_dir,
        html_dir=html_dir,
        run_json=output_dir / "run.json",
        dataset_snapshot_json=output_dir / "dataset.snapshot.json",
        tagging_request_json=output_dir / "tagging-request.json",
        tagging_result_json=output_dir / "tagging-result.json",
        tagging_items_jsonl=output_dir / "tagging-items.jsonl",
        vector_upsert_input_json=output_dir / "vector-upsert-input.json",
        vector_upsert_result_json=output_dir / "vector-upsert-result.json",
        index_state_json=output_dir / "index-state.json",
        vector_exists_result_json=output_dir / "vector-exists-result.json",
        search_cases_jsonl=output_dir / "search-cases.jsonl",
        search_results_jsonl=output_dir / "search-results.jsonl",
        vector_delete_result_json=output_dir / "vector-delete-result.json",
        metrics_json=output_dir / "metrics.json",
        index_html=html_dir / "index.html",
        tagging_report_html=html_dir / "tagging-report.html",
        search_report_html=html_dir / "search-report.html",
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=formatters.json_default) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=formatters.json_default) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FlowError(f"asset_search_eval JSON file not found: {path}", exit_code=2)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FlowError(f"asset_search_eval file must be valid JSON: {path}: {exc}", exit_code=2) from exc
    if not isinstance(data, dict):
        raise FlowError(f"asset_search_eval file must contain a JSON object: {path}", exit_code=2)
    return data


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FlowError(f"asset_search_eval JSONL file not found: {path}", exit_code=2)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FlowError(f"asset_search_eval JSONL line is invalid: {path}:{line_number}: {exc}", exit_code=2) from exc
        if not isinstance(row, dict):
            raise FlowError(f"asset_search_eval JSONL line must be an object: {path}:{line_number}", exit_code=2)
        rows.append(row)
    if not rows:
        raise FlowError(f"asset_search_eval JSONL file has no rows: {path}", exit_code=2)
    return rows


def _load_dataset(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FlowError(f"asset_search_eval dataset not found: {path}", exit_code=2)
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FlowError(f"asset_search_eval dataset must be valid JSON: {path}: {exc}", exit_code=2) from exc
    if not isinstance(dataset, dict):
        raise FlowError("asset_search_eval dataset must be a JSON object", exit_code=2)
    items = dataset.get("items")
    label_snapshot = dataset.get("label_snapshot")
    search_cases = dataset.get("search_cases")
    if not isinstance(items, list) or not items:
        raise FlowError("asset_search_eval dataset.items must be a non-empty list", exit_code=2)
    if not isinstance(label_snapshot, list) or not label_snapshot:
        raise FlowError("asset_search_eval dataset.label_snapshot must be a non-empty list", exit_code=2)
    if not isinstance(search_cases, list) or not search_cases:
        raise FlowError("asset_search_eval dataset.search_cases must be a non-empty list", exit_code=2)
    return dataset


def _load_working_dataset(
    *,
    dataset: str,
    item_limit: int | None,
    confirm_full_batch: bool,
    require_full_confirmation: bool,
) -> tuple[str, Path, dict[str, Any]]:
    dataset_name, dataset_path, is_full_alias = _resolve_dataset_path(dataset)
    loaded_dataset = _load_dataset(dataset_path)
    is_full_dataset = (
        is_full_alias
        or loaded_dataset.get("dataset_range") == "full"
        or (item_limit is None and len(loaded_dataset["items"]) > 100)
    )
    if require_full_confirmation and is_full_dataset and item_limit is None and not confirm_full_batch:
        raise FlowError("--confirm-full-batch is required when running a full asset_search_eval dataset", exit_code=2)
    working_dataset = _slice_dataset(loaded_dataset, item_limit=item_limit)
    _validate_dataset(working_dataset)
    return dataset_name, dataset_path, working_dataset


def _resolve_vector_upsert_input_path(path: str | None, *, output_dir: Path | None) -> Path:
    if path is None:
        if output_dir is None:
            raise FlowError("--vector-upsert-input is required when --output-dir is not provided", exit_code=2)
        return output_dir / "vector-upsert-input.json"
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = ROOT_DIR / resolved
    return resolved


def _load_vector_upsert_input(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if payload.get("job_type") != UPSERT_JOB_TYPE:
        raise FlowError(f"asset_search_eval vector upsert input job_type mismatch: {path}", exit_code=2)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise FlowError(f"asset_search_eval vector upsert input missing items: {path}", exit_code=2)
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("item_id"), str):
            raise FlowError(f"asset_search_eval vector upsert input contains invalid item: {path}", exit_code=2)
    return items


def _slice_dataset(dataset: dict[str, Any], *, item_limit: int | None) -> dict[str, Any]:
    copied = copy.deepcopy(dataset)
    if item_limit is None:
        return copied
    if item_limit < 1:
        raise FlowError("--limit must be greater than or equal to 1", exit_code=2)
    copied["items"] = copied["items"][:item_limit]
    item_ids = {str(item["item_id"]) for item in copied["items"] if isinstance(item, dict)}
    category_ids = {str(item["category_id"]) for item in copied["items"] if isinstance(item, dict)}
    copied["label_snapshot"] = [
        group
        for group in copied["label_snapshot"]
        if isinstance(group, dict) and group.get("category_id") in category_ids
    ]
    search_cases: list[dict[str, Any]] = []
    for case in copied["search_cases"]:
        if not isinstance(case, dict) or not _case_is_usable(case, selected_item_ids=item_ids):
            continue
        case_copy = copy.deepcopy(case)
        if isinstance(case_copy.get("candidate_item_ids"), list):
            case_copy["candidate_item_ids"] = [
                item_id for item_id in case_copy["candidate_item_ids"] if item_id in item_ids
            ]
        search_cases.append(case_copy)
    copied["search_cases"] = search_cases
    if not copied["search_cases"]:
        raise FlowError("asset_search_eval dataset has no usable search_cases after --limit", exit_code=2)
    return copied


def _case_is_usable(case: dict[str, Any], *, selected_item_ids: set[str]) -> bool:
    expected = case.get("expected_item_ids")
    if not isinstance(expected, list) or not all(isinstance(item_id, str) for item_id in expected):
        return False
    if not set(expected).issubset(selected_item_ids):
        return False
    query_item_id = case.get("query_item_id")
    if isinstance(query_item_id, str) and query_item_id not in selected_item_ids:
        return False
    item_ids = case.get("item_ids")
    if isinstance(item_ids, list) and not set(item_ids).issubset(selected_item_ids):
        return False
    return True


def _validate_dataset(dataset: dict[str, Any]) -> None:
    item_ids: set[str] = set()
    category_ids: set[str] = set()
    for item in dataset["items"]:
        if not isinstance(item, dict):
            raise FlowError("asset_search_eval dataset.items[] must be objects", exit_code=2)
        item_id = item.get("item_id")
        category_id = item.get("category_id")
        asset = item.get("asset")
        if not isinstance(item_id, str) or not item_id:
            raise FlowError("asset_search_eval dataset.items[].item_id must be a non-empty string", exit_code=2)
        if item_id in item_ids:
            raise FlowError(f"asset_search_eval duplicated item_id: {item_id}", exit_code=2)
        item_ids.add(item_id)
        if not isinstance(category_id, str) or not category_id:
            raise FlowError(f"asset_search_eval item missing category_id: {item_id}", exit_code=2)
        category_ids.add(category_id)
        if not isinstance(asset, dict) or not isinstance(asset.get("public_url"), str) or not isinstance(asset.get("content_type"), str):
            raise FlowError(f"asset_search_eval item missing asset ref: {item_id}", exit_code=2)
    snapshot_category_ids = {group.get("category_id") for group in dataset["label_snapshot"] if isinstance(group, dict)}
    missing = sorted(category_ids - snapshot_category_ids)
    if missing:
        raise FlowError(f"asset_search_eval label_snapshot missing categories: {missing}", exit_code=2)
    for case in dataset["search_cases"]:
        if not isinstance(case, dict):
            raise FlowError("asset_search_eval search_cases[] must be objects", exit_code=2)
        expected = case.get("expected_item_ids")
        if not isinstance(expected, list) or not expected:
            raise FlowError(f"asset_search_eval search case missing expected_item_ids: {case}", exit_code=2)
        unknown_expected = sorted(set(expected) - item_ids)
        if unknown_expected:
            raise FlowError(f"asset_search_eval search case has unknown expected_item_ids: {unknown_expected}", exit_code=2)


def _submit_and_wait(
    *,
    context: service_runtime.RuntimeContext,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    create_envelope = http_runtime.request_json(
        context.summary["jobs_url"],
        method="POST",
        headers=headers,
        payload=payload,
    )
    job = http_runtime.data_object(create_envelope, "job")
    terminal_envelope = job_runtime.poll_job_envelope(
        jobs_url=context.summary["jobs_url"],
        job_id=str(job["job_id"]),
        headers=headers,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    terminal_job = http_runtime.data_object(terminal_envelope, "job")
    if terminal_job.get("job_status") != "succeeded":
        raise FlowError(f"{payload['job_type']} did not succeed: {terminal_job}", exit_code=1)
    return create_envelope, terminal_envelope


def _chunks(items: list[Any], *, batch_size: int) -> list[list[Any]]:
    if batch_size < 1:
        raise FlowError("--batch-size must be greater than or equal to 1", exit_code=2)
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def _data(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("code") != "0":
        raise FlowError(f"unexpected response envelope: {envelope}", exit_code=1)
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise FlowError(f"response missing data object: {envelope}", exit_code=1)
    return data


def _job_result(job: dict[str, Any], *, job_type: str) -> dict[str, Any]:
    result = job.get("job_result")
    if not isinstance(result, dict):
        raise FlowError(f"{job_type} result is missing", exit_code=1)
    if result.get("job_type") != job_type:
        raise FlowError(f"{job_type} result job_type mismatch: {result}", exit_code=1)
    return result


def _assert_batch_result(
    job: dict[str, Any],
    *,
    job_type: str,
    item_ids: list[str],
    count_key: str,
) -> dict[str, Any]:
    result = _job_result(job, job_type=job_type)
    batch_summary = result.get("batch_summary")
    if not isinstance(batch_summary, dict):
        raise FlowError(f"{job_type} result missing batch_summary: {result}", exit_code=1)
    if batch_summary.get("total") != len(item_ids) or batch_summary.get(count_key) != len(item_ids):
        raise FlowError(f"{job_type} result batch_summary mismatch: {result}", exit_code=1)
    items = result.get("items")
    if not isinstance(items, list) or len(items) != len(item_ids):
        raise FlowError(f"{job_type} result items mismatch: {result}", exit_code=1)
    returned_item_ids = [item.get("item_id") for item in items if isinstance(item, dict)]
    if returned_item_ids != item_ids:
        raise FlowError(f"{job_type} result item_ids mismatch: {returned_item_ids}", exit_code=1)
    return result


def _assert_tagging_result(job: dict[str, Any], *, request_payload: dict[str, Any]) -> dict[str, Any]:
    request_items = request_payload["job_params"]["items"]
    request_item_ids = [str(item["item_id"]) for item in request_items]
    result = _job_result(job, job_type=TAGGING_JOB_TYPE)
    items = result.get("items")
    if not isinstance(items, list) or len(items) != len(request_item_ids):
        raise FlowError(f"{TAGGING_JOB_TYPE} result items mismatch: {result}", exit_code=1)
    returned_item_ids = [item.get("item_id") for item in items if isinstance(item, dict)]
    if returned_item_ids != request_item_ids:
        raise FlowError(f"{TAGGING_JOB_TYPE} result item_ids mismatch: {returned_item_ids}", exit_code=1)
    batch_summary = result.get("batch_summary")
    if not isinstance(batch_summary, dict):
        raise FlowError(f"{TAGGING_JOB_TYPE} result missing batch_summary: {result}", exit_code=1)
    if batch_summary.get("total") != len(request_item_ids):
        raise FlowError(f"{TAGGING_JOB_TYPE} result batch_summary mismatch: {result}", exit_code=1)
    if batch_summary.get("failed", 0) != 0:
        raise FlowError(f"{TAGGING_JOB_TYPE} returned failed items: {result}", exit_code=1)
    return result


def _tagging_payload(
    dataset: dict[str, Any],
    *,
    client_request_id: str | None,
    run_id: str,
    batch_index: int,
    batch_items: list[dict[str, Any]],
) -> dict[str, Any]:
    category_ids = {str(item["category_id"]) for item in batch_items}
    return {
        "client_request_id": client_request_id or f"smoke-asset-search-eval-tagging-{run_id}-{batch_index}",
        "job_type": TAGGING_JOB_TYPE,
        "job_params": {
            "tagging_language": dataset.get("tagging_language", "zh"),
            "items": batch_items,
            "label_snapshot": [
                group
                for group in dataset["label_snapshot"]
                if isinstance(group, dict) and group.get("category_id") in category_ids
            ],
        },
        "metadata": {"source": f"scripts/smoke.sh {SCENARIO_NAME}", "run_id": run_id, "batch_index": batch_index},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def _delete_payload(item_ids: list[str], *, run_id: str, batch_index: int) -> dict[str, Any]:
    return {
        "client_request_id": f"smoke-asset-search-eval-delete-{run_id}-{batch_index}",
        "job_type": DELETE_JOB_TYPE,
        "job_params": {"item_ids": item_ids},
        "metadata": {"source": f"scripts/smoke.sh {SCENARIO_NAME}", "run_id": run_id, "batch_index": batch_index},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def _upsert_payload(items: list[dict[str, Any]], *, run_id: str, batch_index: int) -> dict[str, Any]:
    return {
        "client_request_id": f"smoke-asset-search-eval-vector-upsert-{run_id}-{batch_index}",
        "job_type": UPSERT_JOB_TYPE,
        "job_params": {"items": items},
        "metadata": {"source": f"scripts/smoke.sh {SCENARIO_NAME}", "run_id": run_id, "batch_index": batch_index},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def _item_by_id(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["item_id"]): item for item in dataset["items"] if isinstance(item, dict)}


def _selected_labels(result_item: dict[str, Any]) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for selection in result_item.get("label_group_selections") or []:
        if not isinstance(selection, dict):
            continue
        for label in selection.get("labels") or []:
            if isinstance(label, dict):
                labels.append(label)
    return labels


def _tagging_rows(dataset: dict[str, Any], tagging_result: dict[str, Any]) -> list[dict[str, Any]]:
    expected_by_item = dataset.get("expected_labels") if isinstance(dataset.get("expected_labels"), dict) else {}
    rows: list[dict[str, Any]] = []
    for item in tagging_result.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id"))
        labels = _selected_labels(item)
        selected_labels = [
            {
                "label_id": label.get("label_id"),
                "label_name": label.get("label_name"),
                "definition": label.get("definition"),
            }
            for label in labels
            if isinstance(label.get("label_id"), str) and isinstance(label.get("label_name"), str)
        ]
        selected_ids = [str(label.get("label_id")) for label in labels if isinstance(label.get("label_id"), str)]
        selected_names = [str(label.get("label_name")) for label in labels if isinstance(label.get("label_name"), str)]
        expected_ids = expected_by_item.get(item_id) if isinstance(expected_by_item, dict) else None
        matched = None
        missing: list[str] = []
        extra: list[str] = []
        if isinstance(expected_ids, list):
            expected_set = {str(value) for value in expected_ids}
            selected_set = set(selected_ids)
            matched = expected_set.issubset(selected_set)
            missing = sorted(expected_set - selected_set)
            extra = sorted(selected_set - expected_set)
        rows.append(
            {
                "item_id": item_id,
                "item_name": item.get("item_name"),
                "category_id": item.get("category_id"),
                "category_name": item.get("category_name"),
                "status": item.get("status"),
                "asset": item.get("asset"),
                "selected_labels": selected_labels,
                "selected_label_ids": selected_ids,
                "selected_label_names": selected_names,
                "expected_label_ids": expected_ids,
                "matched_expected_labels": matched,
                "missing_label_ids": missing,
                "extra_label_ids": extra,
                "asset_description": item.get("asset_description"),
                "validation_issues": item.get("validation_issues") or [],
                "error": item.get("error"),
            }
        )
    return rows


def _vector_labels(result_item: dict[str, Any], *, language: str) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for label in _selected_labels(result_item):
        label_id = label.get("label_id")
        label_name = label.get("label_name")
        if not isinstance(label_id, str) or not isinstance(label_name, str):
            continue
        vector_label = {
            "label_id": label_id,
            "language": language,
            "label_name": label_name,
        }
        definition = label.get("definition")
        if isinstance(definition, str) and definition:
            vector_label["definition"] = definition
        labels.append(vector_label)
    return labels


def _vector_upsert_payload(
    dataset: dict[str, Any],
    tagging_result: dict[str, Any],
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    source_items = _item_by_id(dataset)
    language = str(dataset.get("tagging_language", "zh"))
    upsert_items: list[dict[str, Any]] = []
    for result_item in tagging_result.get("items") or []:
        if not isinstance(result_item, dict):
            continue
        if result_item.get("status") not in {"succeeded", "partial_success"}:
            continue
        item_id = str(result_item["item_id"])
        if item_id not in source_items:
            raise FlowError(f"asset_search_eval tagging returned unknown item_id: {item_id}", exit_code=1)
        source_item = source_items[item_id]
        upsert_items.append(
            {
                "item_id": item_id,
                "item_name": source_item["item_name"],
                "asset": source_item["asset"],
                "labels": _vector_labels(result_item, language=language),
                "metadata": {
                    "source": f"scripts/smoke.sh {SCENARIO_NAME}",
                    "run_id": run_id,
                    "category_id": source_item["category_id"],
                    "category_name": source_item["category_name"],
                    "asset_description": result_item.get("asset_description"),
                },
            }
        )
    if not upsert_items:
        raise FlowError("asset_search_eval has no successfully tagged items to index", exit_code=1)
    return upsert_items


def _index_state(
    *,
    run_id: str,
    dataset_name: str,
    dataset_path: Path | None,
    vector_upsert_input_path: Path,
    vector_items: list[dict[str, Any]],
    upsert_result: dict[str, Any],
    exists_result: dict[str, Any],
    artifacts: SearchEvalArtifacts,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema": INDEX_STATE_SCHEMA,
        "scenario": SCENARIO_NAME,
        "run_id": run_id,
        "dataset": dataset_name,
        "dataset_path": None if dataset_path is None else str(dataset_path),
        "vector_upsert_input_json": str(vector_upsert_input_path),
        "item_ids": [str(item["item_id"]) for item in vector_items],
        "item_count": len(vector_items),
        "vector_upsert_job_ids": upsert_result["job_ids"],
        "exists_verified": True,
        "exists_response": exists_result,
        "created_at": now,
        "artifacts": _artifact_paths(artifacts),
    }


def _load_index_state(index_state_path: str | None, *, output_dir: Path | None = None) -> tuple[dict[str, Any], Path]:
    path = _resolve_index_state_path(index_state_path, output_dir=output_dir)
    state = _read_json(path)
    if state.get("schema") != INDEX_STATE_SCHEMA or state.get("scenario") != SCENARIO_NAME:
        raise FlowError(f"asset_search_eval index-state schema mismatch: {path}", exit_code=2)
    item_ids = state.get("item_ids")
    if not isinstance(item_ids, list) or not item_ids or not all(isinstance(item_id, str) for item_id in item_ids):
        raise FlowError(f"asset_search_eval index-state missing item_ids: {path}", exit_code=2)
    return state, path


def _resolve_index_state_path(index_state_path: str | None, *, output_dir: Path | None = None) -> Path:
    if index_state_path is None:
        if output_dir is None:
            raise FlowError("--index-state is required when --output-dir is not provided", exit_code=2)
        return output_dir / "index-state.json"
    path = Path(index_state_path).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _search_payload(
    case: dict[str, Any],
    *,
    source_items: dict[str, dict[str, Any]],
    candidate_item_ids: list[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {"search_mode": case["search_mode"]}
    if case.get("text") is not None:
        payload["text"] = case["text"]
    query_item_id = case.get("query_item_id")
    if isinstance(query_item_id, str):
        payload["asset"] = source_items[query_item_id]["asset"]
    if case.get("asset") is not None:
        payload["asset"] = case["asset"]
    if case.get("item_ids") is not None:
        payload["item_ids"] = case["item_ids"]
    if "candidate_item_ids" in case:
        raw_candidate_item_ids = case["candidate_item_ids"]
        if raw_candidate_item_ids is None:
            payload["candidate_item_ids"] = candidate_item_ids
        else:
            if not isinstance(raw_candidate_item_ids, list) or not all(
                isinstance(item_id, str) for item_id in raw_candidate_item_ids
            ):
                raise FlowError(
                    f"asset_search_eval search case candidate_item_ids must be a string list: {case['case_id']}",
                    exit_code=2,
                )
            candidate_set = set(candidate_item_ids)
            outside_candidate_pool = [item_id for item_id in raw_candidate_item_ids if item_id not in candidate_set]
            if outside_candidate_pool:
                raise FlowError(
                    "asset_search_eval search case candidate_item_ids are outside index-state item_ids: "
                    f"{case['case_id']} {outside_candidate_pool}",
                    exit_code=2,
                )
            payload["candidate_item_ids"] = raw_candidate_item_ids
    else:
        payload["candidate_item_ids"] = candidate_item_ids
    if case.get("top_k") is not None:
        payload["top_k"] = case["top_k"]
    return payload


def _run_search_cases(
    *,
    context: service_runtime.RuntimeContext,
    headers: dict[str, str],
    dataset: dict[str, Any],
    candidate_item_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    source_items = _item_by_id(dataset)
    resolved_candidate_item_ids = candidate_item_ids
    if resolved_candidate_item_ids is None:
        resolved_candidate_item_ids = [str(item["item_id"]) for item in dataset["items"]]
    rows: list[dict[str, Any]] = []
    for case in dataset["search_cases"]:
        payload = _search_payload(case, source_items=source_items, candidate_item_ids=resolved_candidate_item_ids)
        envelope = http_runtime.request_json(
            _api_url(context, "/vector-search"),
            method="POST",
            headers=headers,
            payload=payload,
        )
        data = _data(envelope)
        returned_item_ids = data.get("item_ids")
        if not isinstance(returned_item_ids, list):
            raise FlowError(f"asset_search_eval search response missing item_ids: {envelope}", exit_code=1)
        expected_item_ids = [str(item_id) for item_id in case["expected_item_ids"]]
        ranks = [
            returned_item_ids.index(expected_item_id) + 1
            for expected_item_id in expected_item_ids
            if expected_item_id in returned_item_ids
        ]
        best_rank = min(ranks) if ranks else None
        rows.append(
            {
                "case_id": case["case_id"],
                "search_mode": case["search_mode"],
                "query": {
                    "text": case.get("text"),
                    "query_item_id": case.get("query_item_id"),
                    "item_ids": case.get("item_ids"),
                },
                "request": payload,
                "expected_item_ids": expected_item_ids,
                "returned_item_ids": returned_item_ids,
                "hit_at_1": best_rank == 1,
                "hit_at_k": best_rank is not None,
                "best_rank": best_rank,
                "reciprocal_rank": 0 if best_rank is None else 1 / best_rank,
                "response": envelope,
            }
        )
    return rows


def _assert_assets_exist(
    *,
    context: service_runtime.RuntimeContext,
    headers: dict[str, str],
    item_ids: list[str],
) -> dict[str, Any]:
    envelope = http_runtime.request_json(
        _api_url(context, "/vector-assets:exists"),
        method="POST",
        headers=headers,
        payload={"item_ids": item_ids},
    )
    items = _data(envelope).get("items")
    if not isinstance(items, list) or len(items) != len(item_ids):
        raise FlowError(f"asset_search_eval vector-assets:exists response mismatch: {envelope}", exit_code=1)
    exists_by_item_id = {str(item.get("item_id")): item.get("exists") for item in items if isinstance(item, dict)}
    missing = [item_id for item_id in item_ids if exists_by_item_id.get(item_id) is not True]
    if missing:
        raise FlowError(f"asset_search_eval indexed items do not exist: {missing}", exit_code=1)
    return envelope


def _assert_index_covers_search_cases(*, candidate_item_ids: list[str], search_cases: list[dict[str, Any]]) -> None:
    candidate_set = set(candidate_item_ids)
    missing: dict[str, list[str]] = {}
    for case in search_cases:
        expected = [str(item_id) for item_id in case["expected_item_ids"]]
        missing_expected = [item_id for item_id in expected if item_id not in candidate_set]
        if missing_expected:
            missing[str(case["case_id"])] = missing_expected
    if missing:
        raise FlowError(f"asset_search_eval index-state does not cover search cases: {missing}", exit_code=2)


def _metrics(tagging_rows: list[dict[str, Any]], search_rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated_tagging = [row for row in tagging_rows if isinstance(row.get("expected_label_ids"), list)]
    required_matches = [row for row in evaluated_tagging if row["matched_expected_labels"] is True]
    expected_label_total = sum(len(row["expected_label_ids"]) for row in evaluated_tagging)
    selected_expected_hits = 0
    selected_extra_total = 0
    for row in evaluated_tagging:
        expected = set(row["expected_label_ids"])
        selected = set(row["selected_label_ids"])
        selected_expected_hits += len(expected & selected)
        selected_extra_total += len(selected - expected)
    hit_at_1 = [row for row in search_rows if row["hit_at_1"]]
    hit_at_k = [row for row in search_rows if row["hit_at_k"]]
    reciprocal_rank_total = sum(float(row["reciprocal_rank"]) for row in search_rows)
    return {
        "tagging": {
            "evaluated_items": len(evaluated_tagging),
            "required_match_count": len(required_matches),
            "required_match_rate": _ratio(len(required_matches), len(evaluated_tagging)),
            "label_recall": _ratio(selected_expected_hits, expected_label_total),
            "extra_label_count": selected_extra_total,
        },
        "search": {
            "case_count": len(search_rows),
            "hit_at_1_count": len(hit_at_1),
            "hit_at_1": _ratio(len(hit_at_1), len(search_rows)),
            "hit_at_k_count": len(hit_at_k),
            "hit_at_k": _ratio(len(hit_at_k), len(search_rows)),
            "mrr": _ratio(reciprocal_rank_total, len(search_rows)),
        },
    }


def _ratio(numerator: float, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _cleanup(
    *,
    context: service_runtime.RuntimeContext,
    headers: dict[str, str],
    item_ids: list[str],
    run_id: str,
    batch_size: int,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    terminal_jobs: list[dict[str, Any]] = []
    for batch_index, batch_item_ids in enumerate(_chunks(item_ids, batch_size=batch_size), start=1):
        _delete_create, delete_terminal = _submit_and_wait(
            context=context,
            headers=headers,
            payload=_delete_payload(batch_item_ids, run_id=run_id, batch_index=batch_index),
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        terminal_job = http_runtime.data_object(delete_terminal, "job")
        _assert_batch_result(terminal_job, job_type=DELETE_JOB_TYPE, item_ids=batch_item_ids, count_key="deleted")
        terminal_jobs.append(terminal_job)
    return _combined_job_result(DELETE_JOB_TYPE, terminal_jobs)


def _combined_job_result(job_type: str, terminal_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    job_ids: list[str] = []
    for job in terminal_jobs:
        job_ids.append(str(job.get("job_id")))
        result = _job_result(job, job_type=job_type)
        if not isinstance(result.get("items"), list):
            raise FlowError(f"{job_type} result is missing items: {job}", exit_code=1)
        items.extend(result["items"])
    if job_type == UPSERT_JOB_TYPE:
        summary_key = "succeeded"
    elif job_type == DELETE_JOB_TYPE:
        summary_key = "deleted"
    else:
        summary_key = "succeeded"
    return {
        "job_type": job_type,
        "job_ids": job_ids,
        "batch_summary": {"total": len(items), summary_key: len(items)},
        "items": items,
    }


def _combined_tagging_result(terminal_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    job_ids: list[str] = []
    for job in terminal_jobs:
        job_ids.append(str(job.get("job_id")))
        result = _job_result(job, job_type=TAGGING_JOB_TYPE)
        if not isinstance(result.get("items"), list):
            raise FlowError(f"{TAGGING_JOB_TYPE} result is missing items: {job}", exit_code=1)
        items.extend(result["items"])
    return {
        "job_type": TAGGING_JOB_TYPE,
        "job_ids": job_ids,
        "batch_summary": {
            "total": len(items),
            "succeeded": sum(1 for item in items if item.get("status") == "succeeded"),
            "partial_success": sum(1 for item in items if item.get("status") == "partial_success"),
            "failed": sum(1 for item in items if item.get("status") == "failed"),
        },
        "items": items,
    }


def _submit_tagging_batches(
    *,
    context: service_runtime.RuntimeContext,
    headers: dict[str, str],
    dataset: dict[str, Any],
    client_request_id: str | None,
    run_id: str,
    batch_size: int,
    request_path: Path,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    terminal_jobs: list[dict[str, Any]] = []
    for batch_index, batch_items in enumerate(_chunks(dataset["items"], batch_size=batch_size), start=1):
        batch_request_id = f"{client_request_id}-{batch_index}" if client_request_id is not None else None
        payload = _tagging_payload(
            dataset,
            client_request_id=batch_request_id,
            run_id=run_id,
            batch_index=batch_index,
            batch_items=batch_items,
        )
        payloads.append(payload)
        _write_json(request_path, {"job_type": TAGGING_JOB_TYPE, "job_payloads": payloads})
        _create, terminal = _submit_and_wait(
            context=context,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        terminal_job = http_runtime.data_object(terminal, "job")
        _assert_tagging_result(terminal_job, request_payload=payload)
        terminal_jobs.append(terminal_job)
    return payloads, _combined_tagging_result(terminal_jobs)


def _submit_vector_upsert_batches(
    *,
    context: service_runtime.RuntimeContext,
    headers: dict[str, str],
    vector_items: list[dict[str, Any]],
    run_id: str,
    batch_size: int,
    request_path: Path,
    indexed_item_ids: list[str],
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    terminal_jobs: list[dict[str, Any]] = []
    for batch_index, batch_items in enumerate(_chunks(vector_items, batch_size=batch_size), start=1):
        payload = _upsert_payload(batch_items, run_id=run_id, batch_index=batch_index)
        payloads.append(payload)
        _write_json(
            request_path,
            {"job_type": UPSERT_JOB_TYPE, "job_payloads": payloads, "items": vector_items},
        )
        _create, terminal = _submit_and_wait(
            context=context,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        terminal_job = http_runtime.data_object(terminal, "job")
        batch_item_ids = [str(item["item_id"]) for item in batch_items]
        indexed_item_ids.extend(batch_item_ids)
        _assert_batch_result(terminal_job, job_type=UPSERT_JOB_TYPE, item_ids=batch_item_ids, count_key="succeeded")
        terminal_jobs.append(terminal_job)
    return payloads, _combined_job_result(UPSERT_JOB_TYPE, terminal_jobs)


def _write_html_reports(
    artifacts: SearchEvalArtifacts,
    *,
    run_summary: dict[str, Any],
    dataset: dict[str, Any],
    tagging_rows: list[dict[str, Any]],
    search_rows: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    artifacts.html_dir.mkdir(parents=True, exist_ok=True)
    item_by_id = _item_by_id(dataset)
    _write_tagging_html(artifacts.tagging_report_html, tagging_rows=tagging_rows)
    _write_search_html(artifacts.search_report_html, search_rows=search_rows, item_by_id=item_by_id)
    cards = [
        ("打标报告", "tagging-report.html", f"items={len(tagging_rows)} required_match={metrics['tagging']['required_match_rate']}"),
        ("搜索报告", "search-report.html", f"cases={len(search_rows)} hit@1={metrics['search']['hit_at_1']} mrr={metrics['search']['mrr']}"),
    ]
    cards_html = "\n".join(
        f"""
        <section class="card">
          <h2>{html.escape(title)}</h2>
          <p>{html.escape(summary)}</p>
          <a href="{html.escape(href)}">打开报告</a>
        </section>
        """
        for title, href, summary in cards
    )
    artifacts.index_html.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Asset Search Eval</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f7f8; color: #111827; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .meta {{ color: #4b5563; font-size: 13px; line-height: 1.7; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-top: 24px; }}
    .card {{ border: 1px solid #d1d5db; border-radius: 8px; background: #fff; padding: 16px; }}
    a {{ color: #0f766e; font-weight: 600; }}
    pre {{ background: #111827; color: #f9fafb; padding: 16px; border-radius: 8px; overflow: auto; }}
  </style>
</head>
<body>
  <main>
    <h1>Asset Search Eval</h1>
    <div class="meta">run_id={html.escape(str(run_summary["run_id"]))}</div>
    <div class="meta">dataset={html.escape(str(run_summary["dataset"]))} items={len(dataset["items"])} generated_at={html.escape(str(run_summary["finished_at"]))}</div>
    <div class="grid">{cards_html}</div>
    <h2>Metrics</h2>
    <pre>{html.escape(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))}</pre>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )


def _write_tagging_html(path: Path, *, tagging_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_html = "\n".join(
        f"""
        <tr>
          <td><img src="{html.escape(row["asset"]["public_url"])}" alt="{html.escape(str(row["item_id"]))}"></td>
          <td>{html.escape(str(row["item_id"]))}<br><span>{html.escape(str(row["item_name"]))}</span></td>
          <td>{html.escape(str(row["category_name"]))}</td>
          <td>{html.escape(", ".join(row["selected_label_names"]))}</td>
          <td>{html.escape(", ".join(row.get("expected_label_ids") or []))}</td>
          <td>{html.escape(str(row["matched_expected_labels"]))}</td>
        </tr>
        """
        for row in tagging_rows
    )
    path.write_text(
        _html_page(
            "Asset Tagging Eval",
            f"""
            <h1>Asset Tagging Eval</h1>
            <table>
              <thead><tr><th>asset</th><th>item</th><th>category</th><th>selected labels</th><th>expected</th><th>matched</th></tr></thead>
              <tbody>{rows_html}</tbody>
            </table>
            """,
        ),
        encoding="utf-8",
    )


def _write_search_html(path: Path, *, search_rows: list[dict[str, Any]], item_by_id: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    for row in search_rows:
        result_cards: list[str] = []
        for rank, item_id in enumerate(row["returned_item_ids"], start=1):
            item = item_by_id.get(str(item_id))
            if item is None:
                continue
            result_cards.append(
                f"""
                <div class="result-card">
                  <img src="{html.escape(item["asset"]["public_url"])}" alt="{html.escape(str(item_id))}">
                  <div>#{rank} {html.escape(str(item_id))}</div>
                </div>
                """
            )
        sections.append(
            f"""
            <section class="case">
              <h2>{html.escape(str(row["case_id"]))} · {html.escape(str(row["search_mode"]))}</h2>
              <div class="meta">expected={html.escape(", ".join(row["expected_item_ids"]))} · best_rank={html.escape(str(row["best_rank"]))}</div>
              <pre>{html.escape(json.dumps(row["query"], ensure_ascii=False, indent=2, sort_keys=True))}</pre>
              <div class="results">{"".join(result_cards)}</div>
            </section>
            """
        )
    path.write_text(_html_page("Asset Search Eval", "<h1>Asset Search Eval</h1>" + "\n".join(sections)), encoding="utf-8")


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f7f8; color: #111827; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 32px 24px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d1d5db; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 10px; vertical-align: top; text-align: left; font-size: 13px; }}
    th {{ background: #f3f4f6; }}
    img {{ width: 120px; max-height: 120px; object-fit: contain; background: #fff; border: 1px solid #e5e7eb; }}
    span, .meta {{ color: #6b7280; font-size: 12px; }}
    pre {{ background: #111827; color: #f9fafb; padding: 12px; border-radius: 8px; overflow: auto; }}
    .case {{ margin-bottom: 28px; }}
    .results {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }}
    .result-card {{ border: 1px solid #d1d5db; border-radius: 8px; background: #fff; padding: 10px; font-size: 12px; }}
  </style>
</head>
<body>
  <main>{body}</main>
</body>
</html>
"""


def _artifact_paths(artifacts: SearchEvalArtifacts) -> dict[str, str]:
    return {
        "run_json": str(artifacts.run_json),
        "dataset_snapshot_json": str(artifacts.dataset_snapshot_json),
        "tagging_request_json": str(artifacts.tagging_request_json),
        "tagging_result_json": str(artifacts.tagging_result_json),
        "tagging_items_jsonl": str(artifacts.tagging_items_jsonl),
        "vector_upsert_input_json": str(artifacts.vector_upsert_input_json),
        "vector_upsert_result_json": str(artifacts.vector_upsert_result_json),
        "index_state_json": str(artifacts.index_state_json),
        "vector_exists_result_json": str(artifacts.vector_exists_result_json),
        "vector_delete_result_json": str(artifacts.vector_delete_result_json),
        "search_results_jsonl": str(artifacts.search_results_jsonl),
        "metrics_json": str(artifacts.metrics_json),
        "index_html": str(artifacts.index_html),
    }


def _write_failure_run(
    artifacts: SearchEvalArtifacts,
    *,
    exc: Exception,
    run_id: str,
    dataset_name: str,
    dataset_path: Path | None,
    output_dir: Path,
    phase: str,
    context: service_runtime.RuntimeContext | None,
    working_dataset: dict[str, Any] | None,
    batch_size: int,
    indexed_item_ids: list[str],
    cleanup: bool,
    cleanup_error: str | None,
    started_at: float,
    started_at_wall: datetime,
    metrics: dict[str, Any],
) -> None:
    failed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _write_json(
        artifacts.run_json,
        {
            "ok": False,
            "scenario": SCENARIO_NAME,
            "run_id": run_id,
            "dataset": dataset_name,
            "dataset_path": None if dataset_path is None else str(dataset_path),
            "output_dir": str(output_dir),
            "api_url": None if context is None else context.summary["api_url"],
            "phase": phase,
            "item_count": None if working_dataset is None else len(working_dataset["items"]),
            "search_case_count": None if working_dataset is None else len(working_dataset["search_cases"]),
            "batch_size": batch_size,
            "indexed_item_ids": indexed_item_ids,
            "cleanup": cleanup,
            "cleanup_error": cleanup_error,
            "started_at": started_at_wall.isoformat().replace("+00:00", "Z"),
            "finished_at": failed_at,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
            "artifacts": _artifact_paths(artifacts),
            "metrics": metrics,
        },
    )


def tag(
    *,
    confirm_run: bool,
    confirm_cost: bool,
    confirm_full_batch: bool,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    client_request_id: str | None,
    dataset: str,
    item_limit: int | None,
    batch_size: int,
    output_dir: str | None,
    json_output: bool,
) -> None:
    if not confirm_run:
        raise FlowError("--confirm-run is required because this command creates real tagging Jobs", exit_code=2)
    if not confirm_cost:
        raise FlowError("--confirm-cost is required because this command calls real model providers", exit_code=2)
    if batch_size < 1:
        raise FlowError("--batch-size must be greater than or equal to 1", exit_code=2)

    run_id = _run_id()
    started_at = time.monotonic()
    started_at_wall = datetime.now(UTC)
    dataset_name, dataset_path, _is_full_alias = _resolve_dataset_path(dataset)
    artifacts = _artifacts(_resolve_output_dir(output_dir, run_id=run_id))
    artifacts.output_dir.mkdir(parents=True, exist_ok=True)
    phase = "load_dataset"
    context: service_runtime.RuntimeContext | None = None
    working_dataset: dict[str, Any] | None = None
    metrics: dict[str, Any] = {}
    try:
        dataset_name, dataset_path, working_dataset = _load_working_dataset(
            dataset=dataset,
            item_limit=item_limit,
            confirm_full_batch=confirm_full_batch,
            require_full_confirmation=True,
        )
        _write_json(artifacts.dataset_snapshot_json, working_dataset)
        phase = "service_context"
        context = job_runtime.resolve_job_context(
            env_file=env_file,
            api_url=api_url,
            allow_remote_api=allow_remote_api,
            caller_id=caller_id,
            service_api_key=service_api_key,
        )
        if not context.summary["ready"]:
            raise FlowError(f"smoke context is not ready: {context.summary['problems']}", exit_code=2)
        headers = service_runtime.build_headers(context.app_env, caller_id=caller_id, service_api_key=service_api_key)
        phase = "tagging"
        tagging_payloads, tagging_result = _submit_tagging_batches(
            context=context,
            headers=headers,
            dataset=working_dataset,
            client_request_id=client_request_id,
            run_id=run_id,
            batch_size=batch_size,
            request_path=artifacts.tagging_request_json,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        _write_json(artifacts.tagging_request_json, {"job_type": TAGGING_JOB_TYPE, "job_payloads": tagging_payloads})
        _write_json(artifacts.tagging_result_json, tagging_result)
        tagging_rows = _tagging_rows(working_dataset, tagging_result)
        _write_jsonl(artifacts.tagging_items_jsonl, tagging_rows)
        vector_items = _vector_upsert_payload(working_dataset, tagging_result, run_id=run_id)
        _write_json(
            artifacts.vector_upsert_input_json,
            {"job_type": UPSERT_JOB_TYPE, "job_payloads": [], "items": vector_items},
        )
        metrics = _metrics(tagging_rows, [])
        _write_json(artifacts.metrics_json, metrics)
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        summary = {
            "ok": True,
            "scenario": SCENARIO_NAME,
            "stage": "tag",
            "run_id": run_id,
            "dataset": dataset_name,
            "dataset_path": str(dataset_path),
            "output_dir": str(artifacts.output_dir),
            "api_url": context.summary["api_url"],
            "item_count": len(working_dataset["items"]),
            "batch_size": batch_size,
            "tagging_job_ids": tagging_result["job_ids"],
            "vector_upsert_input_json": str(artifacts.vector_upsert_input_json),
            "started_at": started_at_wall.isoformat().replace("+00:00", "Z"),
            "finished_at": finished_at,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "artifacts": _artifact_paths(artifacts),
            "metrics": metrics,
        }
        _write_tagging_html(artifacts.tagging_report_html, tagging_rows=tagging_rows)
        _write_json(artifacts.run_json, summary)
    except Exception as exc:
        _write_failure_run(
            artifacts,
            exc=exc,
            run_id=run_id,
            dataset_name=dataset_name,
            dataset_path=dataset_path,
            output_dir=artifacts.output_dir,
            phase=phase,
            context=context,
            working_dataset=working_dataset,
            batch_size=batch_size,
            indexed_item_ids=[],
            cleanup=False,
            cleanup_error=None,
            started_at=started_at,
            started_at_wall=started_at_wall,
            metrics=metrics,
        )
        raise

    if json_output:
        formatters.print_json(summary)
        return
    formatters.section("Asset Search Eval Tag")
    print(f"dataset: {dataset_name} ({dataset_path})")
    print(f"output_dir: {artifacts.output_dir}")
    print(f"tagging_items: {artifacts.tagging_items_jsonl}")
    print(f"vector_upsert_input: {artifacts.vector_upsert_input_json}")
    print(f"tagging_report: {artifacts.tagging_report_html}")
    formatters.print_table(
        [
            {
                "ok": True,
                "items": len(working_dataset["items"]),
                "tag_required": metrics["tagging"]["required_match_rate"],
                "elapsed_s": summary["elapsed_seconds"],
            }
        ],
        columns=[
            ("ok", "ok"),
            ("items", "items"),
            ("tag_required", "tag_required"),
            ("elapsed_s", "elapsed_s"),
        ],
    )


def index(
    *,
    confirm_run: bool,
    confirm_cost: bool,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    vector_upsert_input: str | None,
    batch_size: int,
    output_dir: str | None,
    json_output: bool,
) -> None:
    if not confirm_run:
        raise FlowError("--confirm-run is required because this command writes vector rows", exit_code=2)
    if not confirm_cost:
        raise FlowError("--confirm-cost is required because this command calls real embedding providers", exit_code=2)
    if batch_size < 1:
        raise FlowError("--batch-size must be greater than or equal to 1", exit_code=2)

    run_id = _run_id()
    started_at = time.monotonic()
    started_at_wall = datetime.now(UTC)
    requested_output_dir = None if output_dir is None else _resolve_output_dir(output_dir, run_id=run_id)
    vector_upsert_input_path = _resolve_vector_upsert_input_path(vector_upsert_input, output_dir=requested_output_dir)
    artifacts = _artifacts(requested_output_dir or vector_upsert_input_path.parent)
    artifacts.output_dir.mkdir(parents=True, exist_ok=True)
    phase = "read_vector_upsert_input"
    context: service_runtime.RuntimeContext | None = None
    indexed_item_ids: list[str] = []
    metrics: dict[str, Any] = {}
    try:
        vector_items = _load_vector_upsert_input(vector_upsert_input_path)
        _write_json(
            artifacts.vector_upsert_input_json,
            {"job_type": UPSERT_JOB_TYPE, "job_payloads": [], "items": vector_items},
        )
        phase = "service_context"
        context = job_runtime.resolve_job_context(
            env_file=env_file,
            api_url=api_url,
            allow_remote_api=allow_remote_api,
            caller_id=caller_id,
            service_api_key=service_api_key,
        )
        if not context.summary["ready"]:
            raise FlowError(f"smoke context is not ready: {context.summary['problems']}", exit_code=2)
        headers = service_runtime.build_headers(context.app_env, caller_id=caller_id, service_api_key=service_api_key)
        phase = "vector_upsert"
        upsert_payloads, upsert_result = _submit_vector_upsert_batches(
            context=context,
            headers=headers,
            vector_items=vector_items,
            run_id=run_id,
            batch_size=batch_size,
            request_path=artifacts.vector_upsert_input_json,
            indexed_item_ids=indexed_item_ids,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        _write_json(
            artifacts.vector_upsert_input_json,
            {"job_type": UPSERT_JOB_TYPE, "job_payloads": upsert_payloads, "items": vector_items},
        )
        _write_json(artifacts.vector_upsert_result_json, upsert_result)
        phase = "vector_exists"
        exists_result = _assert_assets_exist(context=context, headers=headers, item_ids=indexed_item_ids)
        _write_json(artifacts.vector_exists_result_json, exists_result)
        state = _index_state(
            run_id=run_id,
            dataset_name="from_vector_upsert_input",
            dataset_path=None,
            vector_upsert_input_path=vector_upsert_input_path,
            vector_items=vector_items,
            upsert_result=upsert_result,
            exists_result=exists_result,
            artifacts=artifacts,
        )
        _write_json(artifacts.index_state_json, state)
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        summary = {
            "ok": True,
            "scenario": SCENARIO_NAME,
            "stage": "index",
            "run_id": run_id,
            "output_dir": str(artifacts.output_dir),
            "api_url": context.summary["api_url"],
            "vector_upsert_input_json": str(vector_upsert_input_path),
            "index_state_json": str(artifacts.index_state_json),
            "item_count": len(vector_items),
            "batch_size": batch_size,
            "vector_upsert_job_ids": upsert_result["job_ids"],
            "started_at": started_at_wall.isoformat().replace("+00:00", "Z"),
            "finished_at": finished_at,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "artifacts": _artifact_paths(artifacts),
        }
        _write_json(artifacts.run_json, summary)
    except Exception as exc:
        cleanup_error = None
        if indexed_item_ids and context is not None:
            try:
                headers = service_runtime.build_headers(context.app_env, caller_id=caller_id, service_api_key=service_api_key)
                cleanup_result = _cleanup(
                    context=context,
                    headers=headers,
                    item_ids=indexed_item_ids,
                    run_id=run_id,
                    batch_size=batch_size,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
                _write_json(artifacts.vector_delete_result_json, cleanup_result)
            except Exception as cleanup_exc:  # noqa: BLE001
                cleanup_error = f"{type(cleanup_exc).__name__}: {cleanup_exc}"
        _write_failure_run(
            artifacts,
            exc=exc,
            run_id=run_id,
            dataset_name="from_vector_upsert_input",
            dataset_path=vector_upsert_input_path,
            output_dir=artifacts.output_dir,
            phase=phase,
            context=context,
            working_dataset=None,
            batch_size=batch_size,
            indexed_item_ids=indexed_item_ids,
            cleanup=True,
            cleanup_error=cleanup_error,
            started_at=started_at,
            started_at_wall=started_at_wall,
            metrics=metrics,
        )
        if cleanup_error is not None:
            exit_code = exc.exit_code if isinstance(exc, FlowError) else 1
            raise FlowError(f"{exc}; cleanup_failed={cleanup_error}", exit_code=exit_code) from exc
        raise

    if json_output:
        formatters.print_json(summary)
        return
    formatters.section("Asset Search Eval Index")
    print(f"vector_upsert_input: {vector_upsert_input_path}")
    print(f"output_dir: {artifacts.output_dir}")
    print(f"index_state: {artifacts.index_state_json}")
    formatters.print_table(
        [{"ok": True, "items": summary["item_count"], "elapsed_s": summary["elapsed_seconds"]}],
        columns=[("ok", "ok"), ("items", "items"), ("elapsed_s", "elapsed_s")],
    )


def search(
    *,
    confirm_cost: bool,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    dataset: str,
    item_limit: int | None,
    index_state: str | None,
    output_dir: str | None,
    json_output: bool,
) -> None:
    if not confirm_cost:
        raise FlowError("--confirm-cost is required because search may call real embedding providers", exit_code=2)

    run_id = _run_id()
    started_at = time.monotonic()
    started_at_wall = datetime.now(UTC)
    artifacts = _artifacts(_resolve_output_dir(output_dir, run_id=run_id))
    artifacts.output_dir.mkdir(parents=True, exist_ok=True)
    dataset_name, dataset_path, _is_full_alias = _resolve_dataset_path(dataset)
    phase = "load_dataset"
    context: service_runtime.RuntimeContext | None = None
    working_dataset: dict[str, Any] | None = None
    metrics: dict[str, Any] = {}
    index_state_path: Path | None = None
    candidate_item_ids: list[str] = []
    try:
        dataset_name, dataset_path, working_dataset = _load_working_dataset(
            dataset=dataset,
            item_limit=item_limit,
            confirm_full_batch=False,
            require_full_confirmation=False,
        )
        _write_json(artifacts.dataset_snapshot_json, working_dataset)
        phase = "load_index_state"
        if index_state is not None:
            state, index_state_path = _load_index_state(index_state)
            candidate_item_ids = [str(item_id) for item_id in state["item_ids"]]
        else:
            candidate_item_ids = [str(item["item_id"]) for item in working_dataset["items"]]
        _assert_index_covers_search_cases(
            candidate_item_ids=candidate_item_ids,
            search_cases=working_dataset["search_cases"],
        )

        phase = "service_context"
        context = job_runtime.resolve_job_context(
            env_file=env_file,
            api_url=api_url,
            allow_remote_api=allow_remote_api,
            caller_id=caller_id,
            service_api_key=service_api_key,
        )
        if not context.summary["ready"]:
            raise FlowError(f"smoke context is not ready: {context.summary['problems']}", exit_code=2)
        headers = service_runtime.build_headers(context.app_env, caller_id=caller_id, service_api_key=service_api_key)
        phase = "vector_exists"
        exists_result = _assert_assets_exist(context=context, headers=headers, item_ids=candidate_item_ids)
        _write_json(artifacts.vector_exists_result_json, exists_result)
        phase = "search"
        search_rows = _run_search_cases(
            context=context,
            headers=headers,
            dataset=working_dataset,
            candidate_item_ids=candidate_item_ids,
        )
        _write_jsonl(artifacts.search_cases_jsonl, working_dataset["search_cases"])
        _write_jsonl(artifacts.search_results_jsonl, search_rows)
        metrics = _metrics([], search_rows)
        _write_json(artifacts.metrics_json, metrics)
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        summary = {
            "ok": True,
            "scenario": SCENARIO_NAME,
            "stage": "search",
            "run_id": run_id,
            "dataset": dataset_name,
            "dataset_path": str(dataset_path),
            "index_state_json": None if index_state_path is None else str(index_state_path),
            "output_dir": str(artifacts.output_dir),
            "api_url": context.summary["api_url"],
            "item_count": len(working_dataset["items"]),
            "candidate_item_count": len(candidate_item_ids),
            "search_case_count": len(working_dataset["search_cases"]),
            "started_at": started_at_wall.isoformat().replace("+00:00", "Z"),
            "finished_at": finished_at,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "artifacts": _artifact_paths(artifacts),
            "metrics": metrics,
        }
        _write_html_reports(
            artifacts,
            run_summary=summary,
            dataset=working_dataset,
            tagging_rows=[],
            search_rows=search_rows,
            metrics=metrics,
        )
        _write_json(artifacts.run_json, summary)
    except Exception as exc:
        _write_failure_run(
            artifacts,
            exc=exc,
            run_id=run_id,
            dataset_name=dataset_name,
            dataset_path=dataset_path,
            output_dir=artifacts.output_dir,
            phase=phase,
            context=context,
            working_dataset=working_dataset,
            batch_size=0,
            indexed_item_ids=candidate_item_ids,
            cleanup=False,
            cleanup_error=None,
            started_at=started_at,
            started_at_wall=started_at_wall,
            metrics=metrics,
        )
        raise

    if json_output:
        formatters.print_json(summary)
        return
    formatters.section("Asset Search Eval Search")
    print(f"dataset: {dataset_name} ({dataset_path})")
    if index_state_path is not None:
        print(f"index_state: {index_state_path}")
    print(f"output_dir: {artifacts.output_dir}")
    print(f"html_index: {artifacts.index_html}")
    formatters.print_table(
        [
            {
                "ok": True,
                "search_cases": len(working_dataset["search_cases"]),
                "candidates": len(candidate_item_ids),
                "hit_at_1": metrics["search"]["hit_at_1"],
                "hit_at_k": metrics["search"]["hit_at_k"],
                "mrr": metrics["search"]["mrr"],
            }
        ],
        columns=[
            ("ok", "ok"),
            ("search_cases", "search_cases"),
            ("candidates", "candidates"),
            ("hit_at_1", "hit@1"),
            ("hit_at_k", "hit@k"),
            ("mrr", "mrr"),
        ],
    )


def cleanup_index(
    *,
    confirm_run: bool,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    index_state: str,
    batch_size: int,
    output_dir: str | None,
    json_output: bool,
) -> None:
    if not confirm_run:
        raise FlowError("--confirm-run is required because this command deletes vector rows", exit_code=2)
    if batch_size < 1:
        raise FlowError("--batch-size must be greater than or equal to 1", exit_code=2)
    run_id = _run_id()
    started_at = time.monotonic()
    started_at_wall = datetime.now(UTC)
    requested_output_dir = None if output_dir is None else _resolve_output_dir(output_dir, run_id=run_id)
    index_state_path = _resolve_index_state_path(index_state)
    artifacts = _artifacts(requested_output_dir or index_state_path.parent)
    artifacts.output_dir.mkdir(parents=True, exist_ok=True)
    phase = "load_index_state"
    context: service_runtime.RuntimeContext | None = None
    item_ids: list[str] = []
    try:
        state, index_state_path = _load_index_state(index_state)
        item_ids = [str(item_id) for item_id in state["item_ids"]]
        phase = "service_context"
        context = job_runtime.resolve_job_context(
            env_file=env_file,
            api_url=api_url,
            allow_remote_api=allow_remote_api,
            caller_id=caller_id,
            service_api_key=service_api_key,
        )
        if not context.summary["ready"]:
            raise FlowError(f"smoke context is not ready: {context.summary['problems']}", exit_code=2)
        headers = service_runtime.build_headers(context.app_env, caller_id=caller_id, service_api_key=service_api_key)
        phase = "cleanup"
        delete_result = _cleanup(
            context=context,
            headers=headers,
            item_ids=item_ids,
            run_id=run_id,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        _write_json(artifacts.vector_delete_result_json, delete_result)
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        summary = {
            "ok": True,
            "scenario": SCENARIO_NAME,
            "stage": "cleanup",
            "run_id": run_id,
            "index_state_json": str(index_state_path),
            "output_dir": str(artifacts.output_dir),
            "api_url": context.summary["api_url"],
            "item_count": len(item_ids),
            "batch_size": batch_size,
            "vector_delete_job_ids": delete_result["job_ids"],
            "started_at": started_at_wall.isoformat().replace("+00:00", "Z"),
            "finished_at": finished_at,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "artifacts": _artifact_paths(artifacts),
        }
        _write_json(artifacts.run_json, summary)
    except Exception as exc:
        _write_failure_run(
            artifacts,
            exc=exc,
            run_id=run_id,
            dataset_name="from_index_state",
            dataset_path=index_state_path,
            output_dir=artifacts.output_dir,
            phase=phase,
            context=context,
            working_dataset=None,
            batch_size=batch_size,
            indexed_item_ids=item_ids,
            cleanup=True,
            cleanup_error=None,
            started_at=started_at,
            started_at_wall=started_at_wall,
            metrics={},
        )
        raise
    if json_output:
        formatters.print_json(summary)
        return
    formatters.section("Asset Search Eval Cleanup")
    print(f"index_state: {index_state_path}")
    print(f"output_dir: {artifacts.output_dir}")
    formatters.print_table(
        [{"ok": True, "deleted": len(item_ids), "elapsed_s": summary["elapsed_seconds"]}],
        columns=[("ok", "ok"), ("deleted", "deleted"), ("elapsed_s", "elapsed_s")],
    )


def run(
    *,
    confirm_run: bool,
    confirm_cost: bool,
    confirm_full_batch: bool,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    client_request_id: str | None,
    dataset: str,
    item_limit: int | None,
    batch_size: int,
    output_dir: str | None,
    cleanup: bool,
    json_output: bool,
) -> None:
    if not confirm_run:
        raise FlowError("--confirm-run is required because this eval creates real Jobs and writes vector rows", exit_code=2)
    if not confirm_cost:
        raise FlowError("--confirm-cost is required because this eval calls real model providers", exit_code=2)
    run_id = _run_id()
    if batch_size < 1:
        raise FlowError("--batch-size must be greater than or equal to 1", exit_code=2)
    dataset_name, dataset_path, is_full_alias = _resolve_dataset_path(dataset)
    started_at = time.monotonic()
    started_at_wall = datetime.now(UTC)
    artifacts = _artifacts(_resolve_output_dir(output_dir, run_id=run_id))
    artifacts.output_dir.mkdir(parents=True, exist_ok=True)

    phase = "load_dataset"
    context: service_runtime.RuntimeContext | None = None
    headers: dict[str, str] | None = None
    working_dataset: dict[str, Any] | None = None
    tagging_rows: list[dict[str, Any]] = []
    search_rows: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    upsert_result: dict[str, Any] | None = None
    delete_result: dict[str, Any] | None = None
    indexed_item_ids: list[str] = []
    try:
        loaded_dataset = _load_dataset(dataset_path)
        is_full_dataset = (
            is_full_alias
            or loaded_dataset.get("dataset_range") == "full"
            or (item_limit is None and len(loaded_dataset["items"]) > 100)
        )
        if is_full_dataset and item_limit is None and not confirm_full_batch:
            raise FlowError("--confirm-full-batch is required when running a full asset_search_eval dataset", exit_code=2)
        working_dataset = _slice_dataset(loaded_dataset, item_limit=item_limit)
        _validate_dataset(working_dataset)
        _write_json(artifacts.dataset_snapshot_json, working_dataset)

        phase = "service_context"
        context = job_runtime.resolve_job_context(
            env_file=env_file,
            api_url=api_url,
            allow_remote_api=allow_remote_api,
            caller_id=caller_id,
            service_api_key=service_api_key,
        )
        if not context.summary["ready"]:
            raise FlowError(f"smoke context is not ready: {context.summary['problems']}", exit_code=2)
        headers = service_runtime.build_headers(context.app_env, caller_id=caller_id, service_api_key=service_api_key)

        phase = "tagging"
        tagging_payloads, tagging_result = _submit_tagging_batches(
            context=context,
            headers=headers,
            dataset=working_dataset,
            client_request_id=client_request_id,
            run_id=run_id,
            batch_size=batch_size,
            request_path=artifacts.tagging_request_json,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        _write_json(artifacts.tagging_request_json, {"job_type": TAGGING_JOB_TYPE, "job_payloads": tagging_payloads})
        _write_json(artifacts.tagging_result_json, tagging_result)
        tagging_rows = _tagging_rows(working_dataset, tagging_result)
        _write_jsonl(artifacts.tagging_items_jsonl, tagging_rows)

        phase = "vector_prepare"
        vector_items = _vector_upsert_payload(working_dataset, tagging_result, run_id=run_id)
        _write_json(
            artifacts.vector_upsert_input_json,
            {"job_type": UPSERT_JOB_TYPE, "job_payloads": [], "items": vector_items},
        )
        phase = "vector_upsert"
        upsert_payloads, upsert_result = _submit_vector_upsert_batches(
            context=context,
            headers=headers,
            vector_items=vector_items,
            run_id=run_id,
            batch_size=batch_size,
            request_path=artifacts.vector_upsert_input_json,
            indexed_item_ids=indexed_item_ids,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        _write_json(
            artifacts.vector_upsert_input_json,
            {"job_type": UPSERT_JOB_TYPE, "job_payloads": upsert_payloads, "items": vector_items},
        )
        _write_json(artifacts.vector_upsert_result_json, upsert_result)

        phase = "vector_exists"
        exists_result = _assert_assets_exist(context=context, headers=headers, item_ids=indexed_item_ids)
        _write_json(artifacts.vector_exists_result_json, exists_result)
        state = _index_state(
            run_id=run_id,
            dataset_name=dataset_name,
            dataset_path=dataset_path,
            vector_upsert_input_path=artifacts.vector_upsert_input_json,
            vector_items=vector_items,
            upsert_result=upsert_result,
            exists_result=exists_result,
            artifacts=artifacts,
        )
        _write_json(artifacts.index_state_json, state)

        phase = "search"
        search_rows = _run_search_cases(context=context, headers=headers, dataset=working_dataset)
        _write_jsonl(artifacts.search_cases_jsonl, working_dataset["search_cases"])
        _write_jsonl(artifacts.search_results_jsonl, search_rows)
        metrics = _metrics(tagging_rows, search_rows)
        _write_json(artifacts.metrics_json, metrics)

        if cleanup:
            phase = "cleanup"
            delete_result = _cleanup(
                context=context,
                headers=headers,
                item_ids=indexed_item_ids,
                run_id=run_id,
                batch_size=batch_size,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            _write_json(artifacts.vector_delete_result_json, delete_result)
    except Exception as exc:
        cleanup_error: str | None = None
        if cleanup and delete_result is None and indexed_item_ids and context is not None and headers is not None:
            try:
                cleanup_result = _cleanup(
                    context=context,
                    headers=headers,
                    item_ids=indexed_item_ids,
                    run_id=run_id,
                    batch_size=batch_size,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
                _write_json(artifacts.vector_delete_result_json, cleanup_result)
            except Exception as cleanup_exc:  # noqa: BLE001
                cleanup_error = f"{type(cleanup_exc).__name__}: {cleanup_exc}"
        _write_failure_run(
            artifacts,
            exc=exc,
            run_id=run_id,
            dataset_name=dataset_name,
            dataset_path=dataset_path,
            output_dir=artifacts.output_dir,
            phase=phase,
            context=context,
            working_dataset=working_dataset,
            batch_size=batch_size,
            indexed_item_ids=indexed_item_ids,
            cleanup=cleanup,
            cleanup_error=cleanup_error,
            started_at=started_at,
            started_at_wall=started_at_wall,
            metrics=metrics,
        )
        if cleanup_error is not None:
            exit_code = exc.exit_code if isinstance(exc, FlowError) else 1
            raise FlowError(f"{exc}; cleanup_failed={cleanup_error}", exit_code=exit_code) from exc
        raise

    phase = "report"
    finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    run_summary = {
        "ok": True,
        "scenario": SCENARIO_NAME,
        "run_id": run_id,
        "dataset": dataset_name,
        "dataset_path": str(dataset_path),
        "output_dir": str(artifacts.output_dir),
        "api_url": context.summary["api_url"],
        "item_count": len(working_dataset["items"]),
        "search_case_count": len(working_dataset["search_cases"]),
        "batch_size": batch_size,
        "tagging_job_ids": tagging_result["job_ids"],
        "vector_upsert_job_ids": upsert_result["job_ids"],
        "vector_delete_job_ids": delete_result["job_ids"] if delete_result else [],
        "cleanup": cleanup,
        "started_at": started_at_wall.isoformat().replace("+00:00", "Z"),
        "finished_at": finished_at,
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "artifacts": _artifact_paths(artifacts),
        "metrics": metrics,
    }
    _write_html_reports(
        artifacts,
        run_summary=run_summary,
        dataset=working_dataset,
        tagging_rows=tagging_rows,
        search_rows=search_rows,
        metrics=metrics,
    )
    _write_json(artifacts.run_json, run_summary)

    if json_output:
        formatters.print_json(run_summary)
        return
    formatters.section("Asset Search Eval")
    print(f"dataset: {dataset_name} ({dataset_path})")
    print(f"output_dir: {artifacts.output_dir}")
    print(f"html_index: {artifacts.index_html}")
    formatters.print_table(
        [
            {
                "ok": True,
                "items": len(working_dataset["items"]),
                "search_cases": len(working_dataset["search_cases"]),
                "tag_required": metrics["tagging"]["required_match_rate"],
                "hit_at_1": metrics["search"]["hit_at_1"],
                "hit_at_k": metrics["search"]["hit_at_k"],
                "mrr": metrics["search"]["mrr"],
                "cleanup": cleanup,
            }
        ],
        columns=[
            ("ok", "ok"),
            ("items", "items"),
            ("search_cases", "search_cases"),
            ("tag_required", "tag_required"),
            ("hit_at_1", "hit@1"),
            ("hit_at_k", "hit@k"),
            ("mrr", "mrr"),
            ("cleanup", "cleanup"),
        ],
    )
    failed_searches = [row for row in search_rows if not row["hit_at_k"]]
    if failed_searches:
        formatters.print_table(
            failed_searches,
            columns=[
                ("case_id", "failed_case"),
                ("search_mode", "mode"),
                ("expected_item_ids", "expected"),
                ("returned_item_ids", "returned"),
            ],
        )
