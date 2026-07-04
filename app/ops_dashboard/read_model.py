from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ops_dashboard.health_rules import health_verdict
from app.ops_dashboard.schemas import DashboardFilters

ROOT_SCOPE_SQL = """
AND {alias}.root_job_id IS NULL
AND {alias}.workflow_node_key IS NULL
AND {alias}.client_request_id IS NOT NULL
"""
CHILD_SCOPE_SQL = """
AND {alias}.root_job_id IS NOT NULL
AND {alias}.workflow_node_key IS NOT NULL
AND {alias}.client_request_id IS NULL
"""
OPTIONAL_FILTER_BIND_TYPES = {
    "job_type": String(),
    "caller_id": String(),
    "since_at": DateTime(timezone=True),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _lineage_scope_clause(alias: str, record_scope: str) -> str:
    if record_scope == "all":
        return ""
    if record_scope == "root":
        return ROOT_SCOPE_SQL.format(alias=alias)
    if record_scope == "child":
        return CHILD_SCOPE_SQL.format(alias=alias)
    raise ValueError(f"invalid record_scope: {record_scope}")


def _family_scope_clause(alias: str) -> str:
    return f"""
AND EXISTS (
  SELECT 1
  FROM job_aggregates root
  WHERE root.deleted_at IS NULL
    AND root.root_job_id IS NULL
    AND root.workflow_node_key IS NULL
    AND root.client_request_id IS NOT NULL
    AND ({alias}.id = root.id OR {alias}.root_job_id = root.id)
    AND (:job_type IS NULL OR root.job_type = :job_type)
    AND (:caller_id IS NULL OR root.caller_id = :caller_id)
    AND (:since_at IS NULL OR root.created_at >= :since_at)
)
"""


def _scope_clause(alias: str, record_scope: str) -> str:
    if record_scope == "family":
        return _family_scope_clause(alias)
    return f"""
{_lineage_scope_clause(alias, record_scope)}
AND (:job_type IS NULL OR {alias}.job_type = :job_type)
AND (:caller_id IS NULL OR {alias}.caller_id = :caller_id)
AND (:since_at IS NULL OR {alias}.created_at >= :since_at)
"""


def _scope_clause_without_since(alias: str, record_scope: str) -> str:
    if record_scope == "family":
        raise ValueError("family scope without since is not supported")
    return f"""
{_lineage_scope_clause(alias, record_scope)}
AND (:job_type IS NULL OR {alias}.job_type = :job_type)
AND (:caller_id IS NULL OR {alias}.caller_id = :caller_id)
"""


def _base_params(filters: DashboardFilters) -> dict[str, Any]:
    return {
        "job_type": filters.job_type,
        "caller_id": filters.caller_id,
        "since_at": _now() - filters.window_delta,
        "limit": filters.sample_limit,
    }


def _typed_text(sql: str):
    statement = text(sql)
    existing_params = statement.compile().params
    bindparams = [
        bindparam(key, type_=param_type)
        for key, param_type in OPTIONAL_FILTER_BIND_TYPES.items()
        if key in existing_params
    ]
    return statement.bindparams(*bindparams) if bindparams else statement


async def _one(db: AsyncSession, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    row = (await db.execute(_typed_text(sql), params)).mappings().first()
    return dict(row) if row else {}


async def _all(db: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (await db.execute(_typed_text(sql), params)).mappings().all()
    return [dict(row) for row in rows]


async def summary(db: AsyncSession, filters: DashboardFilters) -> dict[str, Any]:
    root_clause = _scope_clause("j", "root")
    family_clause = _scope_clause("j", "family")
    root_params = _base_params(filters)
    family_params = _base_params(filters)

    jobs = await _one(
        db,
        f"""
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE j.status = 'queued') AS queued,
          count(*) FILTER (WHERE j.status = 'running') AS running,
          count(*) FILTER (WHERE j.status = 'running' AND j.active_attempt_id IS NOT NULL) AS running_active,
          count(*) FILTER (WHERE j.status = 'running' AND j.active_attempt_id IS NULL) AS running_inactive,
          count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded,
          count(*) FILTER (WHERE j.status = 'failed') AS failed,
          count(*) FILTER (
            WHERE j.status = 'queued'
               OR (j.status = 'running' AND j.active_attempt_id IS NOT NULL)
          ) AS active_jobs,
          min(j.created_at) AS oldest_created_at,
          max(j.created_at) AS newest_created_at
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
        {root_clause}
        """,
        root_params,
    )
    by_job_type = await _all(
        db,
        f"""
        SELECT
          j.job_type,
          count(*) AS total,
          count(*) FILTER (WHERE j.status = 'queued') AS queued,
          count(*) FILTER (WHERE j.status = 'running') AS running,
          count(*) FILTER (
            WHERE j.status = 'queued'
               OR (j.status = 'running' AND j.active_attempt_id IS NOT NULL)
          ) AS active_jobs,
          count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded,
          count(*) FILTER (WHERE j.status = 'failed') AS failed
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
        {root_clause}
        GROUP BY j.job_type
        ORDER BY active_jobs DESC, total DESC, j.job_type ASC
        LIMIT :limit
        """,
        root_params,
    )
    attempts = await _one(
        db,
        f"""
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE a.status = 'pending') AS pending,
          count(*) FILTER (WHERE a.status = 'running') AS running,
          count(*) FILTER (WHERE a.status = 'succeeded') AS succeeded,
          count(*) FILTER (WHERE a.status = 'failed') AS failed
        FROM job_execution_attempts a
        JOIN job_aggregates j ON j.id = a.job_id
        WHERE j.deleted_at IS NULL
        {family_clause}
        """,
        family_params,
    )
    dispatch = await _one(
        db,
        f"""
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE d.status = 'pending') AS pending,
          count(*) FILTER (WHERE d.status = 'leased') AS leased,
          count(*) FILTER (WHERE d.status = 'published') AS published,
          count(*) FILTER (WHERE d.status = 'retrying') AS retrying,
          count(*) FILTER (WHERE d.status = 'dead_letter') AS dead_letter,
          count(*) FILTER (
            WHERE d.status IN ('pending', 'retrying')
              AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= now())
          ) AS due
        FROM dispatch_outbox d
        JOIN job_execution_attempts a ON a.id = d.attempt_id
        JOIN job_aggregates j ON j.id = a.job_id
        WHERE j.deleted_at IS NULL
          AND d.task_name = 'jobs.run_attempt'
        {family_clause}
        """,
        family_params,
    )
    callbacks = await _one(
        db,
        f"""
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE c.status = 'pending') AS pending,
          count(*) FILTER (WHERE c.status = 'leased') AS leased,
          count(*) FILTER (WHERE c.status = 'delivering') AS delivering,
          count(*) FILTER (WHERE c.status = 'delivered') AS delivered,
          count(*) FILTER (WHERE c.status = 'failed') AS failed,
          count(*) FILTER (WHERE c.status = 'dead_letter') AS dead_letter,
          count(*) FILTER (
            WHERE c.status IN ('pending', 'failed', 'retrying')
              AND (c.next_attempt_at IS NULL OR c.next_attempt_at <= now())
          ) AS due
        FROM callback_outbox c
        JOIN job_aggregates j ON j.id = c.job_id
        WHERE j.deleted_at IS NULL
        {root_clause}
        """,
        root_params,
    )
    return {
        "jobs": jobs,
        "by_job_type": by_job_type,
        "attempts": attempts,
        "dispatch": dispatch,
        "callbacks": callbacks,
    }


async def global_gate(db: AsyncSession, max_active_jobs: int) -> dict[str, Any]:
    current = await _one(
        db,
        """
        SELECT
          count(*) FILTER (
            WHERE j.status = 'queued'
               OR (j.status = 'running' AND j.active_attempt_id IS NOT NULL)
          ) AS active_jobs,
          count(*) FILTER (WHERE j.status = 'queued') AS queued,
          count(*) FILTER (WHERE j.status = 'running' AND j.active_attempt_id IS NOT NULL) AS running_active
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
        """,
        {},
    )
    active = int(current.get("active_jobs") or 0)
    return current | {
        "max_active_jobs": max_active_jobs,
        "active_ratio": (active / max_active_jobs) if max_active_jobs > 0 else None,
        "headroom": max(max_active_jobs - active, 0) if max_active_jobs > 0 else None,
    }


async def ingress(db: AsyncSession, filters: DashboardFilters) -> list[dict[str, Any]]:
    clause = _scope_clause_without_since("j", "root")
    params = _base_params(filters) | {"bucket_seconds": filters.bucket_seconds}
    return await _all(
        db,
        f"""
        WITH events AS (
          SELECT j.created_at AS event_at, 'created' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.created_at >= :since_at
            {clause}
          UNION ALL
          SELECT j.started_at AS event_at, 'started' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.started_at IS NOT NULL
            AND j.started_at >= :since_at
            {clause}
          UNION ALL
          SELECT j.finished_at AS event_at, 'terminal' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.finished_at IS NOT NULL
            AND j.finished_at >= :since_at
            {clause}
          UNION ALL
          SELECT j.finished_at AS event_at, 'failed' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.status = 'failed'
            AND j.finished_at IS NOT NULL
            AND j.finished_at >= :since_at
            {clause}
        )
        SELECT
          to_timestamp(
            floor(EXTRACT(EPOCH FROM event_at) / :bucket_seconds) * :bucket_seconds
          ) AS bucket_at,
          count(*) FILTER (WHERE metric = 'created') AS created,
          count(*) FILTER (WHERE metric = 'started') AS started,
          count(*) FILTER (WHERE metric = 'terminal') AS terminal,
          count(*) FILTER (WHERE metric = 'failed') AS failed
        FROM events
        GROUP BY bucket_at
        ORDER BY bucket_at ASC
        """,
        params,
    )


async def latency(db: AsyncSession, filters: DashboardFilters) -> list[dict[str, Any]]:
    clause = _scope_clause("j", "root")
    return await _all(
        db,
        f"""
        SELECT
          'all' AS group_key,
          count(*) AS total,
          count(*) FILTER (WHERE j.started_at IS NOT NULL) AS started,
          count(*) FILTER (WHERE j.finished_at IS NOT NULL) AS terminal,
          count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded,
          count(*) FILTER (WHERE j.status = 'failed') AS failed,
          percentile_cont(0.95) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (j.started_at - COALESCE(j.queued_at, j.created_at)))
          ) FILTER (WHERE j.started_at IS NOT NULL) AS queue_wait_p95_seconds,
          percentile_cont(0.95) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (j.finished_at - j.started_at))
          ) FILTER (WHERE j.started_at IS NOT NULL AND j.finished_at IS NOT NULL) AS run_p95_seconds,
          percentile_cont(0.95) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (j.finished_at - j.created_at))
          ) FILTER (WHERE j.finished_at IS NOT NULL) AS lifecycle_p95_seconds
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
        {clause}
        """,
        _base_params(filters),
    )


async def stuck(db: AsyncSession, filters: DashboardFilters, *, older_than: timedelta = timedelta(minutes=10)) -> list[dict[str, Any]]:
    clause = _scope_clause("j", "family")
    params = _base_params(filters) | {"cutoff": _now() - older_than}
    return await _all(
        db,
        f"""
        (
          SELECT 'dispatch_due_not_published' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 d.id::text AS related_id, d.status AS related_status,
                 d.created_at AS since_at, d.next_attempt_at,
                 COALESCE(d.last_error->>'code', '-') AS detail_code
          FROM job_aggregates j
          JOIN job_execution_attempts a ON a.id = j.active_attempt_id
          JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
          WHERE j.deleted_at IS NULL
            AND j.status IN ('queued', 'running')
            AND a.status = 'pending'
            AND d.status IN ('pending', 'retrying')
            AND d.created_at < :cutoff
            AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= now())
            {clause}
        )
        UNION ALL
        (
          SELECT 'published_dispatch_not_claimed' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 d.id::text AS related_id, d.status AS related_status,
                 d.published_at AS since_at, d.next_attempt_at,
                 COALESCE(d.last_error->>'code', '-') AS detail_code
          FROM job_aggregates j
          JOIN job_execution_attempts a ON a.id = j.active_attempt_id
          JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
          WHERE j.deleted_at IS NULL
            AND j.status IN ('queued', 'running')
            AND a.status = 'pending'
            AND d.status = 'published'
            AND d.published_at < :cutoff
            {clause}
        )
        UNION ALL
        (
          SELECT 'running_attempt_lease_expired' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 a.id::text AS related_id, a.status AS related_status,
                 a.lease_expires_at AS since_at, NULL::timestamptz AS next_attempt_at,
                 COALESCE(a.error->>'code', '-') AS detail_code
          FROM job_aggregates j
          JOIN job_execution_attempts a ON a.id = j.active_attempt_id
          WHERE j.deleted_at IS NULL
            AND j.status = 'running'
            AND a.status = 'running'
            AND a.lease_expires_at < :cutoff
            {clause}
        )
        UNION ALL
        (
          SELECT 'callback_lease_expired' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 c.id::text AS related_id, c.status AS related_status,
                 c.lease_expires_at AS since_at, c.next_attempt_at,
                 COALESCE(c.last_error->>'code', '-') AS detail_code
          FROM callback_outbox c
          JOIN job_aggregates j ON j.id = c.job_id
          WHERE j.deleted_at IS NULL
            AND c.status = 'leased'
            AND c.lease_expires_at < :cutoff
            {clause}
        )
        UNION ALL
        (
          SELECT 'terminal_callback_not_settled' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 c.id::text AS related_id, c.status AS related_status,
                 j.finished_at AS since_at, c.next_attempt_at,
                 COALESCE(c.last_error->>'code', '-') AS detail_code
          FROM job_aggregates j
          JOIN callback_outbox c ON c.job_id = j.id
          WHERE j.deleted_at IS NULL
            AND j.status IN ('succeeded', 'failed')
            AND c.status IN ('pending', 'leased', 'retrying')
            AND j.finished_at < :cutoff
            {clause}
        )
        ORDER BY since_at ASC NULLS LAST
        LIMIT :limit
        """,
        params,
    )


async def callbacks_summary(db: AsyncSession, filters: DashboardFilters) -> list[dict[str, Any]]:
    clause = _scope_clause("j", "root")
    return await _all(
        db,
        f"""
        SELECT
          c.status,
          count(*) AS count,
          count(*) FILTER (
            WHERE c.status IN ('pending', 'failed', 'retrying')
              AND (c.next_attempt_at IS NULL OR c.next_attempt_at <= now())
          ) AS due,
          min(c.created_at) AS oldest_created_at,
          max(c.updated_at) AS newest_updated_at,
          min(c.next_attempt_at) FILTER (
            WHERE c.status IN ('pending', 'failed', 'retrying')
          ) AS next_attempt_at,
          max(c.delivery_attempts) AS max_delivery_attempts_seen,
          max(c.last_http_status) FILTER (WHERE c.last_http_status IS NOT NULL) AS last_http_status_seen
        FROM callback_outbox c
        JOIN job_aggregates j ON j.id = c.job_id
        WHERE j.deleted_at IS NULL
        {clause}
        GROUP BY c.status
        ORDER BY c.status ASC
        """,
        _base_params(filters),
    )


async def failure_groups(db: AsyncSession, filters: DashboardFilters) -> list[dict[str, Any]]:
    clause = _scope_clause("j", "family")
    return await _all(
        db,
        f"""
        SELECT
          COALESCE(j.error->>'code', '-') AS error_code,
          COALESCE(a.error_kind, '-') AS error_kind,
          COALESCE(a.failure_phase, '-') AS failure_phase,
          COALESCE(j.error->'details'->>'type', '-') AS detail_type,
          count(*) AS count,
          max(j.updated_at) AS newest_updated_at
        FROM job_aggregates j
        LEFT JOIN job_execution_attempts a ON a.id = j.active_attempt_id
        WHERE j.deleted_at IS NULL
          AND j.status = 'failed'
          {clause}
        GROUP BY 1, 2, 3, 4
        ORDER BY count DESC, newest_updated_at DESC
        LIMIT :limit
        """,
        _base_params(filters),
    )


async def failed_samples(db: AsyncSession, filters: DashboardFilters) -> list[dict[str, Any]]:
    clause = _scope_clause("j", "root")
    return await _all(
        db,
        f"""
        SELECT
          j.id::text AS job_id,
          j.status,
          j.job_type,
          j.caller_id,
          j.client_request_id,
          j.progress_percent,
          j.progress_stage,
          j.created_at,
          j.started_at,
          j.finished_at,
          j.updated_at
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
          AND j.status = 'failed'
          {clause}
        ORDER BY j.updated_at DESC
        LIMIT :limit
        """,
        _base_params(filters),
    )


async def overview_data(db: AsyncSession, filters: DashboardFilters, *, max_active_jobs: int) -> dict[str, Any]:
    summary_payload = await summary(db, filters)
    stuck_rows = await stuck(db, filters)
    callback_rows = await callbacks_summary(db, filters)
    return {
        "generated_at": _now(),
        "filters": filters.__dict__,
        "health": health_verdict(summary=summary_payload, stuck=stuck_rows, callbacks=callback_rows),
        "summary": summary_payload,
        "capacity": {"current": await global_gate(db, max_active_jobs=max_active_jobs)},
        "ingress": await ingress(db, filters),
        "latency": await latency(db, filters),
        "stuck": {"count": len(stuck_rows), "sample": stuck_rows},
    }


async def failures_data(db: AsyncSession, filters: DashboardFilters) -> dict[str, Any]:
    callback_rows = await callbacks_summary(db, filters)
    stuck_rows = await stuck(db, filters)
    return {
        "generated_at": _now(),
        "filters": filters.__dict__,
        "failure_groups": await failure_groups(db, filters),
        "failed_samples": await failed_samples(db, filters),
        "callbacks": callback_rows,
        "stuck": {"count": len(stuck_rows), "sample": stuck_rows},
    }


def _summary_of(value: Any) -> dict[str, Any]:
    if value is None:
        return {"present": False}
    if isinstance(value, dict):
        return {
            "present": True,
            "type": "dict",
            "key_count": len(value),
            "keys": sorted(str(key) for key in value.keys())[:50],
        }
    if isinstance(value, list):
        return {"present": True, "type": "list", "count": len(value)}
    return {"present": True, "type": type(value).__name__}


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                """
                SELECT
                  j.id::text AS job_id,
                  j.root_job_id::text AS root_job_id,
                  j.workflow_node_key,
                  j.status,
                  j.job_type,
                  j.caller_id,
                  j.client_request_id,
                  j.progress_percent,
                  j.progress_stage,
                  j.progress_text,
                  j.metadata,
                  j.job_params_ref,
                  j.runtime_ref,
                  j.result,
                  j.canonical_result,
                  j.error,
                  j.error->>'code' AS error_code,
                  j.error->>'message' AS error_message,
                  j.active_attempt_id::text AS active_attempt_id,
                  j.created_at,
                  j.started_at,
                  j.finished_at,
                  j.updated_at,
                  CASE
                    WHEN j.callback_url IS NULL THEN 'not_configured'
                    WHEN cb.status IS NULL THEN 'pending'
                    WHEN cb.status = 'leased' THEN 'delivering'
                    WHEN cb.status = 'dead_letter' THEN 'failed'
                    WHEN cb.status = 'skipped' AND cb.last_error IS NOT NULL THEN 'failed'
                    WHEN cb.status = 'skipped' THEN 'not_configured'
                    ELSE cb.status
                  END AS callback_status
                FROM job_aggregates j
                LEFT JOIN LATERAL (
                  SELECT c.status, c.last_error
                  FROM callback_outbox c
                  WHERE c.job_id = j.id
                  ORDER BY c.created_at DESC
                  LIMIT 1
                ) cb ON TRUE
                WHERE j.id = :job_id
                  AND j.deleted_at IS NULL
                """
            ),
            {"job_id": job_id},
        )
    ).mappings().first()
    if row is None:
        return None
    data = dict(row)
    error = data.pop("error", None)
    return data | {
        "metadata_summary": _summary_of(data.pop("metadata", None)),
        "job_params_summary": _summary_of(data.pop("job_params_ref", None)),
        "runtime_summary": _summary_of(data.pop("runtime_ref", None)),
        "result_summary": _summary_of(data.pop("result", None)),
        "canonical_result_summary": _summary_of(data.pop("canonical_result", None)),
        "error_summary": _summary_of(error),
    }


