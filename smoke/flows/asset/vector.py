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

SCENARIO_NAME = "asset-vector"
UPSERT_JOB_TYPE = "asset_vector_batch_upsert"
DELETE_JOB_TYPE = "asset_vector_batch_delete"
ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE_PATH = ROOT_DIR / "smoke/fixtures/asset_vector/batch.zh.json"


def _api_url(context: service_runtime.RuntimeContext, path: str) -> str:
    return f"{context.summary['api_url']}{context.summary['api_prefix']}{path}"


def _resolve_fixture_path(fixture_path: str | None) -> Path:
    if fixture_path is None:
        return DEFAULT_FIXTURE_PATH
    path = Path(fixture_path).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _load_fixture(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FlowError(f"asset_vector fixture not found: {path}", exit_code=2)
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FlowError(f"asset_vector fixture must be valid JSON: {path}: {exc}", exit_code=2) from exc
    if not isinstance(fixture, dict) or not isinstance(fixture.get("job_params"), dict):
        raise FlowError("asset_vector fixture must contain object field job_params", exit_code=2)
    return fixture


def _slice_job_params(job_params: dict[str, Any], *, item_limit: int | None) -> dict[str, Any]:
    copied = copy.deepcopy(job_params)
    items = copied.get("items")
    if not isinstance(items, list) or not items:
        raise FlowError("asset_vector fixture job_params.items must be a non-empty list", exit_code=2)
    if item_limit is not None:
        if item_limit < 1:
            raise FlowError("--limit must be greater than or equal to 1", exit_code=2)
        copied["items"] = items[:item_limit]
    return copied


def _expectations(fixture: dict[str, Any], *, job_params: dict[str, Any]) -> dict[str, str]:
    raw = fixture.get("expectations")
    items = job_params["items"]
    first_item_id = str(items[0]["item_id"])
    if raw is None:
        return {"primary_item_id": first_item_id, "text_query": str(items[0]["item_name"])}
    if not isinstance(raw, dict):
        raise FlowError("asset_vector fixture expectations must be an object", exit_code=2)
    primary_item_id = raw.get("primary_item_id")
    text_query = raw.get("text_query")
    if not isinstance(primary_item_id, str) or not primary_item_id:
        raise FlowError("asset_vector fixture expectations.primary_item_id must be a non-empty string", exit_code=2)
    if not isinstance(text_query, str) or not text_query:
        raise FlowError("asset_vector fixture expectations.text_query must be a non-empty string", exit_code=2)
    item_ids = {str(item["item_id"]) for item in items}
    if primary_item_id not in item_ids:
        primary_item_id = first_item_id
    return {"primary_item_id": primary_item_id, "text_query": text_query}


def build_payload(
    *,
    client_request_id: str | None,
    fixture_path: str | None,
    item_limit: int | None,
) -> tuple[dict[str, Any], Path, dict[str, str]]:
    fixture_file = _resolve_fixture_path(fixture_path)
    fixture = _load_fixture(fixture_file)
    job_params = _slice_job_params(fixture["job_params"], item_limit=item_limit)
    expectations = _expectations(fixture, job_params=job_params)
    return {
        "client_request_id": client_request_id or f"smoke-asset-vector-upsert-{uuid.uuid4()}",
        "job_type": UPSERT_JOB_TYPE,
        "job_params": job_params,
        "metadata": {"source": f"scripts/smoke.sh {SCENARIO_NAME}"},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }, fixture_file, expectations


def delete_payload(item_ids: list[str]) -> dict[str, Any]:
    return {
        "client_request_id": f"smoke-asset-vector-delete-{uuid.uuid4()}",
        "job_type": DELETE_JOB_TYPE,
        "job_params": {"item_ids": item_ids},
        "metadata": {"source": f"scripts/smoke.sh {SCENARIO_NAME}"},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


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


def _data(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("code") != "0":
        raise FlowError(f"unexpected response envelope: {envelope}", exit_code=1)
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise FlowError(f"response missing data object: {envelope}", exit_code=1)
    return data


def _assert_search_result(envelope: dict[str, Any], *, expected_item_id: str) -> None:
    data = _data(envelope)
    item_ids = data.get("item_ids")
    if not isinstance(item_ids, list) or expected_item_id not in item_ids:
        raise FlowError(f"search result does not contain {expected_item_id}: {envelope}", exit_code=1)


def _assert_job_result(job: dict[str, Any], *, job_type: str, item_ids: list[str], count_key: str) -> None:
    result = job.get("job_result")
    if not isinstance(result, dict):
        raise FlowError(f"{job_type} result is missing", exit_code=1)
    if result.get("job_type") != job_type:
        raise FlowError(f"{job_type} result job_type mismatch: {result}", exit_code=1)
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


def _assert_empty_search_result(envelope: dict[str, Any]) -> None:
    data = _data(envelope)
    item_ids = data.get("item_ids")
    if item_ids != []:
        raise FlowError(f"search result should be empty for empty candidate pool: {envelope}", exit_code=1)


def _fixture_items(request_payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = request_payload["job_params"]["items"]
    if not isinstance(items, list) or not items:
        raise FlowError("asset_vector request payload has no items", exit_code=2)
    return items


def _run_searches(
    *,
    context: service_runtime.RuntimeContext,
    headers: dict[str, str],
    request_payload: dict[str, Any],
    expectations: dict[str, str],
) -> dict[str, Any]:
    items = _fixture_items(request_payload)
    item_by_id = {str(item["item_id"]): item for item in items}
    expected_item_id = expectations["primary_item_id"]
    first = item_by_id[expected_item_id]
    top_k = min(len(items), 3)
    text_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={"search_mode": "text", "text": {"query": expectations["text_query"]}, "top_k": top_k},
    )
    _assert_search_result(text_search, expected_item_id=expected_item_id)
    image_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={
            "search_mode": "image",
            "asset": first["asset"],
            "top_k": top_k,
        },
    )
    _assert_search_result(image_search, expected_item_id=expected_item_id)
    item_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={"search_mode": "item_ids", "item_ids": [expected_item_id], "top_k": top_k},
    )
    _assert_search_result(item_search, expected_item_id=expected_item_id)
    hybrid_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={
            "search_mode": "hybrid",
            "text": {"query": "礼物 gift"},
            "asset": first["asset"],
            "item_ids": [expected_item_id],
            "top_k": top_k,
        },
    )
    _assert_search_result(hybrid_search, expected_item_id=expected_item_id)
    empty_candidate_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={
            "search_mode": "text",
            "text": {"query": "礼物"},
            "candidate_item_ids": [],
            "top_k": 1,
        },
    )
    _assert_empty_search_result(empty_candidate_search)
    return {
        "text": text_search,
        "image": image_search,
        "item_ids": item_search,
        "hybrid": hybrid_search,
        "empty_candidate": empty_candidate_search,
    }


