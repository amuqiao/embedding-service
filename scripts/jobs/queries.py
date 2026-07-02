from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg2.extensions import connection

VALID_RECORD_SCOPES = {"root", "child", "all", "family"}


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


def _lineage_scope_clause(table_alias: str, record_scope: str) -> str:
    if record_scope == "all":
        return ""
    if record_scope == "root":
        return f"""
AND {table_alias}.root_job_id IS NULL
AND {table_alias}.workflow_node_key IS NULL
AND {table_alias}.client_request_id IS NOT NULL
"""
    if record_scope == "child":
        return f"""
AND {table_alias}.root_job_id IS NOT NULL
AND {table_alias}.workflow_node_key IS NOT NULL
AND {table_alias}.client_request_id IS NULL
"""
    if record_scope == "family":
        raise ValueError("family scope requires root seed filters")
    raise ValueError("invalid record_scope: " + record_scope)


def _family_scope_clause(
    *,
    table_alias: str,
    job_type: str | None,
    caller_id: str | None,
    client_request_id: str | None,
    since: datetime | None,
) -> tuple[str, dict[str, Any]]:
    clauses = [
        "root.deleted_at IS NULL",
        "root.root_job_id IS NULL",
        "root.workflow_node_key IS NULL",
        "root.client_request_id IS NOT NULL",
        f"({table_alias}.id = root.id OR {table_alias}.root_job_id = root.id)",
    ]
    params: dict[str, Any] = {}
    if job_type is not None:
        clauses.append("root.job_type = %(job_type)s")
        params["job_type"] = job_type
    if caller_id is not None:
        clauses.append("root.caller_id = %(caller_id)s")
        params["caller_id"] = caller_id
    if client_request_id is not None:
        clauses.append("root.client_request_id = %(client_request_id)s")
        params["client_request_id"] = client_request_id
    if since is not None:
        clauses.append("root.created_at >= %(since)s")
        params["since"] = since
    return "AND EXISTS (\n  SELECT 1\n  FROM job_aggregates root\n  WHERE " + "\n    AND ".join(clauses) + "\n)", params