async def attempts(db: AsyncSession, job_id: uuid.UUID) -> list[dict[str, Any]]:
    return await _all(
        db,
        """
        SELECT a.id::text, a.job_id::text, a.purpose, a.purpose_attempt_no, a.status,
               d.status AS dispatch_status, d.published_at,
               d.publish_attempts, d.next_attempt_at, d.last_error->>'message' AS dispatch_last_error_message,
               a.worker_id, a.leased_at, a.lease_expires_at,
               a.heartbeat_at, a.started_at, a.finished_at, a.timeout_seconds,
               a.error->>'code' AS error_code, a.error->>'message' AS error_message,
               a.error_kind, a.failure_phase, a.retry_eligible, a.retry_decision,
               a.retry_decision_reason, a.policy_max_attempts, a.policy_retryable_error_codes,
               a.next_attempt_scheduled_at,
               a.created_at, a.updated_at
        FROM job_execution_attempts a
        JOIN job_aggregates j ON j.id = a.job_id
        LEFT JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
        WHERE a.job_id = :job_id
          AND j.deleted_at IS NULL
        ORDER BY a.purpose ASC, a.purpose_attempt_no ASC, a.created_at ASC
        """,
        {"job_id": job_id},
    )


async def callbacks(db: AsyncSession, job_id: uuid.UUID) -> list[dict[str, Any]]:
    return await _all(
        db,
        """
        SELECT c.id::text, c.job_id::text, c.event_id::text, c.event_type,
               c.status, c.delivery_attempts, c.next_attempt_at,
               c.lease_expires_at, c.last_http_status,
               c.last_error->>'message' AS last_error_message,
               c.first_attempt_at, c.last_attempt_at,
               c.delivered_at, c.dead_lettered_at, c.created_at, c.updated_at
        FROM callback_outbox c
        JOIN job_aggregates j ON j.id = c.job_id
        WHERE c.job_id = :job_id
          AND j.deleted_at IS NULL
        ORDER BY c.created_at DESC
        """,
        {"job_id": job_id},
    )