def _assert_cleanup_done(
    *,
    context: service_runtime.RuntimeContext,
    headers: dict[str, str],
    item_ids: list[str],
) -> dict[str, Any]:
    exists = http_runtime.request_json(
        _api_url(context, "/vector-assets:exists"),
        method="POST",
        headers=headers,
        payload={"item_ids": item_ids},
    )
    exists_items = _data(exists).get("items")
    if not isinstance(exists_items, list) or [item.get("exists") for item in exists_items] != [False] * len(item_ids):
        raise FlowError(f"cleanup did not remove smoke assets: {exists}", exit_code=1)
    search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={
            "search_mode": "text",
            "text": {"query": "礼物"},
            "candidate_item_ids": [item_ids[0]],
            "top_k": 1,
        },
    )
    _assert_empty_search_result(search)
    return {"exists": exists, "candidate_search": search}


def _search_top(envelope: dict[str, Any]) -> str:
    item_ids = _data(envelope).get("item_ids")
    if not isinstance(item_ids, list) or not item_ids:
        return "-"
    return str(item_ids[0])


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
    cleanup: bool,
    json_output: bool,
) -> None:
    if not confirm_run:
        raise FlowError("--confirm-run is required because this smoke creates real Jobs and writes vector rows", exit_code=2)
    if not confirm_cost:
        raise FlowError("--confirm-cost is required because this smoke calls DashScope embeddings", exit_code=2)
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
    upsert, fixture_file, expectations = build_payload(
        client_request_id=client_request_id,
        fixture_path=fixture_path,
        item_limit=item_limit,
    )
    item_ids = [str(item["item_id"]) for item in _fixture_items(upsert)]
    upsert_create, upsert_terminal = _submit_and_wait(
        context=context,
        headers=headers,
        payload=upsert,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    upsert_job = http_runtime.data_object(upsert_terminal, "job")
    _assert_job_result(upsert_job, job_type=UPSERT_JOB_TYPE, item_ids=item_ids, count_key="succeeded")
    exists = http_runtime.request_json(
        _api_url(context, "/vector-assets:exists"),
        method="POST",
        headers=headers,
        payload={"item_ids": item_ids + ["smoke/missing"]},
    )
    exists_items = _data(exists).get("items")
    expected_exists = [True] * len(item_ids) + [False]
    if not isinstance(exists_items, list) or [item.get("exists") for item in exists_items] != expected_exists:
        raise FlowError(f"unexpected vector-assets:exists response: {exists}", exit_code=1)
    ids = http_runtime.request_json(
        _api_url(context, "/vector-assets/ids?limit=20"),
        method="GET",
        headers=headers,
    )
    listed_item_ids = _data(ids).get("item_ids")
    if not isinstance(listed_item_ids, list) or item_ids[0] not in listed_item_ids:
        raise FlowError(f"unexpected vector-assets/ids response: {ids}", exit_code=1)
    searches = _run_searches(context=context, headers=headers, request_payload=upsert, expectations=expectations)

    delete_create = None
    delete_terminal = None
    cleanup_verification = None
    if cleanup:
        delete_create, delete_terminal = _submit_and_wait(
            context=context,
            headers=headers,
            payload=delete_payload(item_ids),
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        delete_job = http_runtime.data_object(delete_terminal, "job")
        _assert_job_result(delete_job, job_type=DELETE_JOB_TYPE, item_ids=item_ids, count_key="deleted")
        cleanup_verification = _assert_cleanup_done(context=context, headers=headers, item_ids=item_ids)

    payload = {
        "ok": True,
        "scenario": SCENARIO_NAME,
        "summary": {
            "api_url": context.summary["api_url"],
            "fixture": str(fixture_file),
            "primary_item_id": expectations["primary_item_id"],
            "upsert_job_id": http_runtime.data_object(upsert_create, "job")["job_id"],
            "cleanup": cleanup,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
        },
        "request": upsert,
        "responses": {
            "upsert_create": upsert_create,
            "upsert_terminal": upsert_terminal,
            "exists": exists,
            "ids": ids,
            "searches": searches,
            "delete_create": delete_create,
            "delete_terminal": delete_terminal,
            "cleanup_verification": cleanup_verification,
        },
    }
    if json_output:
        formatters.print_json(payload)
        return
    formatters.section("Asset Vector Smoke")
    print(f"fixture: {fixture_file}")
    formatters.print_table(
        [
            {
                "ok": True,
                "upsert_job_id": payload["summary"]["upsert_job_id"],
                "items": len(item_ids),
                "text_top": _search_top(searches["text"]),
                "image_top": _search_top(searches["image"]),
                "item_ids_top": _search_top(searches["item_ids"]),
                "hybrid_top": _search_top(searches["hybrid"]),
                "cleanup": cleanup,
            }
        ],
        columns=[
            ("ok", "ok"),
            ("upsert_job_id", "upsert_job_id"),
            ("items", "items"),
            ("text_top", "text_top"),
            ("image_top", "image_top"),
            ("item_ids_top", "item_ids_top"),
            ("hybrid_top", "hybrid_top"),
            ("cleanup", "cleanup"),
        ],
    )