def _common_filters(
    *,
    table_alias: str,
    statuses: list[str],
    job_type: str | None,
    caller_id: str | None,
    client_request_id: str | None,
    since: datetime | None,
    record_scope: str = "all",
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    status_sql, status_params = _status_clause(statuses, table_alias)
    if status_sql:
        clauses.append(status_sql)
        params.update(status_params)
    if record_scope == "family":
        family_sql, family_params = _family_scope_clause(
            table_alias=table_alias,
            job_type=job_type,
            caller_id=caller_id,
            client_request_id=client_request_id,
            since=since,
        )
        clauses.append(family_sql)
        params.update(family_params)
        return "\n".join(clauses), params

    lineage_sql = _lineage_scope_clause(table_alias, record_scope)
    if lineage_sql:
        clauses.append(lineage_sql)
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


def _scope_filters(
    *,
    table_alias: str,
    job_type: str | None,
    caller_id: str | None,
    since: datetime | None = None,
    record_scope: str = "all",
) -> tuple[str, dict[str, Any]]:
    return _common_filters(
        table_alias=table_alias,
        statuses=[],
        job_type=job_type,
        caller_id=caller_id,
        client_request_id=None,
        since=since,
        record_scope=record_scope,
    )


def list_jobs(
    conn: connection,
    *,
    statuses: list[str],
    job_type: str | None,
    caller_id: str | None,
    client_request_id: str | None,
    since: datetime | None,
    limit: int,
    record_scope: str = "root",
) -> list[dict]:
    filters, params = _common_filters(
        table_alias="j",
        statuses=statuses,
        job_type=job_type,
        caller_id=caller_id,
        client_request_id=client_request_id,
        since=since,
        record_scope=record_scope,
    )
    params["limit"] = limit
    return _fetch_all(
        conn,
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
        LEFT JOIN LATERAL (
          SELECT c.status, c.last_error
          FROM callback_outbox c
          WHERE c.job_id = j.id
          ORDER BY c.created_at DESC
          LIMIT 1
        ) cb ON TRUE
        WHERE j.deleted_at IS NULL
        {filters}
        ORDER BY j.created_at DESC
        LIMIT %(limit)s
        """,
        params,
    )


def _deleted_common_filters(
    *,
    table_alias: str,
    job_type: str | None,
    caller_id: str | None,
    client_request_id: str | None = None,
    deleted_since: datetime | None = None,
    record_scope: str = "root",
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if record_scope == "family":
        family_clauses = [
            "root.deleted_at IS NOT NULL",
            "root.root_job_id IS NULL",
            "root.workflow_node_key IS NULL",
            "root.client_request_id IS NOT NULL",
            f"({table_alias}.id = root.id OR {table_alias}.root_job_id = root.id)",
        ]
        if job_type is not None:
            family_clauses.append("root.job_type = %(job_type)s")
            params["job_type"] = job_type
        if caller_id is not None:
            family_clauses.append("root.caller_id = %(caller_id)s")
            params["caller_id"] = caller_id
        if client_request_id is not None:
            family_clauses.append("root.client_request_id = %(client_request_id)s")
            params["client_request_id"] = client_request_id
        if deleted_since is not None:
            family_clauses.append("root.deleted_at >= %(deleted_since)s")
            params["deleted_since"] = deleted_since
        clauses.append(
            "AND EXISTS (\n  SELECT 1\n  FROM job_aggregates root\n  WHERE "
            + "\n    AND ".join(family_clauses)
            + "\n)"
        )
        return "\n".join(clauses), params

    lineage_sql = _lineage_scope_clause(table_alias, record_scope)
    if lineage_sql:
        clauses.append(lineage_sql)
    if job_type is not None:
        clauses.append(f"AND {table_alias}.job_type = %(job_type)s")
        params["job_type"] = job_type
    if caller_id is not None:
        clauses.append(f"AND {table_alias}.caller_id = %(caller_id)s")
        params["caller_id"] = caller_id
    if client_request_id is not None:
        clauses.append(f"AND {table_alias}.client_request_id = %(client_request_id)s")
        params["client_request_id"] = client_request_id
    if deleted_since is not None:
        clauses.append(f"AND {table_alias}.deleted_at >= %(deleted_since)s")
        params["deleted_since"] = deleted_since
    return "\n".join(clauses), params


def deleted_jobs(
    conn: connection,
    *,
    job_type: str | None,
    caller_id: str | None,
    client_request_id: str | None,
    deleted_since: datetime | None,
    limit: int,
    record_scope: str = "root",
) -> list[dict]:
    filters, params = _deleted_common_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        client_request_id=client_request_id,
        deleted_since=deleted_since,
        record_scope=record_scope,
    )
    params["limit"] = limit
    return _fetch_all(
        conn,
        f"""
        SELECT
          j.id::text AS job_id,
          CASE WHEN j.root_job_id IS NULL THEN 'root' ELSE 'child' END AS record_scope,
          COALESCE(j.root_job_id, j.id)::text AS family_root_job_id,
          j.root_job_id::text AS root_job_id,
          j.workflow_node_key,
          j.status,
          j.job_type,
          j.caller_id,
          j.client_request_id,
          j.progress_percent,
          j.progress_stage,
          j.created_at,
          j.finished_at,
          j.expires_at,
          j.delete_requested_at,
          j.deleted_at,
          j.deleted_reason
        FROM job_aggregates j
        WHERE j.deleted_at IS NOT NULL
        {filters}
        ORDER BY j.deleted_at DESC, j.created_at DESC
        LIMIT %(limit)s
        """,
        params,
    )


def deleted_summary(
    conn: connection,
    *,
    job_type: str | None,
    caller_id: str | None,
    deleted_since: datetime | None,
    record_scope: str = "all",
) -> dict[str, Any]:
    filters, params = _deleted_common_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        deleted_since=deleted_since,
        record_scope=record_scope,
    )
    counts = _fetch_one(
        conn,
        f"""
        SELECT
          count(*) AS total_deleted,
          count(*) FILTER (WHERE j.root_job_id IS NULL) AS root_deleted,
          count(*) FILTER (WHERE j.root_job_id IS NOT NULL) AS child_deleted,
          count(DISTINCT COALESCE(j.root_job_id, j.id)) AS family_count,
          min(j.deleted_at) AS oldest_deleted_at,
          max(j.deleted_at) AS newest_deleted_at
        FROM job_aggregates j
        WHERE j.deleted_at IS NOT NULL
        {filters}
        """,
        params,
    ) or {}
    by_reason = _fetch_all(
        conn,
        f"""
        SELECT COALESCE(j.deleted_reason, '-') AS deleted_reason, count(*) AS count
        FROM job_aggregates j
        WHERE j.deleted_at IS NOT NULL
        {filters}
        GROUP BY 1
        ORDER BY count DESC, deleted_reason ASC
        """,
        params,
    )
    by_status = _fetch_all(
        conn,
        f"""
        SELECT j.status, count(*) AS count
        FROM job_aggregates j
        WHERE j.deleted_at IS NOT NULL
        {filters}
        GROUP BY j.status
        ORDER BY count DESC, j.status ASC
        """,
        params,
    )
    by_job_type = _fetch_all(
        conn,
        f"""
        SELECT j.job_type, count(*) AS count
        FROM job_aggregates j
        WHERE j.deleted_at IS NOT NULL
        {filters}
        GROUP BY j.job_type
        ORDER BY count DESC, j.job_type ASC
        """,
        params,
    )
    key_counts = _fetch_one(
        conn,
        f"""
        SELECT
          count(*) AS total_deleted,
          count(*) FILTER (WHERE sk.deleted_reason = 'expired') AS expired_deleted
        FROM job_submission_keys sk
        JOIN job_aggregates j ON j.id = sk.job_id
        WHERE sk.deleted_at IS NOT NULL
        {filters}
        """,
        params,
    ) or {}
    consistency_clauses = [
        "j.root_job_id IS NULL",
        "j.workflow_node_key IS NULL",
        "j.client_request_id IS NOT NULL",
    ]
    consistency_params: dict[str, Any] = {}
    if job_type is not None:
        consistency_clauses.append("j.job_type = %(job_type)s")
        consistency_params["job_type"] = job_type
    if caller_id is not None:
        consistency_clauses.append("j.caller_id = %(caller_id)s")
        consistency_params["caller_id"] = caller_id
    if deleted_since is not None:
        consistency_clauses.append("(j.deleted_at >= %(deleted_since)s OR sk.deleted_at >= %(deleted_since)s)")
        consistency_params["deleted_since"] = deleted_since
    child_consistency_clauses = [
        "child.root_job_id = root.id",
        "child.deleted_at IS NOT NULL",
        "(child.status NOT IN ('succeeded', 'failed') OR child.active_attempt_id IS NOT NULL)",
    ]
    if job_type is not None:
        child_consistency_clauses.append("root.job_type = %(job_type)s")
    if caller_id is not None:
        child_consistency_clauses.append("root.caller_id = %(caller_id)s")
    if deleted_since is not None:
        child_consistency_clauses.append("(root.deleted_at >= %(deleted_since)s OR child.deleted_at >= %(deleted_since)s)")
    inconsistencies = _fetch_one(
        conn,
        f"""
        SELECT
          count(*) FILTER (
            WHERE j.deleted_at IS NOT NULL
              AND sk.id IS NOT NULL
              AND sk.deleted_at IS NULL
          ) AS deleted_root_active_submission_keys,
          count(*) FILTER (
            WHERE j.deleted_at IS NULL
              AND sk.id IS NOT NULL
              AND sk.deleted_at IS NOT NULL
          ) AS active_root_deleted_submission_keys,
          count(*) FILTER (
            WHERE j.deleted_at IS NOT NULL
              AND (j.status NOT IN ('succeeded', 'failed') OR j.active_attempt_id IS NOT NULL)
          ) AS deleted_active_jobs,
          (
            SELECT count(*)
            FROM job_aggregates child
            JOIN job_aggregates root ON root.id = child.root_job_id
            WHERE {" AND ".join(child_consistency_clauses)}
          ) AS deleted_child_active_jobs
        FROM job_aggregates j
        LEFT JOIN job_submission_keys sk ON sk.job_id = j.id
        WHERE {" AND ".join(consistency_clauses)}
        """,
        consistency_params,
    ) or {}
    return {
        "counts": counts,
        "by_reason": by_reason,
        "by_status": by_status,
        "by_job_type": by_job_type,
        "submission_keys": key_counts,
        "inconsistencies": inconsistencies,
    }


def get_deleted_job(conn: connection, job_id: str) -> dict | None:
    return _fetch_one(
        conn,
        """
        SELECT
          j.*,
          COALESCE(j.root_job_id, j.id)::text AS family_root_job_id,
          CASE WHEN j.root_job_id IS NULL THEN 'root' ELSE 'child' END AS record_scope,
          COALESCE(attempt_counts.total, 0) AS attempt_count,
          COALESCE(callback_counts.total, 0) AS callback_count,
          COALESCE(child_counts.total, 0) AS child_count,
          COALESCE(child_counts.deleted, 0) AS deleted_child_count
        FROM job_aggregates j
        LEFT JOIN LATERAL (
          SELECT count(*) AS total
          FROM job_execution_attempts a
          WHERE a.job_id = j.id
        ) attempt_counts ON TRUE
        LEFT JOIN LATERAL (
          SELECT count(*) AS total
          FROM callback_outbox c
          WHERE c.job_id = j.id
        ) callback_counts ON TRUE
        LEFT JOIN LATERAL (
          SELECT
            count(*) AS total,
            count(*) FILTER (WHERE child.deleted_at IS NOT NULL) AS deleted
          FROM job_aggregates child
          WHERE child.root_job_id = j.id
        ) child_counts ON TRUE
        WHERE j.id = %(job_id)s
          AND j.deleted_at IS NOT NULL
        """,
        {"job_id": job_id},
    )


def get_job(conn: connection, job_id: str) -> dict | None:
    return _fetch_one(
        conn,
        """
        SELECT
          j.*,
          CASE
            WHEN j.callback_url IS NULL THEN 'not_configured'
            WHEN cb.status IS NULL THEN 'pending'
            WHEN cb.status = 'leased' THEN 'delivering'
            WHEN cb.status = 'dead_letter' THEN 'failed'
            WHEN cb.status = 'skipped' AND cb.last_error IS NOT NULL THEN 'failed'
            WHEN cb.status = 'skipped' THEN 'not_configured'
            ELSE cb.status
          END AS callback_status,
          COALESCE(cb.delivery_attempts, 0) AS callback_attempts,
          cb.next_attempt_at AS callback_next_retry_at,
          cb.last_error AS callback_last_error
        FROM job_aggregates j
        LEFT JOIN LATERAL (
          SELECT c.status, c.delivery_attempts, c.next_attempt_at, c.last_error, c.created_at
          FROM callback_outbox c
          WHERE c.job_id = j.id
          ORDER BY c.created_at DESC
          LIMIT 1
        ) cb ON TRUE
        WHERE j.id = %(job_id)s
          AND j.deleted_at IS NULL
        """,
        {"job_id": job_id},
    )


def child_jobs(conn: connection, root_job_id: str) -> list[dict]:
    return _fetch_all(
        conn,
        """
        SELECT
          j.workflow_node_key,
          j.id::text AS job_id,
          j.root_job_id::text AS root_job_id,
          j.status,
          j.job_type,
          j.caller_id,
          j.client_request_id,
          j.progress_percent,
          j.progress_stage,
          j.progress_text,
          j.active_attempt_id::text AS active_attempt_id,
          a.status AS attempt_status,
          a.purpose_attempt_no AS attempt_no,
          a.worker_id,
          a.lease_expires_at,
          d.status AS dispatch_status,
          d.publish_attempts,
          d.next_attempt_at AS dispatch_next_attempt_at,
          j.metadata,
          j.job_params_ref,
          j.job_params_hash,
          j.runtime_ref,
          j.error,
          j.result,
          j.canonical_result,
          j.created_at,
          j.started_at,
          j.updated_at,
          j.finished_at,
          CASE
            WHEN j.started_at IS NULL THEN NULL
            WHEN j.finished_at IS NULL THEN now() - j.started_at
            ELSE j.finished_at - j.started_at
          END AS duration
        FROM job_aggregates j
        LEFT JOIN job_execution_attempts a ON a.id = j.active_attempt_id
        LEFT JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
        WHERE j.deleted_at IS NULL
          AND j.root_job_id = %(root_job_id)s
          AND j.workflow_node_key IS NOT NULL
        ORDER BY j.created_at ASC
        """,
        {"root_job_id": root_job_id},
    )


def summary(
    conn: connection,
    *,
    job_type: str | None,
    caller_id: str | None,
    since: datetime | None,
    record_scope: str = "root",
    execution_scope: str = "family",
) -> dict[str, Any]:
    filters, params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope=record_scope,
    )
    execution_filters, execution_params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope=execution_scope,
    )
    callback_filters, callback_params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope="root",
    )
    job_counts = _fetch_one(
        conn,
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
        {filters}
        """,
        params,
    ) or {}
    by_job_type = _fetch_all(
        conn,
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
        {filters}
        GROUP BY j.job_type
        ORDER BY active_jobs DESC, total DESC, j.job_type ASC
        """,
        params,
    )
    attempts = _fetch_one(
        conn,
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
        {execution_filters}
        """,
        execution_params,
    ) or {}
    dispatch = _fetch_one(
        conn,
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
        {execution_filters}
        """,
        execution_params,
    ) or {}
    callbacks_row = _fetch_one(
        conn,
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
            WHERE c.status IN ('pending', 'failed')
              AND (c.next_attempt_at IS NULL OR c.next_attempt_at <= now())
          ) AS due
        FROM callback_outbox c
        JOIN job_aggregates j ON j.id = c.job_id
        WHERE j.deleted_at IS NULL
        {callback_filters}
        """,
        callback_params,
    ) or {}
    return {
        "query_scopes": {
            "jobs": record_scope,
            "by_job_type": record_scope,
            "attempts": execution_scope,
            "dispatch": execution_scope,
            "callbacks": "root",
        },
        "jobs": job_counts,
        "by_job_type": by_job_type,
        "attempts": attempts,
        "dispatch": dispatch,
        "callbacks": callbacks_row,
    }


def latency(
    conn: connection,
    *,
    job_type: str | None,
    caller_id: str | None,
    since: datetime | None,
    group_by: str,
    record_scope: str = "root",
) -> list[dict]:
    filters, params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope=record_scope,
    )
    group_expr = {
        "job_type": "j.job_type",
        "caller_id": "j.caller_id",
        "status": "j.status",
        "all": "'all'",
    }[group_by]
    return _fetch_all(
        conn,
        f"""
        SELECT
          {group_expr} AS group_key,
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
          avg(EXTRACT(EPOCH FROM (j.started_at - COALESCE(j.queued_at, j.created_at))))
            FILTER (WHERE j.started_at IS NOT NULL) AS queue_wait_avg_seconds,
          percentile_cont(0.95) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (j.started_at - COALESCE(j.queued_at, j.created_at)))
          ) FILTER (WHERE j.started_at IS NOT NULL) AS queue_wait_p95_seconds,
          avg(EXTRACT(EPOCH FROM (j.finished_at - j.started_at)))
            FILTER (WHERE j.started_at IS NOT NULL AND j.finished_at IS NOT NULL) AS run_avg_seconds,
          percentile_cont(0.95) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (j.finished_at - j.started_at))
          ) FILTER (WHERE j.started_at IS NOT NULL AND j.finished_at IS NOT NULL) AS run_p95_seconds,
          avg(EXTRACT(EPOCH FROM (j.finished_at - j.created_at)))
            FILTER (WHERE j.finished_at IS NOT NULL) AS lifecycle_avg_seconds,
          percentile_cont(0.95) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (j.finished_at - j.created_at))
          ) FILTER (WHERE j.finished_at IS NOT NULL) AS lifecycle_p95_seconds
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
        {filters}
        GROUP BY 1
        ORDER BY lifecycle_p95_seconds DESC NULLS LAST, total DESC
        """,
        params,
    )


