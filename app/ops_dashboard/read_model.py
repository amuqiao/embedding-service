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
    "client_request_id": String(),
    "run_id": String(),
    "status": String(),
    "since_at": DateTime(timezone=True),
    "until_at": DateTime(timezone=True),
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
    AND (:run_id IS NULL OR root.metadata->>'run_id' = :run_id)
    AND (:since_at IS NULL OR root.created_at >= :since_at)
    AND (:until_at IS NULL OR root.created_at < :until_at)
)
"""


def _scope_clause(alias: str, record_scope: str) -> str:
    if record_scope == "family":
        return _family_scope_clause(alias)
    return f"""
{_lineage_scope_clause(alias, record_scope)}
AND (:job_type IS NULL OR {alias}.job_type = :job_type)
AND (:caller_id IS NULL OR {alias}.caller_id = :caller_id)
AND (:run_id IS NULL OR {alias}.metadata->>'run_id' = :run_id)
AND (:since_at IS NULL OR {alias}.created_at >= :since_at)
AND (:until_at IS NULL OR {alias}.created_at < :until_at)
"""


def _scope_clause_without_since(alias: str, record_scope: str) -> str:
    if record_scope == "family":
        raise ValueError("family scope without since is not supported")
    return f"""
{_lineage_scope_clause(alias, record_scope)}
AND (:job_type IS NULL OR {alias}.job_type = :job_type)
AND (:caller_id IS NULL OR {alias}.caller_id = :caller_id)
AND (:run_id IS NULL OR {alias}.metadata->>'run_id' = :run_id)
"""


def _root_job_filter_clause(alias: str) -> str:
    return f"""
{ROOT_SCOPE_SQL.format(alias=alias)}
AND (:job_type IS NULL OR {alias}.job_type = :job_type)
AND (:caller_id IS NULL OR {alias}.caller_id = :caller_id)
AND (:client_request_id IS NULL OR {alias}.client_request_id = :client_request_id)
AND (:run_id IS NULL OR {alias}.metadata->>'run_id' = :run_id)
AND (:since_at IS NULL OR {alias}.created_at >= :since_at)
AND (:until_at IS NULL OR {alias}.created_at < :until_at)
"""


def _base_params(filters: DashboardFilters) -> dict[str, Any]:
    return {
        "job_type": filters.job_type,
        "caller_id": filters.caller_id,
        "run_id": filters.run_id,
        "since_at": filters.range_start_at,
        "until_at": filters.range_end_at,
        "limit": filters.sample_limit,
    }


def _jobs_cli_filter_args(filters: DashboardFilters) -> str:
    args = ""
    if filters.job_type:
        args += f" --job-type {filters.job_type}"
    if filters.caller_id:
        args += f" --caller-id {filters.caller_id}"
    if filters.run_id:
        args += f" --run-id {filters.run_id}"
    return args


def _flow_capacity_next_checks(filters: DashboardFilters) -> list[str]:
    filter_args = _jobs_cli_filter_args(filters)
    return [
        f"./scripts/jobs.sh capacity --since {filters.window}{filter_args}",
        f"./scripts/jobs.sh ingress --since {filters.window} --bucket {filters.resolved_bucket}{filter_args}",
        f"./scripts/jobs.sh drain --since {filters.window} --older-than 10m{filter_args}",
        f"./scripts/jobs.sh latency --since {filters.window} --group-by job_type{filter_args}",
        "./scripts/jobs.sh broker",
        "./scripts/jobs.sh runtime",
    ]


def _failures_callbacks_next_checks(filters: DashboardFilters) -> list[str]:
    filter_args = _jobs_cli_filter_args(filters)
    return [
        f"./scripts/jobs.sh failures --since {filters.window}{filter_args}",
        f"./scripts/jobs.sh callbacks-summary --since {filters.window}{filter_args}",
        f"./scripts/jobs.sh list --status failed --scope family --since {filters.window}{filter_args} --limit 20",
    ]


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
          count(*) FILTER (WHERE j.finished_at IS NOT NULL) AS terminal,
          CASE
            WHEN count(*) FILTER (WHERE j.finished_at IS NOT NULL) = 0 THEN NULL
            ELSE
              (count(*) FILTER (WHERE j.status = 'succeeded'))::float
              / (count(*) FILTER (WHERE j.finished_at IS NOT NULL))::float
          END AS success_rate,
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
        "headroom": (max_active_jobs - active) if max_active_jobs > 0 else None,
    }


async def capacity_window(db: AsyncSession, filters: DashboardFilters) -> dict[str, Any]:
    clause = _scope_clause("j", "root")
    params = _base_params(filters) | {"window_seconds": filters.window_delta.total_seconds()}
    window = await _one(
        db,
        f"""
        SELECT
          count(*) AS accepted_jobs,
          count(*) FILTER (WHERE j.finished_at IS NOT NULL) AS terminal_jobs,
          min(j.created_at) AS first_created_at,
          max(j.created_at) AS newest_created_at,
          percentile_cont(0.95) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (j.finished_at - j.created_at))
          ) FILTER (WHERE j.finished_at IS NOT NULL) AS lifecycle_p95_seconds,
          CASE
            WHEN min(j.created_at) IS NOT NULL
             AND max(j.created_at) IS NOT NULL
             AND max(j.created_at) > min(j.created_at)
            THEN EXTRACT(EPOCH FROM (max(j.created_at) - min(j.created_at)))
            ELSE NULL
          END AS observed_span_seconds
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
        {clause}
        """,
        params,
    )
    accepted_jobs = int(window.get("accepted_jobs") or 0)
    observed_span_seconds = window.get("observed_span_seconds")
    effective_window_seconds = (
        float(observed_span_seconds)
        if observed_span_seconds is not None and float(observed_span_seconds) > 0
        else float(params["window_seconds"])
    )
    lifecycle_p95_seconds = window.get("lifecycle_p95_seconds")
    accepted_submit_rps = accepted_jobs / effective_window_seconds if effective_window_seconds > 0 else None
    active_jobs_needed_upper_bound = (
        accepted_submit_rps * float(lifecycle_p95_seconds)
        if accepted_submit_rps is not None and lifecycle_p95_seconds is not None
        else None
    )
    return window | {
        "window_seconds": params["window_seconds"],
        "effective_window_seconds": effective_window_seconds,
        "accepted_submit_rps": accepted_submit_rps,
        "active_jobs_needed_upper_bound": active_jobs_needed_upper_bound,
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
            AND j.created_at < :until_at
            {clause}
          UNION ALL
          SELECT j.started_at AS event_at, 'started' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.started_at IS NOT NULL
            AND j.started_at >= :since_at
            AND j.started_at < :until_at
            {clause}
          UNION ALL
          SELECT j.finished_at AS event_at, 'terminal' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.finished_at IS NOT NULL
            AND j.finished_at >= :since_at
            AND j.finished_at < :until_at
            {clause}
          UNION ALL
          SELECT j.finished_at AS event_at, 'failed' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.status = 'failed'
            AND j.finished_at IS NOT NULL
            AND j.finished_at >= :since_at
            AND j.finished_at < :until_at
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


async def status_composition(db: AsyncSession, filters: DashboardFilters) -> list[dict[str, Any]]:
    clause = _scope_clause_without_since("j", "root")
    params = _base_params(filters) | {"bucket_seconds": filters.bucket_seconds}
    return await _all(
        db,
        f"""
        SELECT
          to_timestamp(
            floor(EXTRACT(EPOCH FROM j.created_at) / :bucket_seconds) * :bucket_seconds
          ) AS bucket_at,
          count(*) FILTER (WHERE j.status = 'queued') AS queued,
          count(*) FILTER (WHERE j.status = 'running') AS running,
          count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded,
          count(*) FILTER (WHERE j.status = 'failed') AS failed
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
          AND j.created_at >= :since_at
          AND j.created_at < :until_at
          {clause}
        GROUP BY bucket_at
        ORDER BY bucket_at ASC
        """,
        params,
    )


async def drain_status(
    db: AsyncSession,
    filters: DashboardFilters,
    *,
    older_than: timedelta = timedelta(minutes=10),
) -> dict[str, Any]:
    current_clause = _scope_clause("j", "family")
    window_clause = _scope_clause("j", "family")
    current_params = {
        "job_type": filters.job_type,
        "caller_id": filters.caller_id,
        "run_id": filters.run_id,
        "since_at": None,
        "until_at": None,
    }
    window_params = _base_params(filters)
    current = await _one(
        db,
        f"""
        SELECT
          count(*) FILTER (WHERE j.status = 'queued') AS queued,
          count(*) FILTER (WHERE j.status = 'running') AS running,
          count(*) FILTER (WHERE j.status = 'running' AND j.active_attempt_id IS NOT NULL) AS running_active,
          count(*) FILTER (WHERE j.status = 'running' AND j.active_attempt_id IS NULL) AS running_inactive,
          count(*) FILTER (
            WHERE j.status = 'queued'
               OR (j.status = 'running' AND j.active_attempt_id IS NOT NULL)
          ) AS active_jobs
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
        {current_clause}
        """,
        current_params,
    )
    window = await _one(
        db,
        f"""
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE j.status = 'queued') AS queued,
          count(*) FILTER (WHERE j.status = 'running') AS running,
          count(*) FILTER (WHERE j.status = 'running' AND j.active_attempt_id IS NOT NULL) AS running_active,
          count(*) FILTER (WHERE j.status = 'running' AND j.active_attempt_id IS NULL) AS running_inactive,
          count(*) FILTER (
            WHERE j.status = 'queued'
               OR (j.status = 'running' AND j.active_attempt_id IS NOT NULL)
          ) AS active_jobs,
          count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded,
          count(*) FILTER (WHERE j.status = 'failed') AS failed,
          min(j.created_at) AS oldest_created_at,
          max(j.created_at) AS newest_created_at
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
        {window_clause}
        """,
        window_params,
    )
    stuck_payload = await stuck_report(db, filters, older_than=older_than)
    status = "drained"
    if (
        int(current.get("active_jobs") or 0)
        or int(current.get("running_inactive") or 0)
        or int(window.get("active_jobs") or 0)
        or int(window.get("failed") or 0)
        or int(stuck_payload.get("total") or 0)
    ):
        status = "not_drained"
    return {
        "status": status,
        "current": current,
        "window": window,
        "stuck": stuck_payload,
    }


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
          CASE
            WHEN count(*) FILTER (WHERE j.finished_at IS NOT NULL) = 0 THEN NULL
            ELSE
              (count(*) FILTER (WHERE j.status = 'succeeded'))::float
              / (count(*) FILTER (WHERE j.finished_at IS NOT NULL))::float
          END AS success_rate,
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


async def job_type_hotspots(db: AsyncSession, filters: DashboardFilters) -> list[dict[str, Any]]:
    clause = _scope_clause("j", "root")
    return await _all(
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
        GROUP BY j.job_type
        ORDER BY active_jobs DESC, total DESC, lifecycle_p95_seconds DESC NULLS LAST, j.job_type ASC
        LIMIT :limit
        """,
        _base_params(filters),
    )


