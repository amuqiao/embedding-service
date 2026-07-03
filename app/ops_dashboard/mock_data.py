from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ops_dashboard.schemas import DashboardFilters

MOCK_ROOT_JOB_ID = uuid.UUID("018f9a7f-0183-4e4f-938d-1baf7411b4fd")
MOCK_FAILED_CHILD_ID = uuid.UUID("018f9a7f-0183-4e4f-938d-1baf7411b4fe")


def _now() -> datetime:
    return datetime.now(UTC)


def _bucket_rows(filters: DashboardFilters) -> list[dict[str, Any]]:
    now = _now().replace(second=0, microsecond=0)
    step = timedelta(seconds=filters.bucket_seconds)
    rows: list[dict[str, Any]] = []
    for index in range(12):
        point = now - step * (11 - index)
        rows.append(
            {
                "bucket_at": point,
                "created": [2, 3, 1, 5, 4, 6, 4, 3, 2, 4, 5, 3][index],
                "started": [1, 2, 2, 4, 4, 5, 5, 3, 3, 3, 4, 3][index],
                "terminal": [0, 1, 2, 3, 3, 4, 5, 4, 3, 2, 4, 2][index],
                "failed": [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0][index],
            }
        )
    return rows


def overview_data(filters: DashboardFilters, *, max_active_jobs: int) -> dict[str, Any]:
    generated_at = _now()
    summary = {
        "jobs": {
            "total": 38,
            "queued": 4,
            "running": 3,
            "running_active": 2,
            "running_inactive": 1,
            "succeeded": 30,
            "failed": 1,
            "active_jobs": 6,
            "oldest_created_at": generated_at - filters.window_delta,
            "newest_created_at": generated_at,
        },
        "by_job_type": [
            {
                "job_type": "poster_title_image",
                "total": 24,
                "queued": 3,
                "running": 2,
                "active_jobs": 4,
                "succeeded": 19,
                "failed": 1,
            },
            {
                "job_type": "test.echo",
                "total": 14,
                "queued": 1,
                "running": 1,
                "active_jobs": 2,
                "succeeded": 11,
                "failed": 0,
            },
        ],
        "attempts": {"total": 52, "pending": 4, "running": 2, "succeeded": 45, "failed": 1},
        "dispatch": {
            "total": 52,
            "pending": 2,
            "leased": 1,
            "published": 48,
            "retrying": 1,
            "dead_letter": 0,
            "due": 1,
        },
        "callbacks": {
            "total": 18,
            "pending": 1,
            "leased": 0,
            "delivering": 0,
            "delivered": 16,
            "failed": 1,
            "dead_letter": 0,
            "due": 1,
        },
    }
    stuck_sample = [
        {
            "issue": "terminal_callback_not_settled",
            "job_id": str(MOCK_ROOT_JOB_ID),
            "job_status": "failed",
            "job_type": "poster_title_image",
            "related_id": "mock-callback-1",
            "related_status": "pending",
            "since_at": generated_at - timedelta(minutes=14),
            "next_attempt_at": generated_at - timedelta(minutes=2),
            "detail_code": "CALLBACK_DELIVERY_FAILED",
        }
    ]
    return {
        "mock_data": True,
        "generated_at": generated_at,
        "filters": filters.__dict__,
        "health": {
            "status": "warning",
            "reasons": ["mock_data", "failed_jobs", "callback_due"],
            "next_checks": [
                "./scripts/jobs.sh dashboard --since 1h",
                "./scripts/jobs.sh trace <job_id> --include-children",
            ],
        },
        "summary": summary,
        "capacity": {
            "current": {
                "active_jobs": 6,
                "queued": 4,
                "running_active": 2,
                "max_active_jobs": max_active_jobs,
                "active_ratio": (6 / max_active_jobs) if max_active_jobs > 0 else None,
                "headroom": max(max_active_jobs - 6, 0) if max_active_jobs > 0 else None,
            }
        },
        "ingress": _bucket_rows(filters),
        "latency": [
            {
                "group_key": "all",
                "total": 38,
                "started": 34,
                "terminal": 31,
                "succeeded": 30,
                "failed": 1,
                "queue_wait_p95_seconds": 8.4,
                "run_p95_seconds": 71.2,
                "lifecycle_p95_seconds": 84.6,
            }
        ],
        "stuck": {"count": len(stuck_sample), "sample": stuck_sample},
    }


