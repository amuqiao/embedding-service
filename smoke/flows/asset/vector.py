from __future__ import annotations

import time
import uuid
from typing import Any

from smoke.harness import formatters, http_runtime, service_runtime
from smoke.harness.errors import FlowError
from smoke.jobs import runtime as job_runtime

SCENARIO_NAME = "asset-vector"
UPSERT_JOB_TYPE = "asset_vector_batch_upsert"
DELETE_JOB_TYPE = "asset_vector_batch_delete"


def _api_url(context: service_runtime.RuntimeContext, path: str) -> str:
    return f"{context.summary['api_url']}{context.summary['api_prefix']}{path}"


def upsert_payload(*, client_request_id: str | None) -> dict[str, Any]:
    return {
        "client_request_id": client_request_id or f"smoke-asset-vector-upsert-{uuid.uuid4()}",
        "job_type": UPSERT_JOB_TYPE,
        "job_params": {
            "items": [
                {
                    "item_id": "smoke_asset_champagne",
                    "item_name": "champagne bottle",
                    "asset": {
                        "public_url": "https://example.com/assets/smoke_asset_champagne.png",
                        "content_type": "image/png",
                    },
                    "labels": [
                        {
                            "label_id": "object_champagne",
                            "language": "en",
                            "label_name": "champagne",
                            "definition": "A champagne bottle for celebration scenes.",
                        }
                    ],
                },
                {
                    "item_id": "smoke_asset_apple",
                    "item_name": "red apple",
                    "asset": {
                        "public_url": "https://example.com/assets/smoke_asset_apple.png",
                        "content_type": "image/png",
                    },
                    "labels": [
                        {
                            "label_id": "object_apple",
                            "language": "en",
                            "label_name": "apple",
                            "definition": "A red apple fruit item.",
                        }
                    ],
                },
            ]
        },
        "metadata": {"source": f"scripts/smoke.sh {SCENARIO_NAME}"},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def delete_payload() -> dict[str, Any]:
    return {
        "client_request_id": f"smoke-asset-vector-delete-{uuid.uuid4()}",
        "job_type": DELETE_JOB_TYPE,
        "job_params": {"item_ids": ["smoke_asset_champagne", "smoke_asset_apple"]},
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


def _assert_empty_search_result(envelope: dict[str, Any]) -> None:
    data = _data(envelope)
    item_ids = data.get("item_ids")
    if item_ids != []:
        raise FlowError(f"search result should be empty for empty candidate pool: {envelope}", exit_code=1)


def _run_searches(
    *,
    context: service_runtime.RuntimeContext,
    headers: dict[str, str],
) -> dict[str, Any]:
    text_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={"search_mode": "text", "text": {"query": "champagne celebration bottle"}, "top_k": 2},
    )
    _assert_search_result(text_search, expected_item_id="smoke_asset_champagne")
    image_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={
            "search_mode": "image",
            "asset": {
                "public_url": "https://example.com/assets/smoke_asset_champagne.png",
                "content_type": "image/png",
            },
            "top_k": 2,
        },
    )
    _assert_search_result(image_search, expected_item_id="smoke_asset_champagne")
    item_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={"search_mode": "item_ids", "item_ids": ["smoke_asset_champagne"], "top_k": 2},
    )
    _assert_search_result(item_search, expected_item_id="smoke_asset_champagne")
    hybrid_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={
            "search_mode": "hybrid",
            "text": {"query": "champagne"},
            "item_ids": ["smoke_asset_champagne"],
            "top_k": 2,
        },
    )
    _assert_search_result(hybrid_search, expected_item_id="smoke_asset_champagne")
    empty_candidate_search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={
            "search_mode": "text",
            "text": {"query": "champagne"},
            "candidate_item_ids": [],
            "top_k": 2,
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
) -> dict[str, Any]:
    exists = http_runtime.request_json(
        _api_url(context, "/vector-assets:exists"),
        method="POST",
        headers=headers,
        payload={"item_ids": ["smoke_asset_champagne", "smoke_asset_apple"]},
    )
    exists_items = _data(exists).get("items")
    if not isinstance(exists_items, list) or [item.get("exists") for item in exists_items] != [False, False]:
        raise FlowError(f"cleanup did not remove smoke assets: {exists}", exit_code=1)

    search = http_runtime.request_json(
        _api_url(context, "/vector-search"),
        method="POST",
        headers=headers,
        payload={
            "search_mode": "text",
            "text": {"query": "champagne"},
            "candidate_item_ids": ["smoke_asset_champagne"],
            "top_k": 1,
        },
    )
    _assert_empty_search_result(search)
    return {"exists": exists, "candidate_search": search}


def run(
    *,
    confirm_run: bool,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    client_request_id: str | None,
    cleanup: bool,
    json_output: bool,
) -> None:
    if not confirm_run:
        raise FlowError("--confirm-run is required because this smoke creates real Jobs and writes vector rows", exit_code=2)
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
    upsert = upsert_payload(client_request_id=client_request_id)
    upsert_create, upsert_terminal = _submit_and_wait(
        context=context,
        headers=headers,
        payload=upsert,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    exists = http_runtime.request_json(
        _api_url(context, "/vector-assets:exists"),
        method="POST",
        headers=headers,
        payload={"item_ids": ["smoke_asset_champagne", "smoke_asset_apple", "smoke_asset_missing"]},
    )
    exists_items = _data(exists).get("items")
    if not isinstance(exists_items, list) or [item.get("exists") for item in exists_items] != [True, True, False]:
        raise FlowError(f"unexpected vector-assets:exists response: {exists}", exit_code=1)
    ids = http_runtime.request_json(
        _api_url(context, "/vector-assets/ids?limit=10"),
        method="GET",
        headers=headers,
    )
    item_ids = _data(ids).get("item_ids")
    if not isinstance(item_ids, list) or "smoke_asset_champagne" not in item_ids:
        raise FlowError(f"unexpected vector-assets/ids response: {ids}", exit_code=1)
    searches = _run_searches(context=context, headers=headers)

    delete_create = None
    delete_terminal = None
    cleanup_verification = None
    if cleanup:
        delete_create, delete_terminal = _submit_and_wait(
            context=context,
            headers=headers,
            payload=delete_payload(),
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        cleanup_verification = _assert_cleanup_done(context=context, headers=headers)

    payload = {
        "ok": True,
        "scenario": SCENARIO_NAME,
        "summary": {
            "api_url": context.summary["api_url"],
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
    formatters.print_table(
        [
            {
                "ok": True,
                "upsert_job_id": payload["summary"]["upsert_job_id"],
                "text_top": _data(searches["text"])["item_ids"][0],
                "image_top": _data(searches["image"])["item_ids"][0],
                "cleanup": cleanup,
            }
        ],
        columns=[
            ("ok", "ok"),
            ("upsert_job_id", "upsert_job_id"),
            ("text_top", "text_top"),
            ("image_top", "image_top"),
            ("cleanup", "cleanup"),
        ],
    )