def _stuck_union_sql(clause: str) -> str:
    return f"""
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
        """


async def stuck(db: AsyncSession, filters: DashboardFilters, *, older_than: timedelta = timedelta(minutes=10)) -> list[dict[str, Any]]:
    clause = _scope_clause("j", "family")
    params = _base_params(filters) | {"cutoff": _now() - older_than}
    return await _all(
        db,
        f"""
        {_stuck_union_sql(clause)}
        ORDER BY since_at ASC NULLS LAST
        LIMIT :limit
        """,
        params,
    )


async def stuck_total(
    db: AsyncSession,
    filters: DashboardFilters,
    *,
    older_than: timedelta = timedelta(minutes=10),
) -> int:
    clause = _scope_clause("j", "family")
    params = _base_params(filters) | {"cutoff": _now() - older_than}
    row = await _one(
        db,
        f"""
        SELECT count(*) AS total
        FROM (
          {_stuck_union_sql(clause)}
        ) stuck_rows
        """,
        params,
    )
    return int(row.get("total") or 0)


async def stuck_report(
    db: AsyncSession,
    filters: DashboardFilters,
    *,
    older_than: timedelta = timedelta(minutes=10),
) -> dict[str, Any]:
    total = await stuck_total(db, filters, older_than=older_than)
    sample = await stuck(db, filters, older_than=older_than)
    return {
        "total": total,
        "count": total,
        "sample": sample,
        "truncated": total > len(sample),
    }


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
          max(c.last_http_status) FILTER (WHERE c.last_http_status IS NOT NULL) AS last_http_status_seen,
          max(c.last_error->>'code') FILTER (WHERE c.last_error IS NOT NULL) AS sample_last_error_code,
          EXTRACT(EPOCH FROM (now() - min(c.created_at))) AS oldest_age_seconds
        FROM callback_outbox c
        JOIN job_aggregates j ON j.id = c.job_id
        WHERE j.deleted_at IS NULL
        {clause}
        GROUP BY c.status
        ORDER BY
          CASE c.status
            WHEN 'pending' THEN 1
            WHEN 'leased' THEN 2
            WHEN 'delivering' THEN 3
            WHEN 'failed' THEN 4
            WHEN 'retrying' THEN 5
            WHEN 'dead_letter' THEN 6
            WHEN 'delivered' THEN 7
            WHEN 'skipped' THEN 8
            ELSE 9
          END,
          c.status ASC
        """,
        _base_params(filters),
    )


async def failure_summary(db: AsyncSession, filters: DashboardFilters) -> dict[str, Any]:
    clause = _scope_clause("j", "family")
    return await _one(
        db,
        f"""
        SELECT
          count(*) FILTER (WHERE j.status = 'failed') AS failed_records,
          count(DISTINCT COALESCE(j.root_job_id, j.id)) FILTER (WHERE j.status = 'failed') AS failed_roots,
          min(j.updated_at) FILTER (WHERE j.status = 'failed') AS oldest_failed_updated_at,
          max(j.updated_at) FILTER (WHERE j.status = 'failed') AS newest_failed_updated_at
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
          {clause}
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
    clause = _scope_clause("j", "family")
    rows = await _all(
        db,
        f"""
        SELECT
          j.id::text AS job_id,
          CASE WHEN j.root_job_id IS NULL THEN 'root' ELSE 'child' END AS record_scope,
          j.root_job_id::text AS root_job_id,
          j.workflow_node_key,
          j.status,
          j.job_type,
          j.caller_id,
          j.client_request_id,
          j.progress_percent,
          j.progress_stage,
          COALESCE(j.error->>'code', '-') AS error_code,
          CASE
            WHEN j.callback_url IS NULL THEN 'not_configured'
            WHEN cb.status IS NULL THEN 'pending'
            WHEN cb.status = 'leased' THEN 'delivering'
            WHEN cb.status = 'dead_letter' THEN 'failed'
            WHEN cb.status = 'skipped' AND cb.last_error IS NOT NULL THEN 'failed'
            WHEN cb.status = 'skipped' THEN 'not_configured'
            ELSE cb.status
          END AS callback_status,
          a.status AS attempt_status,
          a.purpose_attempt_no AS attempt_no,
          d.status AS dispatch_status,
          d.publish_attempts,
          a.lease_expires_at,
          j.created_at,
          j.started_at,
          j.finished_at,
          j.updated_at,
          EXTRACT(EPOCH FROM (now() - j.created_at)) AS age_seconds,
          CASE
            WHEN j.started_at IS NULL THEN NULL
            WHEN j.finished_at IS NULL THEN EXTRACT(EPOCH FROM (now() - j.started_at))
            ELSE EXTRACT(EPOCH FROM (j.finished_at - j.started_at))
          END AS duration_seconds
        FROM job_aggregates j
        LEFT JOIN job_execution_attempts a ON a.id = j.active_attempt_id
        LEFT JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
        LEFT JOIN LATERAL (
          SELECT c.status, c.last_error
          FROM callback_outbox c
          WHERE c.job_id = j.id
          ORDER BY c.created_at DESC
          LIMIT 1
        ) cb ON TRUE
        WHERE j.deleted_at IS NULL
          AND j.status = 'failed'
          {clause}
        ORDER BY j.updated_at DESC
        LIMIT :limit
        """,
        _base_params(filters),
    )
    return [
        row
        | {
            "duration_or_age_seconds": (
                row.get("duration_seconds")
                if row.get("duration_seconds") is not None
                else row.get("age_seconds")
            ),
        }
        for row in rows
    ]


