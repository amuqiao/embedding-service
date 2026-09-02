from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any

from smoke.harness import formatters, http_runtime, service_runtime
from smoke.harness.errors import FlowError
from smoke.jobs import runtime as job_runtime

JOB_TYPE = "asset_image_tagging"
SCENARIO_NAME = "asset-image-tagging"
ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE_PATH = ROOT_DIR / "smoke/fixtures/asset_image_tagging/batch.zh.json"


def _resolve_fixture_path(fixture_path: str | None) -> Path:
    if fixture_path is None:
        return DEFAULT_FIXTURE_PATH
    path = Path(fixture_path).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _load_fixture(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FlowError(f"asset_image_tagging fixture not found: {path}", exit_code=2)
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FlowError(f"asset_image_tagging fixture must be valid JSON: {path}: {exc}", exit_code=2) from exc
    if not isinstance(fixture, dict) or not isinstance(fixture.get("job_params"), dict):
        raise FlowError("asset_image_tagging fixture must contain object field job_params", exit_code=2)
    return fixture


def _slice_job_params(job_params: dict[str, Any], *, item_limit: int | None) -> dict[str, Any]:
    copied = copy.deepcopy(job_params)
    items = copied.get("items")
    label_snapshot = copied.get("label_snapshot")
    if not isinstance(items, list) or not items:
        raise FlowError("asset_image_tagging fixture job_params.items must be a non-empty list", exit_code=2)
    if not isinstance(label_snapshot, list) or not label_snapshot:
        raise FlowError("asset_image_tagging fixture job_params.label_snapshot must be a non-empty list", exit_code=2)
    if item_limit is not None:
        if item_limit < 1:
            raise FlowError("--limit must be greater than or equal to 1", exit_code=2)
        copied["items"] = items[:item_limit]
    category_ids = {
        item.get("category_id")
        for item in copied["items"]
        if isinstance(item, dict) and isinstance(item.get("category_id"), str)
    }
    copied["label_snapshot"] = [
        group
        for group in label_snapshot
        if isinstance(group, dict) and group.get("category_id") in category_ids
    ]
    if not copied["label_snapshot"]:
        raise FlowError("asset_image_tagging fixture has no label_snapshot groups for selected items", exit_code=2)
    return copied


def build_payload(
    *,
    client_request_id: str | None,
    fixture_path: str | None,
    item_limit: int | None,
) -> tuple[dict[str, Any], Path]:
    fixture_file = _resolve_fixture_path(fixture_path)
    fixture = _load_fixture(fixture_file)
    job_params = _slice_job_params(fixture["job_params"], item_limit=item_limit)
    return {
        "client_request_id": client_request_id or f"smoke-asset-image-tagging-{uuid.uuid4()}",
        "job_type": JOB_TYPE,
        "job_params": job_params,
        "metadata": {"source": f"scripts/smoke.sh {SCENARIO_NAME}"},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }, fixture_file


def _request_item_by_id(job_params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["item_id"]): item for item in job_params["items"] if isinstance(item, dict)}


