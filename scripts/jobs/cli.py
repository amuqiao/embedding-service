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
VALID_LATENCY_GROUP_BY = {"all", "job_type", "caller_id", "status"}
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
  summary    汇总 Job、attempt、dispatch 和 callback 当前状态。
  latency    按 job_type / caller / status 统计 Job 生命周期耗时。
  capacity   查看 MAX_ACTIVE_JOBS 当前水位和窗口容量估算。
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
  ./scripts/jobs.sh summary --since 10m
  ./scripts/jobs.sh latency --since 30m --group-by job_type
  ./scripts/jobs.sh capacity --since 10m --max-active-jobs 750
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


def parse_latency_group_by(value: str) -> str:
    if value not in VALID_LATENCY_GROUP_BY:
        raise ValueError("无效 group-by：" + value + "；可选值：" + ", ".join(sorted(VALID_LATENCY_GROUP_BY)))
    return value


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


def _latency_columns() -> list[tuple[str, str]]:
    return [
        ("group_key", "group"),
        ("total", "total"),
        ("started", "started"),
        ("terminal", "terminal"),
        ("succeeded", "succeeded"),
        ("failed", "failed"),
        ("success_rate", "success_rate"),
        ("queue_wait_p95_seconds", "queue_p95_s"),
        ("run_p95_seconds", "run_p95_s"),
        ("lifecycle_p95_seconds", "lifecycle_p95_s"),
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


def _since_window(value: str) -> tuple[timedelta, datetime]:
    try:
        window = parse_duration(value)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    return window, datetime.now(timezone.utc) - window


def _env_max_active_jobs() -> int | None:
    raw = db.env_value("MAX_ACTIVE_JOBS")
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _capacity_recommendation(payload: dict[str, Any], max_active_jobs: int | None) -> dict[str, Any]:
    active_jobs = int(payload["current"].get("active_jobs") or 0)
    needed = payload["estimated"].get("active_jobs_needed_upper_bound")
    active_ratio = active_jobs / max_active_jobs if max_active_jobs and max_active_jobs > 0 else None
    if max_active_jobs is None:
        message = "未提供 MAX_ACTIVE_JOBS，无法判断当前水位比例。"
    elif max_active_jobs == 0:
        message = "MAX_ACTIVE_JOBS=0 表示跳过 active 门禁；生产不建议用它做容量保护。"
    elif active_ratio is not None and active_ratio >= 1:
        message = "当前 active 已达到或超过门禁；若组件健康且可排空，才小步提高 MAX_ACTIVE_JOBS。"
    elif active_ratio is not None and active_ratio >= 0.8:
        message = "当前 active 接近门禁；先确认 queued/running 可排空和 DB/Redis/worker 健康。"
    elif needed is not None and max_active_jobs is not None and needed > max_active_jobs:
        message = "按当前窗口生命周期上界估算，业务 active 需求可能高于门禁；先确认环境硬上限，再调整。"
    else:
        message = "当前窗口未显示 active 门禁压力；继续结合延迟、失败率和排空趋势判断。"
    return {
        "max_active_jobs": max_active_jobs,
        "active_ratio": active_ratio,
        "message": message,
    }


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


@app.command(help="汇总 Job、attempt、dispatch 和 callback 当前状态。")
def summary(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    since: Annotated[
        str,
        typer.Option("--since", help="只统计指定时间窗口内创建的 Job，例如 10m。"),
    ] = "10m",
    json_output: JsonOption = False,
) -> None:
    window, since_at = _since_window(since)
    payload = _with_connection(
        lambda conn: queries.summary(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
        )
    )
    payload = {
        "scope": {"since": since, "seconds": window.total_seconds(), "job_type": job_type, "caller_id": caller_id},
        **payload,
    }
    if json_output:
        formatters.print_json(payload)
        return
    formatters.section("Job Summary")
    formatters.event("OK", "summary", f"since={since}")
    formatters.print_json(payload)


@app.command(help="统计 Job 生命周期耗时。")
def latency(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    since: Annotated[
        str,
        typer.Option("--since", help="只统计指定时间窗口内创建的 Job，例如 30m。"),
    ] = "30m",
    group_by: Annotated[
        str,
        typer.Option("--group-by", help="分组字段：all、job_type、caller_id 或 status。"),
    ] = "job_type",
    json_output: JsonOption = False,
) -> None:
    try:
        group = parse_latency_group_by(group_by)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    window, since_at = _since_window(since)
    rows = _with_connection(
        lambda conn: queries.latency(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
            group_by=group,
        )
    )
    if json_output:
        formatters.print_json(
            {
                "scope": {"since": since, "seconds": window.total_seconds(), "job_type": job_type, "caller_id": caller_id},
                "group_by": group,
                "latency": rows,
            }
        )
        return
    _render_result(section="Job Latency", target="groups", rows=rows, columns=_latency_columns())


@app.command(help="查看 MAX_ACTIVE_JOBS 当前水位和窗口容量估算。")
def capacity(
    since: Annotated[
        str,
        typer.Option("--since", help="估算窗口，例如 10m。"),
    ] = "10m",
    max_active_jobs: Annotated[
        int | None,
        typer.Option("--max-active-jobs", min=0, help="用于计算水位比例；默认读取环境或 .env。"),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    window, since_at = _since_window(since)
    limit = max_active_jobs if max_active_jobs is not None else _env_max_active_jobs()
    payload = _with_connection(
        lambda conn: queries.capacity(
            conn,
            job_type=None,
            caller_id=None,
            since=since_at,
            window_seconds=window.total_seconds(),
        )
    )
    if limit is not None and limit > 0:
        payload["estimated"]["active_ratio"] = int(payload["current"].get("active_jobs") or 0) / limit
        payload["estimated"]["headroom"] = limit - int(payload["current"].get("active_jobs") or 0)
    else:
        payload["estimated"]["active_ratio"] = None
        payload["estimated"]["headroom"] = None
    payload = {
        "scope": {
            "current": "global",
            "window": {"since": since, "seconds": window.total_seconds()},
        },
        "max_active_jobs": limit,
        "current": payload["current"],
        "window": payload["window"],
        "estimated": payload["estimated"],
        "notes": {
            "current_active_jobs": "全局实时门禁口径：queued + running 且 active_attempt_id 非空。",
            "active_jobs_needed_upper_bound": "使用窗口 accepted_submit_rps * lifecycle_p95_seconds 得到的上界估算；workflow root 等待子任务时间会让它偏保守。",
        },
    }
    payload["recommendation"] = _capacity_recommendation(payload, limit)
    if json_output:
        formatters.print_json(payload)
        return
    formatters.section("Job Capacity")
    formatters.event("OK", "capacity", f"since={since}")
    formatters.print_json(payload)


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