async def callback_samples(db: AsyncSession, filters: DashboardFilters) -> list[dict[str, Any]]:
    clause = _scope_clause("j", "root")
    return await _all(
        db,
        f"""
        SELECT
          c.id::text AS callback_id,
          c.job_id::text AS job_id,
          c.event_type,
          c.status,
          j.job_type,
          j.caller_id,
          j.client_request_id,
          c.delivery_attempts,
          c.max_delivery_attempts,
          c.next_attempt_at,
          c.lease_expires_at,
          c.last_http_status,
          COALESCE(c.last_error->>'code', '-') AS last_error_code,
          c.created_at,
          c.updated_at,
          c.delivered_at,
          c.dead_lettered_at
        FROM callback_outbox c
        JOIN job_aggregates j ON j.id = c.job_id
        WHERE j.deleted_at IS NULL
          AND (
            c.status IN ('leased', 'dead_letter')
            OR (
              c.status IN ('pending', 'failed', 'retrying')
              AND (c.next_attempt_at IS NULL OR c.next_attempt_at <= now())
            )
          )
          {clause}
        ORDER BY
          CASE
            WHEN c.status = 'dead_letter' THEN 1
            WHEN c.status = 'leased' THEN 2
            WHEN c.status IN ('pending', 'failed', 'retrying') THEN 3
            ELSE 5
          END,
          c.next_attempt_at ASC NULLS FIRST,
          c.updated_at DESC
        LIMIT :limit
        """,
        _base_params(filters),
    )