def _label_group_by_index(job_params: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {index: group for index, group in enumerate(job_params["label_snapshot"]) if isinstance(group, dict)}


def _label_ids_from_group(group: dict[str, Any]) -> set[str]:
    labels = group.get("labels")
    if not isinstance(labels, list):
        return set()
    return {label["label_id"] for label in labels if isinstance(label, dict) and isinstance(label.get("label_id"), str)}


def _assert_result(job: dict[str, Any], *, request_payload: dict[str, Any]) -> None:
    if job.get("job_status") != "succeeded":
        raise FlowError(f"asset_image_tagging job did not succeed: {job}", exit_code=1)
    result = job.get("job_result")
    if not isinstance(result, dict):
        raise FlowError("asset_image_tagging result is missing", exit_code=1)
    if result.get("job_type") != JOB_TYPE:
        raise FlowError(f"unexpected job_type in result: {result.get('job_type')}", exit_code=1)
    items = result.get("items")
    request_items = request_payload["job_params"]["items"]
    if not isinstance(items, list) or len(items) != len(request_items):
        raise FlowError(f"unexpected result items: {items}", exit_code=1)
    expected_item_ids = {item["item_id"] for item in request_items}
    actual_item_ids = {item.get("item_id") for item in items if isinstance(item, dict)}
    if actual_item_ids != expected_item_ids:
        raise FlowError(f"asset_image_tagging result item_id mismatch: {actual_item_ids}", exit_code=1)

    request_item_by_id = _request_item_by_id(request_payload["job_params"])
    label_group_by_index = _label_group_by_index(request_payload["job_params"])
    for item in items:
        if not isinstance(item, dict):
            raise FlowError(f"asset_image_tagging returned invalid item: {item}", exit_code=1)
        request_item = request_item_by_id.get(str(item.get("item_id")))
        if request_item is None:
            raise FlowError(f"asset_image_tagging returned unknown item: {item}", exit_code=1)
        if item.get("status") != "succeeded":
            raise FlowError(f"asset_image_tagging item did not succeed: {item}", exit_code=1)
        validation_issues = item.get("validation_issues")
        if validation_issues not in (None, []):
            raise FlowError(f"asset_image_tagging item has validation issues: {item}", exit_code=1)
        asset_description = item.get("asset_description")
        if not isinstance(asset_description, dict) or not isinstance(asset_description.get("text"), str):
            raise FlowError(f"asset_image_tagging item missing asset_description: {item}", exit_code=1)
        if not asset_description["text"].strip():
            raise FlowError(f"asset_image_tagging item missing asset_description: {item}", exit_code=1)
        selections = item.get("label_group_selections")
        if not isinstance(selections, list) or not selections:
            raise FlowError(f"asset_image_tagging item missing label selections: {item}", exit_code=1)
        selected_label_ids: list[Any] = []
        for selection in selections:
            if not isinstance(selection, dict):
                raise FlowError(f"asset_image_tagging returned invalid selection: {selection}", exit_code=1)
            label_snapshot_index = selection.get("label_snapshot_index")
            if isinstance(label_snapshot_index, bool) or not isinstance(label_snapshot_index, int):
                raise FlowError(f"asset_image_tagging returned invalid label_snapshot_index: {selection}", exit_code=1)
            request_group = label_group_by_index.get(label_snapshot_index)
            if request_group is None:
                raise FlowError(f"asset_image_tagging returned unknown label_snapshot_index: {selection}", exit_code=1)
            if request_group.get("category_id") != request_item.get("category_id"):
                raise FlowError(f"asset_image_tagging returned cross-category selection: {selection}", exit_code=1)
            if selection.get("category_id") != request_item.get("category_id"):
                raise FlowError(f"asset_image_tagging returned mismatched selection category: {selection}", exit_code=1)
            labels = selection.get("labels")
            if not isinstance(labels, list):
                raise FlowError(f"asset_image_tagging returned invalid labels: {selection}", exit_code=1)
            if request_group.get("selection_mode") == "single" and len(labels) > 1:
                raise FlowError(f"asset_image_tagging returned multiple labels for single group: {selection}", exit_code=1)
            candidate_label_ids = _label_ids_from_group(request_group)
            selected_label_ids.extend(label.get("label_id") for label in labels if isinstance(label, dict))
            unknown_group_label_ids = sorted(
                str(label.get("label_id"))
                for label in labels
                if isinstance(label, dict) and label.get("label_id") not in candidate_label_ids
            )
            if unknown_group_label_ids:
                raise FlowError(f"asset_image_tagging returned labels outside item group: {unknown_group_label_ids}", exit_code=1)
        if not selected_label_ids:
            raise FlowError(f"asset_image_tagging item did not select labels: {item}", exit_code=1)


def _result_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        selected_labels: list[str] = []
        tagging_labels: list[str] = []
        for selection in item.get("label_group_selections", []):
            if not isinstance(selection, dict):
                continue
            for label in selection.get("labels", []):
                if isinstance(label, dict) and isinstance(label.get("label_id"), str):
                    selected_labels.append(label["label_id"])
                    label_name = label.get("label_name")
                    if isinstance(label_name, str) and label_name:
                        tagging_labels.append(label_name)
        rows.append(
            {
                "item_id": item.get("item_id"),
                "input_relative_path": item.get("item_id"),
                "status": item.get("status"),
                "selected": ",".join(selected_labels[:4]),
                "tagging_labels": ",".join(tagging_labels[:4]),
                "issue_count": len(item.get("validation_issues") or []),
            }
        )
    return rows


def run(
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
    client_request_id: str | None,
    fixture_path: str | None,
    item_limit: int | None,
    json_output: bool,
) -> None:
    if not confirm_run:
        raise FlowError("--confirm-run is required because this smoke creates a real Job", exit_code=2)
    if not confirm_cost:
        raise FlowError("--confirm-cost is required because this smoke calls a real model provider", exit_code=2)
    started_at = time.monotonic()
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
    request_payload, resolved_fixture_path = build_payload(
        client_request_id=client_request_id,
        fixture_path=fixture_path,
        item_limit=item_limit,
    )
    create_envelope = http_runtime.request_json(
        context.summary["jobs_url"],
        method="POST",
        headers=headers,
        payload=request_payload,
    )
    job = http_runtime.data_object(create_envelope, "job")
    job_id = str(job["job_id"])
    terminal_envelope = job_runtime.poll_job_envelope(
        jobs_url=context.summary["jobs_url"],
        job_id=job_id,
        headers=headers,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    terminal_job = http_runtime.data_object(terminal_envelope, "job")
    _assert_result(terminal_job, request_payload=request_payload)
    payload = {
        "ok": True,
        "scenario": SCENARIO_NAME,
        "summary": {
            "api_url": context.summary["api_url"],
            "job_id": job_id,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
        },
        "request": request_payload,
        "result": terminal_job["job_result"],
        "responses": {
            "create": create_envelope,
            "terminal": terminal_envelope,
        },
    }
    if json_output:
        formatters.print_json(payload)
        return
    formatters.section("Asset Image Tagging Smoke")
    print(f"fixture: {resolved_fixture_path}")
    result = terminal_job["job_result"]
    summary = result["batch_summary"]
    formatters.print_table(
        [
            {
                "job_id": job_id,
                "status": terminal_job["job_status"],
                "items": len(request_payload["job_params"]["items"]),
                "succeeded": summary["succeeded"],
                "partial_success": summary["partial_success"],
                "failed": summary["failed"],
                "elapsed_seconds": payload["summary"]["elapsed_seconds"],
            }
        ],
        columns=[
            ("job_id", "job_id"),
            ("status", "status"),
            ("items", "items"),
            ("succeeded", "succeeded"),
            ("partial_success", "partial"),
            ("failed", "failed"),
            ("elapsed_seconds", "elapsed_s"),
        ],
    )
    formatters.print_table(
        _result_rows(result["items"]),
        columns=[
            ("item_id", "item_id"),
            ("input_relative_path", "input_relative_path"),
            ("status", "status"),
            ("selected", "selected_labels"),
            ("tagging_labels", "tagging_labels"),
            ("issue_count", "issue_count"),
        ],
    )
