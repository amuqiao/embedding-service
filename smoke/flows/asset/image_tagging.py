from __future__ import annotations

import time
import uuid
from typing import Any

from smoke.harness import formatters, http_runtime, service_runtime
from smoke.harness.errors import FlowError
from smoke.jobs import runtime as job_runtime

JOB_TYPE = "asset_image_tagging"
SCENARIO_NAME = "asset-image-tagging"


def build_payload(*, client_request_id: str | None) -> dict[str, Any]:
    return {
        "client_request_id": client_request_id or f"smoke-asset-image-tagging-{uuid.uuid4()}",
        "job_type": JOB_TYPE,
        "job_params": {
            "tagging_language": "zh",
            "items": [
                {
                    "item_id": "smoke_asset_hair_001",
                    "item_name": "棕色中长卷发",
                    "category_id": "hair",
                    "category_name": "发型",
                    "asset": {
                        "public_url": "https://example.com/assets/hair_001.png",
                        "content_type": "image/png",
                    },
                }
            ],
            "label_snapshot": [
                {
                    "category_id": "hair",
                    "category_name": "发型",
                    "selection_mode": "single",
                    "labels": [
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
                }
            ],
        },
        "metadata": {"source": f"scripts/smoke.sh {SCENARIO_NAME}"},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def _assert_result(job: dict[str, Any]) -> None:
    if job.get("job_status") != "succeeded":
        raise FlowError(f"asset_image_tagging job did not succeed: {job}", exit_code=1)
    result = job.get("job_result")
    if not isinstance(result, dict):
        raise FlowError("asset_image_tagging result is missing", exit_code=1)
    if result.get("job_type") != JOB_TYPE:
        raise FlowError(f"unexpected job_type in result: {result.get('job_type')}", exit_code=1)
    items = result.get("items")
    if not isinstance(items, list) or len(items) != 1:
        raise FlowError(f"unexpected result items: {items}", exit_code=1)
    item = items[0]
    if item.get("status") != "succeeded":
        raise FlowError(f"asset_image_tagging item did not succeed: {item}", exit_code=1)
    selections = item.get("label_group_selections")
    if not isinstance(selections, list) or not selections:
        raise FlowError(f"asset_image_tagging item missing label selections: {item}", exit_code=1)


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
    json_output: bool,
) -> None:
    if not confirm_run:
        raise FlowError("--confirm-run is required because this smoke creates a real Job", exit_code=2)
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
    request_payload = build_payload(client_request_id=client_request_id)
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
    _assert_result(terminal_job)
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
    formatters.print_table(
        [
            {
                "ok": payload["ok"],
                "job_id": job_id,
                "status": terminal_job["job_status"],
                "selected": terminal_job["job_result"]["items"][0]["label_group_selections"][0]["labels"][0]["label_id"],
            }
        ],
        columns=[("ok", "ok"), ("job_id", "job_id"), ("status", "status"), ("selected", "selected_label")],
    )