async def recent_jobs_summary(
    db: AsyncSession,
    filters: DashboardFilters,
    *,
    status: str | None,
    client_request_id: str | None,
) -> dict[str, Any]:
    clause = _root_job_filter_clause("j")
    params = _base_params(filters) | {
        "status": status,
        "client_request_id": client_request_id,
    }
    return await _one(
        db,
        f"""
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE j.status = 'queued') AS queued,
          count(*) FILTER (WHERE j.status = 'running') AS running,
          count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded,
          count(*) FILTER (WHERE j.status = 'failed') AS failed,
          count(*) FILTER (WHERE j.finished_at IS NOT NULL) AS terminal,
          min(j.created_at) AS oldest_created_at,
          max(j.created_at) AS newest_created_at,
          max(j.updated_at) AS newest_updated_at
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
          AND (:status IS NULL OR j.status = :status)
          {clause}
        """,
        params,
    )


async def recent_jobs(
    db: AsyncSession,
    filters: DashboardFilters,
    *,
    status: str | None,
    client_request_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clause = _root_job_filter_clause("j")
    params = _base_params(filters) | {
        "status": status,
        "client_request_id": client_request_id,
        "limit": limit,
    }
    rows = await _all(
        db,
        f"""
        SELECT
          j.id::text AS job_id,
          'root' AS record_scope,
          j.root_job_id::text AS root_job_id,
          j.workflow_node_key,
          j.status,
          j.job_type,
          j.caller_id,
          j.client_request_id,
          j.progress_percent,
          j.progress_stage,
          CASE
            WHEN j.callback_url IS NULL THEN 'not_configured'
            WHEN cb.status IS NULL THEN 'pending'
            WHEN cb.status = 'leased' THEN 'delivering'
            WHEN cb.status = 'dead_letter' THEN 'failed'
            WHEN cb.status = 'skipped' AND cb.last_error IS NOT NULL THEN 'failed'
            WHEN cb.status = 'skipped' THEN 'not_configured'
            ELSE cb.status
          END AS callback_status,
          a.status AS attempt_status,
          a.purpose_attempt_no AS attempt_no,
          d.status AS dispatch_status,
          d.publish_attempts,
          a.lease_expires_at,
          j.created_at,
          j.started_at,
          j.finished_at,
          j.updated_at,
          EXTRACT(EPOCH FROM (now() - j.created_at)) AS age_seconds,
          CASE
            WHEN j.started_at IS NULL THEN NULL
            WHEN j.finished_at IS NULL THEN EXTRACT(EPOCH FROM (now() - j.started_at))
            ELSE EXTRACT(EPOCH FROM (j.finished_at - j.started_at))
          END AS duration_seconds
        FROM job_aggregates j
        LEFT JOIN job_execution_attempts a ON a.id = j.active_attempt_id
        LEFT JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
        LEFT JOIN LATERAL (
          SELECT c.status, c.last_error
          FROM callback_outbox c
          WHERE c.job_id = j.id
          ORDER BY c.created_at DESC
          LIMIT 1
        ) cb ON TRUE
        WHERE j.deleted_at IS NULL
          AND (:status IS NULL OR j.status = :status)
          {clause}
        ORDER BY j.created_at DESC
        LIMIT :limit
        """,
        params,
    )
    return [
        row
        | {
            "duration_or_age_seconds": row.get("duration_seconds") or row.get("age_seconds"),
        }
        for row in rows
    ]


async def recent_jobs_data(
    db: AsyncSession,
    filters: DashboardFilters,
    *,
    status: str,
    client_request_id: str | None,
    limit: int,
) -> dict[str, Any]:
    normalized_status = None if status == "all" else status
    summary_payload = await recent_jobs_summary(
        db,
        filters,
        status=normalized_status,
        client_request_id=client_request_id,
    )
    rows = await recent_jobs(
        db,
        filters,
        status=normalized_status,
        client_request_id=client_request_id,
        limit=limit,
    )
    return {
        "generated_at": _now(),
        "filters": filters.as_payload(),
        "controls": {"status": status, "client_request_id": client_request_id, "limit": limit},
        "status_options": ["all", "queued", "running", "succeeded", "failed"],
        "summary": summary_payload,
        "jobs": rows,
        "health": {
            "status": "ok",
            "reasons": [],
            "next_checks": ["./scripts/jobs.sh list --status succeeded,failed --json"],
        },
    }


async def flow_capacity_data(db: AsyncSession, filters: DashboardFilters, *, max_active_jobs: int) -> dict[str, Any]:
    summary_payload = await summary(db, filters)
    capacity_current = await global_gate(db, max_active_jobs=max_active_jobs)
    capacity_window_payload = await capacity_window(db, filters)
    return {
        "generated_at": _now(),
        "filters": filters.as_payload(),
        "health": {
            "status": "ok",
            "reasons": [],
            "next_checks": _flow_capacity_next_checks(filters),
        },
        "summary": summary_payload,
        "capacity": {
            "current": capacity_current,
            "window": capacity_window_payload,
        },
        "drain": await drain_status(db, filters),
        "ingress": await ingress(db, filters),
        "status_composition": await status_composition(db, filters),
        "latency": await latency(db, filters),
        "job_type_hotspots": await job_type_hotspots(db, filters),
        "query_scopes": {
            "capacity.current": "global_gate current active; ignores window/job_type/caller_id",
            "capacity.window": "root scope created_at time range; applies job_type/caller_id",
            "drain.current": "family scope current active; applies root job_type/caller_id, ignores window",
            "drain.window": "family scope created_at time range; applies root job_type/caller_id",
            "drain.stuck": "family scope stuck total/sample/truncated; applies root created_at time range and root job_type/caller_id",
            "ingress": "root event-time buckets for created/started/finished events; applies job_type/caller_id",
            "status_composition": "dashboard root created_at buckets; applies job_type/caller_id",
            "latency": "root scope created_at time range; applies job_type/caller_id",
            "job_type_hotspots": "root scope created_at time range; applies job_type/caller_id; grouped by job_type",
        },
    }


async def overview_data(db: AsyncSession, filters: DashboardFilters, *, max_active_jobs: int) -> dict[str, Any]:
    summary_payload = await summary(db, filters)
    stuck_payload = await stuck_report(db, filters)
    callback_rows = await callbacks_summary(db, filters)
    return {
        "generated_at": _now(),
        "filters": filters.as_payload(),
        "health": health_verdict(
            summary=summary_payload,
            stuck=stuck_payload["sample"],
            callbacks=callback_rows,
            filters=filters,
        ),
        "summary": summary_payload,
        "capacity": {"current": await global_gate(db, max_active_jobs=max_active_jobs)},
        "ingress": await ingress(db, filters),
        "latency": await latency(db, filters),
        "stuck": stuck_payload,
    }


def _callbacks_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total": 0,
        "due": 0,
        "delivered": 0,
        "dead_letter": 0,
        "pending": 0,
        "leased": 0,
        "retrying": 0,
        "skipped": 0,
        "failed": 0,
    }
    for row in rows:
        status = str(row.get("status") or "")
        count = int(row.get("count") or 0)
        counts["total"] += count
        counts["due"] += int(row.get("due") or 0)
        if status in counts:
            counts[status] += count
    return counts


