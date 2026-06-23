from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg2.extensions import connection


def _fetch_all(conn: connection, sql: str, params: dict[str, Any]) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def _fetch_one(conn: connection, sql: str, params: dict[str, Any]) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None


def _status_clause(statuses: list[str], table_alias: str) -> tuple[str, dict[str, Any]]:
    if not statuses:
        return "", {}
    return f"AND {table_alias}.status = ANY(%(statuses)s)", {"statuses": statuses}


def _common_filters(
    *,
    table_alias: str,
    statuses: list[str],
    job_type: str | None,
    caller_id: str | None,
    client_request_id: str | None,
    since: datetime | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    status_sql, status_params = _status_clause(statuses, table_alias)
    if status_sql:
        clauses.append(status_sql)
        params.update(status_params)
    if job_type is not None:
        clauses.append(f"AND {table_alias}.job_type = %(job_type)s")
        params["job_type"] = job_type
    if caller_id is not None:
        clauses.append(f"AND {table_alias}.caller_id = %(caller_id)s")
        params["caller_id"] = caller_id
    if client_request_id is not None:
        clauses.append(f"AND {table_alias}.client_request_id = %(client_request_id)s")
        params["client_request_id"] = client_request_id
    if since is not None:
        clauses.append(f"AND {table_alias}.created_at >= %(since)s")
        params["since"] = since
    return "\n".join(clauses), params


def list_jobs(
    conn: connection,
    *,
    statuses: list[str],
    job_type: str | None,
    caller_id: str | None,
    client_request_id: str | None,
    since: datetime | None,
    limit: int,
) -> list[dict]:
    filters, params = _common_filters(
        table_alias="j",
        statuses=statuses,
        job_type=job_type,
        caller_id=caller_id,
        client_request_id=client_request_id,
        since=since,
    )
    params["limit"] = limit
    return _fetch_all(
        conn,
        f"""
        SELECT
          j.id::text AS job_id,
          j.status,
          j.job_type,
          j.caller_id,
          j.client_request_id,
          j.progress_percent,
          j.progress_stage,
          j.callback_status,
          a.status AS attempt_status,
          a.attempt_no,
          d.status AS dispatch_status,
          d.publish_attempts,
          a.lease_expires_at,
          j.created_at,
          j.updated_at,
          now() - j.created_at AS age,
          CASE
            WHEN j.started_at IS NULL THEN NULL
            WHEN j.finished_at IS NULL THEN now() - j.started_at
            ELSE j.finished_at - j.started_at
          END AS duration
        FROM job_aggregates j
        LEFT JOIN job_execution_attempts a ON a.id = j.active_attempt_id
        LEFT JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
        WHERE j.deleted_at IS NULL
        {filters}
        ORDER BY j.created_at DESC
        LIMIT %(limit)s
        """,
        params,
    )


def get_job(conn: connection, job_id: str) -> dict | None:
    return _fetch_one(conn, "SELECT * FROM job_aggregates WHERE id = %(job_id)s AND deleted_at IS NULL", {"job_id": job_id})


def attempts(conn: connection, job_id: str) -> list[dict]:
    return _fetch_all(
        conn,
        """
        SELECT a.id::text, a.job_id::text, a.attempt_no, a.status,
               d.status AS dispatch_status, d.published_at,
               d.publish_attempts, d.next_attempt_at, d.last_error AS dispatch_last_error,
               a.worker_id, a.lease_token::text, a.leased_at, a.lease_expires_at,
               a.heartbeat_at, a.started_at, a.finished_at, a.timeout_seconds,
               a.error, a.error_kind, a.failure_phase, a.retryable,
               a.created_at, a.updated_at
        FROM job_execution_attempts a
        JOIN job_aggregates j ON j.id = a.job_id
        LEFT JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
        WHERE a.job_id = %(job_id)s
          AND j.deleted_at IS NULL
        ORDER BY a.attempt_no ASC, a.created_at ASC
        """,
        {"job_id": job_id},
    )


def callbacks(conn: connection, job_id: str) -> list[dict]:
    return _fetch_all(
        conn,
        """
        SELECT c.id::text, c.job_id::text, c.event_id::text, c.event_type,
               c.status, c.delivery_attempts, c.next_attempt_at,
               c.lease_token::text, c.lease_expires_at, c.last_http_status,
               c.last_error, c.first_attempt_at, c.last_attempt_at,
               c.delivered_at, c.dead_lettered_at, c.created_at, c.updated_at
        FROM callback_outbox c
        JOIN job_aggregates j ON j.id = c.job_id
        WHERE c.job_id = %(job_id)s
          AND j.deleted_at IS NULL
        ORDER BY c.created_at DESC
        """,
        {"job_id": job_id},
    )


def timeline(conn: connection, job_id: str, *, limit: int) -> list[dict]:
    return _fetch_all(
        conn,
        """
        SELECT e.id::text, e.job_id::text, e.attempt_id::text, e.callback_id::text,
               e.event_type, e.from_status, e.to_status, e.reason, e.payload, e.created_at
        FROM job_audit_events e
        JOIN job_aggregates j ON j.id = e.job_id
        WHERE e.job_id = %(job_id)s
          AND j.deleted_at IS NULL
        ORDER BY e.created_at ASC
        LIMIT %(limit)s
        """,
        {"job_id": job_id, "limit": limit},
    )


def stuck(conn: connection, *, older_than: timedelta, limit: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - older_than
    return _fetch_all(
        conn,
        """
        (
          SELECT 'dispatch_due_not_published' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 d.id::text AS related_id, d.status AS related_status,
                 d.created_at AS since_at, d.next_attempt_at,
                 d.last_error AS detail
          FROM job_aggregates j
          JOIN job_execution_attempts a ON a.id = j.active_attempt_id
          JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
          WHERE j.deleted_at IS NULL
            AND j.status IN ('queued', 'running')
            AND a.status = 'pending'
            AND d.status IN ('pending', 'retrying')
            AND d.created_at < %(cutoff)s
            AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= now())
        )
        UNION ALL
        (
          SELECT 'published_dispatch_not_claimed' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 d.id::text AS related_id, d.status AS related_status,
                 d.published_at AS since_at, d.next_attempt_at,
                 d.last_error AS detail
          FROM job_aggregates j
          JOIN job_execution_attempts a ON a.id = j.active_attempt_id
          JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
          WHERE j.deleted_at IS NULL
            AND j.status IN ('queued', 'running')
            AND a.status = 'pending'
            AND d.status = 'published'
            AND d.published_at < %(cutoff)s
            AND d.next_attempt_at <= now()
        )
        UNION ALL
        (
          SELECT 'running_attempt_lease_expired' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 a.id::text AS related_id, a.status AS related_status,
                 a.lease_expires_at AS since_at, NULL::timestamptz AS next_attempt_at,
                 a.error AS detail
          FROM job_aggregates j
          JOIN job_execution_attempts a ON a.id = j.active_attempt_id
          WHERE j.deleted_at IS NULL
            AND j.status = 'running'
            AND a.status = 'running'
            AND a.lease_expires_at < now()
        )
        UNION ALL
        (
          SELECT 'callback_lease_expired' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 c.id::text AS related_id, c.status AS related_status,
                 c.lease_expires_at AS since_at, c.next_attempt_at,
                 c.last_error AS detail
          FROM callback_outbox c
          JOIN job_aggregates j ON j.id = c.job_id
          WHERE j.deleted_at IS NULL
            AND c.status = 'leased'
            AND c.lease_expires_at < now()
        )
        UNION ALL
        (
          SELECT 'terminal_callback_not_settled' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 NULL::text AS related_id, j.callback_status AS related_status,
                 j.finished_at AS since_at, j.callback_next_retry_at AS next_attempt_at,
                 j.callback_last_error AS detail
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.status IN ('succeeded', 'failed')
            AND j.callback_status IN ('pending', 'delivering')
            AND j.finished_at < %(cutoff)s
        )
        ORDER BY since_at ASC NULLS LAST
        LIMIT %(limit)s
        """,
        {"cutoff": cutoff, "limit": limit},
    )
