from __future__ import annotations

import contextlib
import io
import re
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import typer

from scripts.jobs import db, formatters, queries


VALID_JOB_STATUSES = {"queued", "running", "succeeded", "failed"}
DURATION_RE = re.compile(r"^(?P<value>[1-9][0-9]*)(?P<unit>s|m|h|d)$")

HELP_EPILOG = """\b
作用域：
  在本地或 Pod 内查询 Job、attempt、callback 和 timeline 证据。
  本入口只执行只读查询，不提供创建、取消、重试、补偿或 callback 重放能力。

\b
命令说明：
  list       查看最近 Job 摘要，支持状态、类型、调用方、时间窗口和 limit 过滤。
  show       查看单个 Job 权威状态。
  inspect    聚合查看单个 Job、attempt、callback 和最近 timeline。
  timeline   查看 lifecycle job events。
  attempts   查看 lifecycle attempts。
  callbacks  查看 lifecycle callback outbox。
  stuck      扫描疑似卡住的 Job、attempt 或 callback lease。
  types      查看当前注册的 job_type。

\b
环境变量：
  DATABASE_URL    DB 查询必填；可通过运行环境或根目录 .env 注入。
  DB_SSL          可选；false/0/no/off 时为 psycopg2 URL 追加 sslmode=disable。

\b
输出：
  默认输出面向人读，使用 section/event/table。
  --json 输出完整 JSON，且 stdout 只包含 JSON，适合 AI、CI 或运维平台解析。
  错误原因输出到 stderr。

\b
常用示例：
  ./scripts/jobs.sh list --status running --since 24h --limit 20
  ./scripts/jobs.sh show <job_id>
  ./scripts/jobs.sh inspect <job_id>
  ./scripts/jobs.sh timeline <job_id> --limit 50
  ./scripts/jobs.sh stuck --older-than 10m --json
  ./scripts/jobs.sh types --json

\b
保护边界：
  只读查询不修改 DB，不触发真实业务调用，不投递消息，不重试 Job，不重放 callback。
  单个 job_id 不存在时返回非 0，不把空结果解释为成功。

\b
Exit Codes:
  0  成功
  2  参数非法或 DB 不可达
  3  查询对象不存在
  4  查询失败或证据不可达
"""

app = typer.Typer(
    name="jobs.sh",
    help="Job 只读查询与排障入口。",
    epilog=HELP_EPILOG,
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)


JsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="输出 JSON；默认输出人读表格。",
        rich_help_panel="Output",
    ),
]
JobIdArgument = Annotated[str, typer.Argument(help="Job ID。")]
LimitOption = Annotated[
    int,
    typer.Option(
        "--limit",
        min=1,
        max=1000,
        help="返回条数，范围 1 到 1000。",
    ),
]


def parse_duration(value: str) -> timedelta:
    match = DURATION_RE.fullmatch(value)
    if not match:
        raise ValueError("时间窗口格式必须类似 30s、10m、24h 或 7d")
    amount = int(match.group("value"))
    unit = match.group("unit")
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def parse_statuses(values: list[str] | None) -> list[str]:
    if not values:
        return []
    statuses: list[str] = []
    for value in values:
        statuses.extend(item.strip() for item in value.split(",") if item.strip())
    invalid = sorted({status for status in statuses if status not in VALID_JOB_STATUSES})
    if invalid:
        raise ValueError("无效 status：" + ", ".join(invalid))

    result: list[str] = []
    seen: set[str] = set()
    for status in statuses:
        if status not in seen:
            result.append(status)
            seen.add(status)
    return result


