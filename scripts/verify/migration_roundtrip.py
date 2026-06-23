from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg2
from psycopg2 import sql
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL, make_url

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))
ALEMBIC_BIN = Path(os.environ.get("ALEMBIC_BIN", ROOT_DIR / ".venv/bin/alembic"))

from app.core.config import settings

REMOVED_JOB_COLUMNS = {
    "cancel_reason",
    "cancel_requested_at",
    "cancel_requested_by",
    "dispatch_attempts",
    "execution_plan",
    "execution_published_at",
    "first_published_at",
    "idempotency_key",
    "last_published_at",
    "request_fingerprint",
}

HEAD_JOB_KERNEL_TABLES = {
    "job_submission_keys",
    "job_aggregates",
    "job_execution_attempts",
    "dispatch_outbox",
    "callback_outbox",
    "job_audit_events",
    "ai_call_ledger_entries",
}


def _require_local(url: URL) -> None:
    if url.host not in {"127.0.0.1", "localhost", "0.0.0.0", "postgres"}:
        raise SystemExit(f"refuse to run migration roundtrip against non-local database host: {url.host}")


def _sync_url(url: URL) -> str:
    return url.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False)


def _async_url(url: URL) -> str:
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def _connect(url: URL):
    return psycopg2.connect(
        dbname=url.database,
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
    )


def _create_database(admin_url: URL, database_name: str) -> None:
    connection = _connect(admin_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    finally:
        connection.close()


def _drop_database(admin_url: URL, database_name: str) -> None:
    connection = _connect(admin_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))
    finally:
        connection.close()


def _run_alembic(command: str, revision: str, *, target_url: URL) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = _async_url(target_url)
    env["SYNC_DATABASE_URL"] = _sync_url(target_url)
    env["PYTHONPATH"] = f"{ROOT_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}"
    result = subprocess.run(
        [str(ALEMBIC_BIN), command, revision],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        raise SystemExit(result.returncode)


def _schema_state(target_url: URL) -> tuple[set[str], dict[str, set[str]], dict[str, set[str]]]:
    engine = create_engine(_sync_url(target_url), pool_pre_ping=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        columns = {table: {column["name"] for column in inspector.get_columns(table)} for table in tables}
        checks = {
            table: {constraint["name"] for constraint in inspector.get_check_constraints(table)}
            for table in tables
        }
        return tables, columns, checks
    finally:
        engine.dispose()


def _assert_head_schema(target_url: URL) -> None:
    tables, columns, checks = _schema_state(target_url)
    if "reconciler_leases" in tables:
        raise AssertionError("head schema must not include reconciler_leases")
    if {"ai_jobs", "ai_job_work_items", "jobs", "job_attempts", "job_events", "ai_call_logs"} & tables:
        raise AssertionError("head schema must not include legacy ai_jobs tables")
    missing_tables = HEAD_JOB_KERNEL_TABLES - tables
    if missing_tables:
        raise AssertionError(f"head schema missing transactional outbox job kernel tables: {sorted(missing_tables)}")
    if REMOVED_JOB_COLUMNS & columns["job_aggregates"]:
        raise AssertionError(
            f"head schema still includes removed job columns: {sorted(REMOVED_JOB_COLUMNS & columns['job_aggregates'])}"
        )
    if {"published_at", "dispatch_attempts", "next_dispatch_at", "last_dispatch_error"} & columns[
        "job_execution_attempts"
    ]:
        raise AssertionError("job_execution_attempts must not include dispatch ledger columns")
    required_dispatch_columns = {"event_id", "attempt_id", "status", "publish_attempts", "next_attempt_at", "lease_token"}
    if not required_dispatch_columns.issubset(columns["dispatch_outbox"]):
        missing = sorted(required_dispatch_columns - columns["dispatch_outbox"])
        raise AssertionError(f"dispatch_outbox missing required columns: {missing}")
    if "ck_job_aggregates_status" not in checks["job_aggregates"]:
        raise AssertionError("head schema missing ck_job_aggregates_status")
    if "ck_job_execution_attempts_status" not in checks["job_execution_attempts"]:
        raise AssertionError("head schema missing ck_job_execution_attempts_status")


def _assert_0012_schema(target_url: URL) -> None:
    tables, columns, checks = _schema_state(target_url)
    if "reconciler_leases" not in tables:
        raise AssertionError("0012 schema must include reconciler_leases after downgrade")
    if not REMOVED_JOB_COLUMNS.issubset(columns["jobs"]):
        missing = sorted(REMOVED_JOB_COLUMNS - columns["jobs"])
        raise AssertionError(f"0012 schema did not restore old job columns: {missing}")
    if "ck_ai_jobs_status" not in checks["jobs"]:
        raise AssertionError("0012 schema missing legacy ck_ai_jobs_status after downgrade")


def main() -> None:
    base_url = make_url(settings.database.sync_url)
    _require_local(base_url)
    database_name = f"{base_url.database}_migration_rt_{uuid.uuid4().hex[:8]}"
    admin_url = base_url.set(database="postgres", drivername="postgresql")
    target_url = base_url.set(database=database_name)

    _create_database(admin_url, database_name)
    try:
        _run_alembic("upgrade", "head", target_url=target_url)
        _assert_head_schema(target_url)
        print("OK        upgrade    head")

        _run_alembic("downgrade", "0012_add_ai_call_logs", target_url=target_url)
        _assert_0012_schema(target_url)
        print("OK        downgrade  0012_add_ai_call_logs")

        _run_alembic("upgrade", "head", target_url=target_url)
        _assert_head_schema(target_url)
        print("OK        reupgrade  head")
    finally:
        _drop_database(admin_url, database_name)


if __name__ == "__main__":
    main()