def failures_data(filters: DashboardFilters) -> dict[str, Any]:
    generated_at = _now()
    stuck_sample = [
        {
            "issue": "terminal_callback_not_settled",
            "job_id": str(MOCK_ROOT_JOB_ID),
            "job_status": "failed",
            "job_type": "poster_title_image",
            "related_id": "mock-callback-1",
            "related_status": "pending",
            "since_at": generated_at - timedelta(minutes=14),
            "next_attempt_at": generated_at - timedelta(minutes=2),
            "detail_code": "CALLBACK_DELIVERY_FAILED",
        }
    ]
    return {
        "mock_data": True,
        "generated_at": generated_at,
        "filters": filters.__dict__,
        "failure_groups": [
            {
                "error_code": "AI_PROVIDER_FAILED",
                "error_kind": "worker_error",
                "failure_phase": "execute",
                "detail_type": "provider_5xx",
                "count": 1,
                "newest_updated_at": generated_at - timedelta(minutes=6),
            },
            {
                "error_code": "CALLBACK_DELIVERY_FAILED",
                "error_kind": "-",
                "failure_phase": "callback",
                "detail_type": "http_502",
                "count": 1,
                "newest_updated_at": generated_at - timedelta(minutes=2),
            },
        ],
        "failed_samples": [
            {
                "job_id": str(MOCK_ROOT_JOB_ID),
                "status": "failed",
                "job_type": "poster_title_image",
                "caller_id": filters.caller_id or "mock-caller",
                "client_request_id": "mock-client-request-001",
                "progress_percent": 92,
                "progress_stage": "failed",
                "created_at": generated_at - timedelta(minutes=26),
                "started_at": generated_at - timedelta(minutes=25),
                "finished_at": generated_at - timedelta(minutes=6),
                "updated_at": generated_at - timedelta(minutes=6),
            }
        ],
        "callbacks": [
            {
                "status": "pending",
                "count": 1,
                "due": 1,
                "oldest_created_at": generated_at - timedelta(minutes=6),
                "newest_updated_at": generated_at - timedelta(minutes=2),
                "next_attempt_at": generated_at - timedelta(minutes=2),
                "max_delivery_attempts_seen": 2,
                "last_http_status_seen": 502,
            },
            {
                "status": "delivered",
                "count": 16,
                "due": 0,
                "oldest_created_at": generated_at - timedelta(minutes=50),
                "newest_updated_at": generated_at - timedelta(minutes=4),
                "next_attempt_at": None,
                "max_delivery_attempts_seen": 1,
                "last_http_status_seen": 200,
            },
        ],
        "stuck": {"count": len(stuck_sample), "sample": stuck_sample},
    }