async def timeline(db: AsyncSession, job_id: uuid.UUID, *, limit: int) -> list[dict[str, Any]]:
    rows = await _all(
        db,
        """
        SELECT *
        FROM (
          SELECT e.id::text, e.job_id::text, e.attempt_id::text, e.callback_id::text,
                 e.event_type, e.from_status, e.to_status, e.reason, e.payload, e.created_at
          FROM job_audit_events e
          JOIN job_aggregates j ON j.id = e.job_id
          WHERE e.job_id = :job_id
            AND j.deleted_at IS NULL
          ORDER BY e.created_at DESC
          LIMIT :limit
        ) recent_events
        ORDER BY created_at ASC
        """,
        {"job_id": job_id, "limit": limit},
    )
    return [row | {"payload_summary": _summary_of(row.pop("payload", None))} for row in rows]


async def workflow_children(db: AsyncSession, root_job_id: uuid.UUID) -> list[dict[str, Any]]:
    return await _all(
        db,
        """
        SELECT
          j.workflow_node_key,
          j.id::text AS job_id,
          j.status,
          j.job_type,
          j.progress_percent,
          j.progress_stage,
          a.status AS attempt_status,
          a.purpose_attempt_no AS attempt_no,
          d.status AS dispatch_status,
          d.publish_attempts,
          j.created_at,
          j.started_at,
          j.updated_at,
          j.finished_at
        FROM job_aggregates j
        LEFT JOIN job_execution_attempts a ON a.id = j.active_attempt_id
        LEFT JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
        WHERE j.deleted_at IS NULL
          AND j.root_job_id = :root_job_id
          AND j.workflow_node_key IS NOT NULL
        ORDER BY j.created_at ASC
        """,
        {"root_job_id": root_job_id},
    )