def _failures_health(*, failure: dict[str, Any], callbacks: dict[str, int]) -> dict[str, Any]:
    reasons: list[str] = []
    severity = "ok"
    if int(callbacks.get("dead_letter") or 0):
        severity = "critical"
        reasons.append("callback_dead_letter")
    if int(callbacks.get("due") or 0):
        if severity != "critical":
            severity = "warning"
        reasons.append("callback_due")
    if int(failure.get("failed_records") or 0):
        if severity != "critical":
            severity = "warning"
        reasons.append("failed_jobs")
    return {"status": severity, "reasons": reasons}


async def failures_data(db: AsyncSession, filters: DashboardFilters) -> dict[str, Any]:
    callback_rows = await callbacks_summary(db, filters)
    callback_counts = _callbacks_counts(callback_rows)
    failure_payload = await failure_summary(db, filters)
    health = _failures_health(failure=failure_payload, callbacks=callback_counts)
    return {
        "generated_at": _now(),
        "filters": filters.as_payload(),
        "health": health | {"next_checks": _failures_callbacks_next_checks(filters)},
        "failure_summary": failure_payload,
        "failure_groups": await failure_groups(db, filters),
        "failed_samples": await failed_samples(db, filters),
        "callback_summary": callback_counts,
        "callbacks": callback_rows,
        "callback_samples": await callback_samples(db, filters),
        "stuck": await stuck_report(db, filters),
        "query_scopes": {
            "failure_summary": "family scope created_at time range; applies root job_type/caller_id",
            "failure_groups": "family scope failed records grouped by summarized error fields",
            "failed_samples": "family scope failed samples; raw error message is not returned",
            "callbacks": "root scope callback_outbox grouped by status",
            "callback_samples": "root scope due/leased/dead_letter callback rows; raw last_error/last_response are not returned",
        },
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


def _load_summary_of(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {"present": False}
    summary = {
        key: metadata.get(key)
        for key in ("source", "run_id", "profile", "case_key", "sequence")
        if metadata.get(key) is not None
    }
    return {"present": bool(summary), **summary}


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
    metadata = data.pop("metadata", None)
    return data | {
        "load_summary": _load_summary_of(metadata),
        "metadata_summary": _summary_of(metadata),
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