def parse_optional_duration(value: str | None) -> timedelta | None:
    if value is None:
        return None
    try:
        return parse_duration(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _jobs_columns() -> list[tuple[str, str]]:
    return [
        ("job_id", "job_id"),
        ("status", "status"),
        ("job_type", "job_type"),
        ("caller_id", "caller"),
        ("client_request_id", "client_request_id"),
        ("progress_percent", "%"),
        ("progress_stage", "stage"),
        ("callback_status", "callback"),
        ("attempt_status", "attempt"),
        ("dispatch_status", "dispatch"),
        ("publish_attempts", "publish_attempts"),
        ("lease_expires_at", "lease_expires_at"),
        ("created_at", "created_at"),
        ("age", "age"),
        ("duration", "duration"),
    ]


def _attempt_columns() -> list[tuple[str, str]]:
    return [
        ("id", "attempt_id"),
        ("attempt_no", "no"),
        ("status", "status"),
        ("dispatch_status", "dispatch"),
        ("publish_attempts", "publish_attempts"),
        ("published_at", "published_at"),
        ("worker_id", "worker"),
        ("heartbeat_at", "heartbeat_at"),
        ("lease_expires_at", "lease_expires_at"),
        ("failure_phase", "failure_phase"),
    ]


def _callback_columns() -> list[tuple[str, str]]:
    return [
        ("id", "callback_id"),
        ("event_type", "event"),
        ("status", "status"),
        ("delivery_attempts", "attempts"),
        ("next_attempt_at", "next_attempt_at"),
        ("lease_expires_at", "lease_expires_at"),
        ("last_http_status", "http"),
        ("last_error", "last_error"),
        ("created_at", "created_at"),
    ]


def _timeline_columns() -> list[tuple[str, str]]:
    return [
        ("created_at", "created_at"),
        ("event_type", "event"),
        ("from_status", "from"),
        ("to_status", "to"),
        ("reason", "reason"),
        ("attempt_id", "attempt_id"),
        ("callback_id", "callback_id"),
        ("payload", "payload"),
    ]


def _stuck_columns() -> list[tuple[str, str]]:
    return [
        ("issue", "issue"),
        ("job_id", "job_id"),
        ("job_status", "job_status"),
        ("job_type", "job_type"),
        ("related_id", "related_id"),
        ("related_status", "related_status"),
        ("since_at", "since_at"),
        ("next_attempt_at", "next_attempt_at"),
        ("detail", "detail"),
    ]


def _job_summary(job: dict) -> dict[str, Any]:
    return {
        "job_id": str(job.get("id")),
        "status": job.get("status"),
        "job_type": job.get("job_type"),
        "caller_id": job.get("caller_id"),
        "client_request_id": job.get("client_request_id"),
        "progress": {
            "percent": job.get("progress_percent"),
            "stage": job.get("progress_stage"),
            "text": job.get("progress_text"),
        },
        "callback": {
            "status": job.get("callback_status"),
            "attempts": job.get("callback_attempts"),
            "next_retry_at": job.get("callback_next_retry_at"),
            "last_error": job.get("callback_last_error"),
        },
        "created_at": job.get("created_at"),
        "queued_at": job.get("queued_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "updated_at": job.get("updated_at"),
    }


def _registered_job_type_specs() -> list[dict[str, Any]]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            from app.jobs.registry import all_job_type_specs
            from app.jobs.types.register import register_all_job_types

            register_all_job_types()
            return sorted((asdict(spec) for spec in all_job_type_specs().values()), key=lambda item: item["job_type"])
    except Exception:
        output = stdout.getvalue().strip()
        errors = stderr.getvalue().strip()
        if output:
            print(output, file=sys.stderr)
        if errors:
            print(errors, file=sys.stderr)
        raise


def _render_result(*, section: str, target: str, rows: list[dict], columns: list[tuple[str, str]]) -> None:
    formatters.section(section)
    formatters.event("OK", target, f"count={len(rows)}")
    formatters.print_table(rows, columns)


def _connect():
    try:
        return db.connect_readonly()
    except Exception as exc:
        print(f"ERROR: database unavailable: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc


def _with_connection(action) -> Any:
    conn = _connect()
    try:
        return action(conn)
    except typer.Exit:
        raise
    except Exception as exc:
        print(f"ERROR: job query failed: {exc}", file=sys.stderr)
        raise typer.Exit(4) from exc
    finally:
        conn.rollback()
        conn.close()


@app.command("list", help="查看最近 Job 摘要。")
def list_jobs(
    status: Annotated[
        list[str] | None,
        typer.Option("--status", help="按 Job 状态过滤；可重复传入，也可用逗号分隔。"),
    ] = None,
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    client_request_id: Annotated[
        str | None,
        typer.Option("--client-request-id", help="按 client_request_id 过滤。"),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="只查看指定时间窗口内创建的 Job，例如 24h。"),
    ] = None,
    limit: LimitOption = 20,
    json_output: JsonOption = False,
) -> None:
    try:
        statuses = parse_statuses(status)
        since_delta = parse_optional_duration(since)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc

    since_at = datetime.now(timezone.utc) - since_delta if since_delta else None
    rows = _with_connection(
        lambda conn: queries.list_jobs(
            conn,
            statuses=statuses,
            job_type=job_type,
            caller_id=caller_id,
            client_request_id=client_request_id,
            since=since_at,
            limit=limit,
        )
    )
    if json_output:
        formatters.print_json({"jobs": rows})
        return
    _render_result(section="Jobs", target="jobs", rows=rows, columns=_jobs_columns())


@app.command(help="查看单个 Job 权威状态。")
def show(job_id: JobIdArgument, json_output: JsonOption = False) -> None:
    job = _with_connection(lambda conn: queries.get_job(conn, job_id))
    if job is None:
        print(f"ERROR: job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json({"job": job})
        return
    formatters.section("Job")
    formatters.event("OK", "job", f"job_id={job_id}")
    formatters.print_json(_job_summary(job) | {"payload_summary": formatters.summarize_job_payload(job)})


@app.command(help="聚合查看单个 Job。")
def inspect(
    job_id: JobIdArgument,
    events_limit: Annotated[
        int,
        typer.Option("--events-limit", min=1, max=1000, help="展示的最近事件条数。"),
    ] = 10,
    json_output: JsonOption = False,
) -> None:
    def action(conn):
        job = queries.get_job(conn, job_id)
        if job is None:
            return None
        return {
            "job": job,
            "attempts": queries.attempts(conn, job_id),
            "callbacks": queries.callbacks(conn, job_id),
            "timeline": queries.timeline(conn, job_id, limit=events_limit),
        }

    payload = _with_connection(action)
    if payload is None:
        print(f"ERROR: job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json(payload)
        return
    formatters.section("Job Inspect")
    formatters.event(
        "OK",
        "job",
        "attempts=%s callbacks=%s timeline=%s"
        % (len(payload["attempts"]), len(payload["callbacks"]), len(payload["timeline"])),
    )
    formatters.print_json(
        {
            "job": _job_summary(payload["job"]),
            "payload_summary": formatters.summarize_job_payload(payload["job"]),
            "attempts": payload["attempts"],
            "callbacks": payload["callbacks"],
            "timeline": payload["timeline"],
        }
    )


def _run_related_collection(
    job_id: str,
    *,
    query_fn,
    section: str,
    target: str,
    columns: list[tuple[str, str]],
    key: str,
    json_output: bool,
) -> None:
    def action(conn):
        if queries.get_job(conn, job_id) is None:
            return None
        return query_fn(conn, job_id)

    rows = _with_connection(action)
    if rows is None:
        print(f"ERROR: job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json({"job_id": job_id, key: rows})
        return
    _render_result(section=section, target=target, rows=rows, columns=columns)


@app.command(help="查看 lifecycle job events。")
def timeline(
    job_id: JobIdArgument,
    limit: LimitOption = 50,
    json_output: JsonOption = False,
) -> None:
    _run_related_collection(
        job_id,
        query_fn=lambda conn, value: queries.timeline(conn, value, limit=limit),
        section="Timeline",
        target="events",
        columns=_timeline_columns(),
        key="timeline",
        json_output=json_output,
    )


@app.command(help="查看 lifecycle attempts。")
def attempts(job_id: JobIdArgument, json_output: JsonOption = False) -> None:
    _run_related_collection(
        job_id,
        query_fn=queries.attempts,
        section="Attempts",
        target="attempts",
        columns=_attempt_columns(),
        key="attempts",
        json_output=json_output,
    )


@app.command(help="查看 lifecycle callback outbox。")
def callbacks(job_id: JobIdArgument, json_output: JsonOption = False) -> None:
    _run_related_collection(
        job_id,
        query_fn=queries.callbacks,
        section="Callbacks",
        target="callbacks",
        columns=_callback_columns(),
        key="callbacks",
        json_output=json_output,
    )


@app.command(help="扫描疑似卡住的 Job、attempt 或 callback lease。")
def stuck(
    older_than: Annotated[
        str,
        typer.Option("--older-than", help="卡住判定时间窗口，例如 10m。"),
    ] = "10m",
    limit: LimitOption = 50,
    json_output: JsonOption = False,
) -> None:
    try:
        older_than_delta = parse_duration(older_than)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc

    rows = _with_connection(
        lambda conn: queries.stuck(
            conn,
            older_than=older_than_delta,
            limit=limit,
        )
    )
    if json_output:
        formatters.print_json({"items": rows})
        return
    _render_result(section="Stuck Jobs", target="items", rows=rows, columns=_stuck_columns())


@app.command(help="查看当前注册的 job_type。")
def types(json_output: JsonOption = False) -> None:
    try:
        specs = _registered_job_type_specs()
    except Exception as exc:
        print(f"ERROR: job type registry unavailable: {exc}", file=sys.stderr)
        raise typer.Exit(4) from exc
    if json_output:
        formatters.print_json({"job_types": specs})
        return
    rows = [
        {
            "job_type": spec["job_type"],
            "params_schema": spec["params_schema"],
            "public_result_schema": spec["public_result_schema"],
            "allow_callback": spec["allow_callback"],
            "max_attempts": spec["max_attempts"],
            "timeout_seconds": spec["timeout_seconds"],
        }
        for spec in specs
    ]
    _render_result(
        section="Job Types",
        target="job-types",
        rows=rows,
        columns=[
            ("job_type", "job_type"),
            ("params_schema", "params_schema"),
            ("public_result_schema", "public_result_schema"),
            ("allow_callback", "callback"),
            ("max_attempts", "attempts"),
            ("timeout_seconds", "timeout"),
        ],
    )


if __name__ == "__main__":
    app(prog_name="jobs.sh")