async def ai_calls(db: AsyncSession, job_id: uuid.UUID) -> list[dict[str, Any]]:
    return await _all(
        db,
        """
        SELECT
          l.id::text,
          l.job_id::text,
          l.attempt_id::text,
          l.operation,
          l.step_name,
          l.request_id,
          l.job_type,
          l.model_id,
          l.provider,
          l.provider_model,
          l.status,
          l.failure_phase,
          l.error_code,
          l.error_message,
          l.input_size_bytes,
          l.output_size_bytes,
          l.billable_status,
          l.cost_calculation_status,
          l.started_at,
          l.completed_at,
          l.duration_ms,
          l.created_at
        FROM ai_call_ledger_entries l
        JOIN job_aggregates j ON j.id = l.job_id
        WHERE (l.job_id = :job_id OR l.scope_id = :job_id_text)
          AND j.deleted_at IS NULL
        ORDER BY l.created_at ASC, l.id ASC
        """,
        {"job_id": job_id, "job_id_text": str(job_id)},
    )


async def job_trace_data(db: AsyncSession, job_id: uuid.UUID, *, limit: int = 100) -> dict[str, Any] | None:
    job = await get_job(db, job_id)
    if job is None:
        return None
    root_job_id = uuid.UUID(job["root_job_id"]) if job.get("root_job_id") else job_id
    return {
        "generated_at": _now(),
        "job": job,
        "attempts": await attempts(db, job_id),
        "callbacks": await callbacks(db, job_id),
        "timeline": await timeline(db, job_id, limit=limit),
        "workflow_children": await workflow_children(db, root_job_id),
        "ai_calls": await ai_calls(db, job_id),
    }