def ingress(
    conn: connection,
    *,
    job_type: str | None,
    caller_id: str | None,
    since: datetime,
    bucket_seconds: int,
    record_scope: str = "root",
) -> list[dict]:
    filters, params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=None,
        record_scope=record_scope,
    )
    params["since"] = since
    params["bucket_seconds"] = bucket_seconds
    return _fetch_all(
        conn,
        f"""
        WITH events AS (
          SELECT j.created_at AS event_at, 'created' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.created_at >= %(since)s
            {filters}
          UNION ALL
          SELECT j.started_at AS event_at, 'started' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.started_at IS NOT NULL
            AND j.started_at >= %(since)s
            {filters}
          UNION ALL
          SELECT j.finished_at AS event_at, 'terminal' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.finished_at IS NOT NULL
            AND j.finished_at >= %(since)s
            {filters}
          UNION ALL
          SELECT j.finished_at AS event_at, 'failed' AS metric
          FROM job_aggregates j
          WHERE j.deleted_at IS NULL
            AND j.status = 'failed'
            AND j.finished_at IS NOT NULL
            AND j.finished_at >= %(since)s
            {filters}
        )
        SELECT
          to_timestamp(
            floor(EXTRACT(EPOCH FROM event_at) / %(bucket_seconds)s) * %(bucket_seconds)s
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


def failure_groups(
    conn: connection,
    *,
    job_type: str | None,
    caller_id: str | None,
    since: datetime | None,
    limit: int,
    record_scope: str = "family",
) -> list[dict]:
    filters, params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope=record_scope,
    )
    params["limit"] = limit
    return _fetch_all(
        conn,
        f"""
        SELECT
          COALESCE(j.error->>'code', '-') AS error_code,
          COALESCE(a.error_kind, '-') AS error_kind,
          COALESCE(a.failure_phase, '-') AS failure_phase,
          COALESCE(j.error->'details'->>'type', '-') AS detail_type,
          COALESCE(j.error->'details'->>'message', j.error->>'message', '-') AS detail_message,
          count(*) AS count,
          max(j.updated_at) AS newest_updated_at
        FROM job_aggregates j
        LEFT JOIN job_execution_attempts a ON a.id = j.active_attempt_id
        WHERE j.deleted_at IS NULL
          AND j.status = 'failed'
          {filters}
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY count DESC, newest_updated_at DESC
        LIMIT %(limit)s
        """,
        params,
    )


def callbacks_summary(
    conn: connection,
    *,
    job_type: str | None,
    caller_id: str | None,
    since: datetime | None,
    record_scope: str = "root",
) -> list[dict]:
    filters, params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope=record_scope,
    )
    return _fetch_all(
        conn,
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
          max(c.last_error::text) FILTER (WHERE c.last_error IS NOT NULL) AS sample_last_error,
          EXTRACT(EPOCH FROM (now() - min(c.created_at))) AS oldest_age_seconds
        FROM callback_outbox c
        JOIN job_aggregates j ON j.id = c.job_id
        WHERE j.deleted_at IS NULL
        {filters}
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
        params,
    )


def global_gate(conn: connection) -> dict[str, Any]:
    return _fetch_one(
        conn,
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
    ) or {}


def capacity(
    conn: connection,
    *,
    job_type: str | None,
    caller_id: str | None,
    since: datetime,
    window_seconds: float,
    window_scope: str = "root",
) -> dict[str, Any]:
    window_filters, window_params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope=window_scope,
    )
    active = global_gate(conn)
    window = _fetch_one(
        conn,
        f"""
        SELECT
          count(*) AS accepted_jobs,
          count(*) FILTER (WHERE j.finished_at IS NOT NULL) AS terminal_jobs,
          min(j.created_at) AS first_created_at,
          max(j.created_at) AS newest_created_at,
          percentile_cont(0.95) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (j.finished_at - j.created_at))
          ) FILTER (WHERE j.finished_at IS NOT NULL) AS lifecycle_p95_seconds
        FROM job_aggregates j
        WHERE j.deleted_at IS NULL
        {window_filters}
        """,
        window_params,
    ) or {}
    accepted_jobs = int(window.get("accepted_jobs") or 0)
    lifecycle_p95_seconds = window.get("lifecycle_p95_seconds")
    first_created_at = window.get("first_created_at")
    newest_created_at = window.get("newest_created_at")
    observed_span_seconds = (
        (newest_created_at - first_created_at).total_seconds()
        if first_created_at is not None and newest_created_at is not None and newest_created_at > first_created_at
        else None
    )
    effective_window_seconds = observed_span_seconds if observed_span_seconds and observed_span_seconds > 0 else window_seconds
    accepted_submit_rps = accepted_jobs / effective_window_seconds if effective_window_seconds > 0 else None
    active_jobs_needed_upper_bound = (
        accepted_submit_rps * float(lifecycle_p95_seconds)
        if accepted_submit_rps is not None and lifecycle_p95_seconds is not None
        else None
    )
    return {
        "current": active,
        "query_scopes": {"current": "global_gate", "window": window_scope},
        "window": window
        | {
            "window_seconds": window_seconds,
            "observed_span_seconds": observed_span_seconds,
            "effective_window_seconds": effective_window_seconds,
            "accepted_submit_rps": accepted_submit_rps,
        },
        "estimated": {"active_jobs_needed_upper_bound": active_jobs_needed_upper_bound},
    }


def attempts(conn: connection, job_id: str) -> list[dict]:
    return _fetch_all(
        conn,
        """
        SELECT a.id::text, a.job_id::text, a.purpose, a.purpose_attempt_no, a.purpose_attempt_no AS attempt_no,
               a.status,
               d.status AS dispatch_status, d.published_at,
               d.publish_attempts, d.next_attempt_at, d.last_error AS dispatch_last_error,
               a.worker_id, a.lease_token::text, a.leased_at, a.lease_expires_at,
               a.heartbeat_at, a.started_at, a.finished_at, a.timeout_seconds,
               a.error, a.error_kind, a.failure_phase, a.retry_eligible, a.retry_decision,
               a.next_attempt_scheduled_at,
               a.created_at, a.updated_at
        FROM job_execution_attempts a
        JOIN job_aggregates j ON j.id = a.job_id
        LEFT JOIN dispatch_outbox d ON d.attempt_id = a.id AND d.task_name = 'jobs.run_attempt'
        WHERE a.job_id = %(job_id)s
          AND j.deleted_at IS NULL
        ORDER BY a.purpose ASC, a.purpose_attempt_no ASC, a.created_at ASC
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
               c.last_response, c.last_error, c.first_attempt_at, c.last_attempt_at,
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
        SELECT *
        FROM (
          SELECT e.id::text, e.job_id::text, e.attempt_id::text, e.callback_id::text,
                 e.event_type, e.from_status, e.to_status, e.reason, e.payload, e.created_at
          FROM job_audit_events e
          JOIN job_aggregates j ON j.id = e.job_id
          WHERE e.job_id = %(job_id)s
            AND j.deleted_at IS NULL
          ORDER BY e.created_at DESC
          LIMIT %(limit)s
        ) recent_events
        ORDER BY created_at ASC
        """,
        {"job_id": job_id, "limit": limit},
    )


def stuck(
    conn: connection,
    *,
    older_than: timedelta,
    limit: int,
    job_type: str | None = None,
    caller_id: str | None = None,
    since: datetime | None = None,
    record_scope: str = "family",
) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - older_than
    scope_filters, scope_params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope=record_scope,
    )
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
            {scope_filters}
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
            {scope_filters}
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
            AND a.lease_expires_at < %(cutoff)s
            {scope_filters}
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
            AND c.lease_expires_at < %(cutoff)s
            {scope_filters}
        )
        UNION ALL
        (
          SELECT 'terminal_callback_not_settled' AS issue, j.id::text AS job_id, j.status AS job_status, j.job_type,
                 c.id::text AS related_id, c.status AS related_status,
                 j.finished_at AS since_at, c.next_attempt_at,
                 c.last_error AS detail
          FROM job_aggregates j
          JOIN callback_outbox c ON c.job_id = j.id
          WHERE j.deleted_at IS NULL
            AND j.status IN ('succeeded', 'failed')
            AND c.status IN ('pending', 'leased', 'retrying')
            AND j.finished_at < %(cutoff)s
            {scope_filters}
        )
        ORDER BY since_at ASC NULLS LAST
        LIMIT %(limit)s
        """.format(scope_filters=scope_filters),
        {"cutoff": cutoff, "limit": limit, **scope_params},
    )


def drain_status(
    conn: connection,
    *,
    job_type: str | None,
    caller_id: str | None,
    since: datetime,
    older_than: timedelta,
    record_scope: str = "family",
) -> dict[str, Any]:
    current_filters, current_params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        record_scope=record_scope,
    )
    window_filters, window_params = _scope_filters(
        table_alias="j",
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope=record_scope,
    )
    current = _fetch_one(
        conn,
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
        {current_filters}
        """,
        current_params,
    ) or {}
    window = _fetch_one(
        conn,
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
        {window_filters}
        """,
        window_params,
    ) or {}
    stuck_rows = stuck(
        conn,
        older_than=older_than,
        limit=1000,
        job_type=job_type,
        caller_id=caller_id,
        since=since,
        record_scope=record_scope,
    )
    return {
        "query_scopes": {"current": record_scope, "window": record_scope, "stuck": record_scope},
        "current": current,
        "window": window,
        "stuck": {
            "total": len(stuck_rows),
            "sample": stuck_rows[:20],
            "truncated": len(stuck_rows) >= 1000,
        },
    }