def job_trace_data(job_id: uuid.UUID, *, limit: int = 100) -> dict[str, Any]:
    del limit
    generated_at = _now()
    root_id = job_id
    return {
        "mock_data": True,
        "generated_at": generated_at,
        "job": {
            "job_id": str(root_id),
            "root_job_id": None,
            "workflow_node_key": None,
            "status": "failed",
            "job_type": "poster_title_image",
            "caller_id": "mock-caller",
            "client_request_id": "mock-client-request-001",
            "progress_percent": 92,
            "progress_stage": "failed",
            "progress_text": "mock provider failure",
            "error_code": "AI_PROVIDER_FAILED",
            "error_message": "mock provider 502",
            "callback_status": "pending",
            "created_at": generated_at - timedelta(minutes=26),
            "started_at": generated_at - timedelta(minutes=25),
            "finished_at": generated_at - timedelta(minutes=6),
            "updated_at": generated_at - timedelta(minutes=6),
            "metadata_summary": {"present": True, "type": "dict", "key_count": 1, "keys": ["mock"]},
            "job_params_summary": {"present": True, "type": "dict", "key_count": 1, "keys": ["items"]},
            "runtime_summary": {"present": True, "type": "dict", "key_count": 2, "keys": ["job_type", "runtime_fields"]},
            "result_summary": {"present": True, "type": "dict", "key_count": 1, "keys": ["items"]},
            "canonical_result_summary": {"present": False},
            "error_summary": {"present": True, "type": "dict", "key_count": 3, "keys": ["code", "details", "message"]},
        },
        "attempts": [
            {
                "id": "mock-attempt-1",
                "job_id": str(root_id),
                "purpose": "business_execution",
                "purpose_attempt_no": 1,
                "status": "failed",
                "dispatch_status": "published",
                "worker_id": "mock-worker:1",
                "failure_phase": "execute",
                "retry_eligible": True,
                "retry_decision": "scheduled_retry",
                "retry_decision_reason": "retryable_error",
                "policy_max_attempts": 3,
                "policy_retryable_error_codes": ["AI_PROVIDER_FAILED", "MODEL_CALL_TIMEOUT"],
                "error_code": "AI_PROVIDER_FAILED",
                "error_message": "mock provider 502",
                "created_at": generated_at - timedelta(minutes=25),
                "started_at": generated_at - timedelta(minutes=24),
                "finished_at": generated_at - timedelta(minutes=23),
            },
            {
                "id": "mock-attempt-2",
                "job_id": str(root_id),
                "purpose": "business_execution",
                "purpose_attempt_no": 2,
                "status": "failed",
                "dispatch_status": "published",
                "worker_id": "mock-worker:2",
                "failure_phase": "execute",
                "retry_eligible": False,
                "retry_decision": "do_not_retry",
                "retry_decision_reason": "max_attempts_reached",
                "policy_max_attempts": 3,
                "policy_retryable_error_codes": ["AI_PROVIDER_FAILED", "MODEL_CALL_TIMEOUT"],
                "error_code": "AI_PROVIDER_FAILED",
                "error_message": "mock provider 502",
                "created_at": generated_at - timedelta(minutes=20),
                "started_at": generated_at - timedelta(minutes=19),
                "finished_at": generated_at - timedelta(minutes=6),
            },
        ],
        "callbacks": [
            {
                "event_type": "job.failed",
                "status": "pending",
                "delivery_attempts": 2,
                "next_attempt_at": generated_at - timedelta(minutes=2),
                "last_http_status": 502,
                "last_error_message": "mock callback 502",
                "created_at": generated_at - timedelta(minutes=6),
                "updated_at": generated_at - timedelta(minutes=2),
            }
        ],
        "timeline": [
            {
                "created_at": generated_at - timedelta(minutes=26),
                "event_type": "attempt.created",
                "from_status": None,
                "to_status": "pending",
                "reason": "initial",
                "payload_summary": {"present": True, "type": "dict", "key_count": 1, "keys": ["purpose"]},
            },
            {
                "created_at": generated_at - timedelta(minutes=24),
                "event_type": "attempt.claimed",
                "from_status": "pending",
                "to_status": "running",
                "reason": None,
                "payload_summary": {"present": True, "type": "dict", "key_count": 1, "keys": ["worker_id"]},
            },
            {
                "created_at": generated_at - timedelta(minutes=6),
                "event_type": "workflow.root.failed",
                "from_status": "running",
                "to_status": "failed",
                "reason": "workflow_finalize",
                "payload_summary": {"present": True, "type": "dict", "key_count": 1, "keys": ["code"]},
            },
        ],
        "workflow_children": [
            {
                "workflow_node_key": "item.mock.sha256:provider502",
                "job_id": str(MOCK_FAILED_CHILD_ID),
                "status": "failed",
                "job_type": "poster_title_image_generate_item",
                "progress_percent": 30,
                "progress_stage": "failed",
                "attempt_status": "failed",
                "attempt_no": 2,
                "dispatch_status": "published",
                "publish_attempts": 1,
                "created_at": generated_at - timedelta(minutes=24),
                "started_at": generated_at - timedelta(minutes=23),
                "finished_at": generated_at - timedelta(minutes=6),
                "updated_at": generated_at - timedelta(minutes=6),
            },
            {
                "workflow_node_key": "item.mock.sha256:success",
                "job_id": "018f9a7f-0183-4e4f-938d-1baf7411b4ff",
                "status": "succeeded",
                "job_type": "poster_title_image_generate_item",
                "progress_percent": 100,
                "progress_stage": "succeeded",
                "attempt_status": "succeeded",
                "attempt_no": 1,
                "dispatch_status": "published",
                "publish_attempts": 1,
                "created_at": generated_at - timedelta(minutes=24),
                "started_at": generated_at - timedelta(minutes=23),
                "finished_at": generated_at - timedelta(minutes=8),
                "updated_at": generated_at - timedelta(minutes=8),
            },
        ],
        "ai_calls": [
            {
                "status": "failed",
                "operation": "poster_title_image.generate_title",
                "step_name": "image_generation",
                "request_id": "mock-request-id",
                "job_type": "poster_title_image_generate_item",
                "model_id": "gpt-image-2",
                "provider": "openai",
                "provider_model": "gpt-image-2",
                "failure_phase": "provider",
                "error_code": "AI_PROVIDER_FAILED",
                "error_message": "mock provider 502",
                "input_size_bytes": 208373,
                "output_size_bytes": None,
                "billable_status": "unknown",
                "cost_calculation_status": "not_applicable",
                "duration_ms": 41_949,
                "started_at": generated_at - timedelta(minutes=20),
                "completed_at": generated_at - timedelta(minutes=19),
                "created_at": generated_at - timedelta(minutes=20),
            }
        ],
    }
