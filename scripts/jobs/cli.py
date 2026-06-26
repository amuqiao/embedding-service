from __future__ import annotations

import contextlib
import csv
import io
import re
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

import typer

from scripts.jobs import db, formatters, queries


VALID_JOB_STATUSES = {"queued", "running", "succeeded", "failed"}
VALID_LATENCY_GROUP_BY = {"all", "job_type", "caller_id", "status"}
DURATION_RE = re.compile(r"^(?P<value>[1-9][0-9]*)(?P<unit>s|m|h|d)$")
HTTP_STATUS_RE = re.compile(r"HTTP (?P<status>[0-9]{3})")
LOG_TIMESTAMP_RE = re.compile(r"^(?P<timestamp>[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}),")

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
  drain      判断压测前后 Job 是否已经排空。
  pressure   汇总压测窗口并判断瓶颈方向。
  summary    汇总 Job、attempt、dispatch 和 callback 当前状态。
  doctor     基于 summary 数据给出维护人员排障摘要和下一步检查。
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
  ./scripts/jobs.sh stuck --older-than 10m --caller-id default --json
  ./scripts/jobs.sh drain --since 30m --caller-id default --strict
  ./scripts/jobs.sh pressure --since 20m --caller-id default --max-active-jobs 1000 --locust-prefix .run/load/<run>
  ./scripts/jobs.sh summary --since 10m
  ./scripts/jobs.sh doctor --since 10m
  ./scripts/jobs.sh latency --since 30m --group-by job_type
  ./scripts/jobs.sh capacity --since 10m --caller-id default --max-active-jobs 1000
  ./scripts/jobs.sh types --json

\b
保护边界：
  只读查询不修改 DB，不触发真实业务调用，不投递消息，不重试 Job，不重放 callback。
  单个 job_id 不存在时返回非 0；列表、summary 和 doctor 的空结果会在成功输出中明确说明。

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


def _drain_current_columns() -> list[tuple[str, str]]:
    return [
        ("active_jobs", "active_jobs"),
        ("queued", "queued"),
        ("running", "running"),
        ("running_active", "running_active"),
        ("running_inactive", "running_inactive"),
    ]


def _drain_window_columns() -> list[tuple[str, str]]:
    return [
        ("total", "total"),
        ("active_jobs", "active_jobs"),
        ("queued", "queued"),
        ("running", "running"),
        ("running_active", "running_active"),
        ("running_inactive", "running_inactive"),
        ("succeeded", "succeeded"),
        ("failed", "failed"),
        ("oldest_created_at", "oldest_created_at"),
        ("newest_created_at", "newest_created_at"),
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


def _pressure_bottleneck_columns() -> list[tuple[str, str]]:
    return [
        ("severity", "severity"),
        ("area", "area"),
        ("signal", "signal"),
        ("message", "message"),
    ]


def _failure_group_columns() -> list[tuple[str, str]]:
    return [
        ("count", "count"),
        ("error_code", "code"),
        ("error_kind", "kind"),
        ("failure_phase", "phase"),
        ("detail_type", "type"),
        ("detail_message", "message"),
    ]


def _summary_job_columns() -> list[tuple[str, str]]:
    return [
        ("total", "total"),
        ("queued", "queued"),
        ("running", "running"),
        ("running_active", "running_active"),
        ("running_inactive", "running_inactive"),
        ("succeeded", "succeeded"),
        ("failed", "failed"),
        ("active_jobs", "active_jobs"),
        ("oldest_created_at", "oldest_created_at"),
        ("newest_created_at", "newest_created_at"),
    ]


def _summary_attempt_columns() -> list[tuple[str, str]]:
    return [
        ("total", "total"),
        ("pending", "pending"),
        ("running", "running"),
        ("succeeded", "succeeded"),
        ("failed", "failed"),
    ]


def _summary_dispatch_columns() -> list[tuple[str, str]]:
    return [
        ("total", "total"),
        ("pending", "pending"),
        ("leased", "leased"),
        ("published", "published"),
        ("retrying", "retrying"),
        ("dead_letter", "dead_letter"),
        ("due", "due"),
    ]


def _summary_callback_columns() -> list[tuple[str, str]]:
    return [
        ("total", "total"),
        ("pending", "pending"),
        ("leased", "leased"),
        ("delivering", "delivering"),
        ("delivered", "delivered"),
        ("failed", "failed"),
        ("dead_letter", "dead_letter"),
        ("due", "due"),
    ]


def _summary_by_job_type_columns() -> list[tuple[str, str]]:
    return [
        ("job_type", "job_type"),
        ("total", "total"),
        ("queued", "queued"),
        ("running", "running"),
        ("active_jobs", "active_jobs"),
        ("succeeded", "succeeded"),
        ("failed", "failed"),
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
        print(f"ERROR: invalid MAX_ACTIVE_JOBS: {raw}", file=sys.stderr)
        raise typer.Exit(2)


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


def _count(value: Any) -> int:
    return int(value or 0)


def _summary_payload(
    *,
    since: str,
    window: timedelta,
    job_type: str | None,
    caller_id: str | None,
    summary_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scope": {"since": since, "seconds": window.total_seconds(), "job_type": job_type, "caller_id": caller_id},
        **summary_payload,
    }


def _summary_next_checks(scope: dict[str, Any], *, no_jobs_found: bool) -> list[str]:
    filters = []
    if scope.get("job_type"):
        filters.append(f"--job-type {scope['job_type']}")
    if scope.get("caller_id"):
        filters.append(f"--caller-id {scope['caller_id']}")
    filter_text = (" " + " ".join(filters)) if filters else ""
    checks = [
        f"./scripts/jobs.sh list --since {scope['since']}{filter_text} --limit 20",
        f"./scripts/jobs.sh drain --since {scope['since']}{filter_text}",
        "./scripts/jobs.sh show <job_id>",
    ]
    if no_jobs_found:
        checks.insert(0, f"扩大 --since 窗口后重试，例如 ./scripts/jobs.sh doctor --since 1h{filter_text}")
        checks.append("./scripts/dev.sh status")
    return checks


def _diagnose_summary(payload: dict[str, Any]) -> dict[str, Any]:
    jobs = payload.get("jobs") or {}
    dispatch = payload.get("dispatch") or {}
    callbacks = payload.get("callbacks") or {}
    scope = payload["scope"]
    findings: list[dict[str, Any]] = []

    def add(status: str, metric: str, value: int, message: str) -> None:
        findings.append({"status": status, "metric": metric, "value": value, "message": message})

    total_jobs = _count(jobs.get("total"))
    no_jobs_found = total_jobs == 0
    if no_jobs_found:
        add("info", "jobs.total", 0, "no jobs found in the selected window.")
    else:
        add("ok", "jobs.total", total_jobs, "selected window contains jobs.")

    dispatch_due = _count(dispatch.get("due"))
    dispatch_dead_letter = _count(dispatch.get("dead_letter"))
    callbacks_due = _count(callbacks.get("due"))
    callbacks_dead_letter = _count(callbacks.get("dead_letter"))
    failed_jobs = _count(jobs.get("failed"))
    queued_jobs = _count(jobs.get("queued"))
    running_active = _count(jobs.get("running_active"))

    if dispatch_dead_letter:
        add("critical", "dispatch.dead_letter", dispatch_dead_letter, "dispatch outbox has dead-lettered run_attempt messages.")
    elif dispatch_due:
        add("warning", "dispatch.due", dispatch_due, "dispatch outbox has due run_attempt messages waiting to publish.")
    else:
        add("ok", "dispatch.due", 0, "no due dispatch messages.")

    if callbacks_dead_letter:
        add("critical", "callbacks.dead_letter", callbacks_dead_letter, "callback outbox has dead-lettered deliveries.")
    elif callbacks_due:
        add("warning", "callbacks.due", callbacks_due, "callback outbox has due deliveries waiting to run.")
    else:
        add("ok", "callbacks.due", 0, "no due callback deliveries.")

    if failed_jobs:
        add("warning", "jobs.failed", failed_jobs, "jobs failed in the selected window.")
    else:
        add("ok", "jobs.failed", 0, "no failed jobs in the selected window.")

    if queued_jobs:
        add("warning", "jobs.queued", queued_jobs, "queued jobs remain in the selected window.")
    else:
        add("ok", "jobs.queued", 0, "no queued jobs in the selected window.")

    if running_active:
        add("info", "jobs.running_active", running_active, "jobs are actively running.")
    else:
        add("ok", "jobs.running_active", 0, "no active running jobs in the selected window.")

    if any(item["status"] == "critical" for item in findings):
        status = "critical"
    elif any(item["status"] == "warning" for item in findings):
        status = "warning"
    else:
        status = "ok"

    next_checks = _summary_next_checks(scope, no_jobs_found=no_jobs_found)
    if dispatch_due or dispatch_dead_letter:
        next_checks.append("./scripts/jobs.sh stuck --older-than 10m")
    if callbacks_due or callbacks_dead_letter:
        next_checks.append("./scripts/jobs.sh callbacks <job_id>")
    if failed_jobs:
        next_checks.append("./scripts/jobs.sh inspect <job_id>")

    return {
        "scope": scope,
        "summary": {key: payload[key] for key in ("jobs", "by_job_type", "attempts", "dispatch", "callbacks")},
        "status": status,
        "findings": findings,
        "next_checks": next_checks,
    }


def _fetch_summary_payload(*, since: str, job_type: str | None, caller_id: str | None) -> dict[str, Any]:
    window, since_at = _since_window(since)
    raw_payload = _with_connection(
        lambda conn: queries.summary(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
        )
    )
    return _summary_payload(
        since=since,
        window=window,
        job_type=job_type,
        caller_id=caller_id,
        summary_payload=raw_payload,
    )


def _render_summary(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    jobs = payload.get("jobs") or {}
    no_jobs_found = _count(jobs.get("total")) == 0

    formatters.section("Job Summary")
    formatters.event(
        "OK",
        "summary",
        f"since={scope['since']} job_type={scope.get('job_type') or '-'} caller_id={scope.get('caller_id') or '-'}",
    )
    if no_jobs_found:
        print("no jobs found in the selected window.")
    formatters.print_table([jobs], _summary_job_columns())
    formatters.section("Attempts")
    formatters.print_table([payload.get("attempts") or {}], _summary_attempt_columns())
    formatters.section("Dispatch")
    formatters.print_table([payload.get("dispatch") or {}], _summary_dispatch_columns())
    formatters.section("Callbacks")
    formatters.print_table([payload.get("callbacks") or {}], _summary_callback_columns())
    formatters.section("By Job Type")
    formatters.print_table(payload.get("by_job_type") or [], _summary_by_job_type_columns(), empty_message="no jobs found")
    if no_jobs_found:
        formatters.section("Next Checks")
        for item in _summary_next_checks(scope, no_jobs_found=True):
            print(f"- {item}")


def _render_doctor(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    formatters.section("Job Doctor")
    formatters.event(
        payload["status"].upper(),
        "doctor",
        f"since={scope['since']} job_type={scope.get('job_type') or '-'} caller_id={scope.get('caller_id') or '-'}",
    )
    for finding in payload["findings"]:
        formatters.event(finding["status"].upper(), finding["metric"], f"{finding['value']} - {finding['message']}")
    formatters.section("Next Checks")
    for item in payload["next_checks"]:
        print(f"- {item}")


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


def _drain_payload(
    *,
    since: str,
    older_than: str,
    job_type: str | None,
    caller_id: str | None,
    raw_payload: dict[str, Any],
) -> dict[str, Any]:
    current_active = _count((raw_payload.get("current") or {}).get("active_jobs"))
    current_running_inactive = _count((raw_payload.get("current") or {}).get("running_inactive"))
    window_active = _count((raw_payload.get("window") or {}).get("active_jobs"))
    window_running_inactive = _count((raw_payload.get("window") or {}).get("running_inactive"))
    window_failed = _count((raw_payload.get("window") or {}).get("failed"))
    stuck_total = _count((raw_payload.get("stuck") or {}).get("total"))
    status = (
        "drained"
        if (
            current_active == 0
            and current_running_inactive == 0
            and window_active == 0
            and window_running_inactive == 0
            and window_failed == 0
            and stuck_total == 0
        )
        else "not_drained"
    )
    if status == "drained":
        message = "当前 scope 已排空且没有 failed/stuck 证据，可以进入下一档压测或结束本轮观察。"
    elif current_active == 0 and current_running_inactive == 0 and window_active == 0 and window_running_inactive == 0 and window_failed > 0 and stuck_total == 0:
        message = "当前 scope 已排空，但窗口内存在 failed Job；先 inspect 失败样本，不要进入下一档。"
    else:
        message = "当前 scope 仍有 active、running_inactive、failed 或 stuck 证据；先等待排空或继续排障。"
    filters = (
        (f" --job-type {job_type}" if job_type else "")
        + (f" --caller-id {caller_id}" if caller_id else "")
    )
    active_list_command = (
        f"./scripts/jobs.sh list --status queued,running{filters} --limit 20"
        if current_active > window_active
        else f"./scripts/jobs.sh list --status queued,running --since {since}{filters} --limit 20"
    )
    return {
        "scope": {
            "since": since,
            "older_than": older_than,
            "job_type": job_type,
            "caller_id": caller_id,
        },
        **raw_payload,
        "status": status,
        "message": message,
        "next_checks": [
            f"./scripts/jobs.sh summary --since {since}{filters}",
            f"./scripts/jobs.sh capacity --since {since}{filters}",
            f"./scripts/jobs.sh stuck --older-than {older_than} --since {since}{filters}",
            active_list_command,
            f"./scripts/jobs.sh list --status failed --since {since}{filters} --limit 20",
        ],
    }


def _render_drain(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    formatters.section("Job Drain")
    formatters.event(
        payload["status"].upper(),
        "drain",
        (
            f"since={scope['since']} older_than={scope['older_than']} "
            f"job_type={scope.get('job_type') or '-'} caller_id={scope.get('caller_id') or '-'}"
        ),
    )
    print(payload["message"])
    formatters.section("Current Active")
    formatters.print_table([payload.get("current") or {}], _drain_current_columns())
    formatters.section("Window")
    formatters.print_table([payload.get("window") or {}], _drain_window_columns())
    formatters.section("Stuck Sample")
    stuck = payload.get("stuck") or {}
    formatters.event("OK", "stuck", f"count={_count(stuck.get('total'))} truncated={bool(stuck.get('truncated'))}")
    formatters.print_table(stuck.get("sample") or [], _stuck_columns(), empty_message="no stuck records")
    if payload["status"] != "drained":
        formatters.section("Next Checks")
        for item in payload["next_checks"]:
            print(f"- {item}")


def _first_latency_row(rows: list[dict]) -> dict[str, Any]:
    return rows[0] if rows else {}


def _has_db_connection_error(failure_groups: list[dict]) -> bool:
    needles = (
        "toomanyconnection",
        "too many clients",
        "remaining connection slots",
        "could not obtain connection from pool",
        "asyncpg",
        "psycopg",
    )
    for group in failure_groups:
        text = " ".join(
            str(group.get(key) or "")
            for key in ("error_code", "error_kind", "failure_phase", "detail_type", "detail_message")
        ).lower()
        if any(needle in text for needle in needles):
            return True
    return False


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _number(value: str | None) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _locust_payload(prefix: str | None) -> dict[str, Any] | None:
    if prefix is None:
        return None
    prefix_path = Path(prefix)
    paths = {
        "stats": prefix_path.with_name(prefix_path.name + "_stats.csv"),
        "failures": prefix_path.with_name(prefix_path.name + "_failures.csv"),
        "exceptions": prefix_path.with_name(prefix_path.name + "_exceptions.csv"),
    }
    files = {name: {"path": str(path), "available": path.is_file()} for name, path in paths.items()}
    if not any(item["available"] for item in files.values()):
        return {
            "prefix": prefix,
            "available": False,
            "files": files,
            "post_jobs": {
                "request_count": None,
                "failure_count": None,
                "requests_per_second": None,
                "failures_per_second": None,
                "p95_ms": None,
                "p99_ms": None,
            },
            "failure_status_counts": {},
            "failures": [],
            "exceptions": [],
        }
    stats_rows = _read_csv_rows(paths["stats"])
    failure_rows = _read_csv_rows(paths["failures"])
    exception_rows = _read_csv_rows(paths["exceptions"])
    post_stats = next((row for row in stats_rows if row.get("Name") == "POST /jobs"), None)
    status_counts: dict[str, int] = {}
    for row in failure_rows:
        error = row.get("Error") or ""
        match = HTTP_STATUS_RE.search(error)
        if match:
            status = match.group("status")
            status_counts[status] = status_counts.get(status, 0) + int(row.get("Occurrences") or 0)
    return {
        "prefix": prefix,
        "available": True,
        "files": files,
        "post_jobs": {
            "request_count": _number(post_stats.get("Request Count")) if post_stats else None,
            "failure_count": _number(post_stats.get("Failure Count")) if post_stats else None,
            "requests_per_second": _number(post_stats.get("Requests/s")) if post_stats else None,
            "failures_per_second": _number(post_stats.get("Failures/s")) if post_stats else None,
            "p95_ms": _number(post_stats.get("95%")) if post_stats else None,
            "p99_ms": _number(post_stats.get("99%")) if post_stats else None,
        },
        "failure_status_counts": status_counts,
        "failures": failure_rows,
        "exceptions": exception_rows,
    }


def _line_timestamp(line: str) -> datetime | None:
    match = LOG_TIMESTAMP_RE.match(line)
    if not match:
        return None
    try:
        parsed = datetime.strptime(match.group("timestamp"), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.astimezone()


def _api_log_payload(path: str | None, *, tail_lines: int, since_at: datetime | None = None) -> dict[str, Any] | None:
    if path is None:
        return None
    log_path = Path(path)
    if not log_path.is_file():
        return {"path": path, "available": False, "matches": {}}
    raw_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-tail_lines:]
    lines: list[str] = []
    if since_at is None:
        lines = raw_lines
    else:
        local_since = since_at.astimezone()
        keep_without_timestamp = False
        for line in raw_lines:
            timestamp = _line_timestamp(line)
            if timestamp is None:
                if keep_without_timestamp:
                    lines.append(line)
                continue
            keep_without_timestamp = timestamp >= local_since
            if keep_without_timestamp:
                lines.append(line)
    signatures = {
        "too_many_connections": ("TooManyConnectionsError", "too many clients", "remaining connection slots"),
        "http_500": ("900500", " 500 ", "status=500"),
        "traceback": ("Traceback",),
        "queue_full": ("900503", "QUEUE_FULL", "active_jobs", "limit"),
    }
    matches: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for name, needles in signatures.items():
        matched = [line for line in lines if any(needle in line for needle in needles)]
        matches[name] = len(matched)
        samples[name] = matched[-3:]
    return {
        "path": path,
        "available": True,
        "tail_lines": tail_lines,
        "since_at": since_at.isoformat() if since_at is not None else None,
        "scanned_lines": len(lines),
        "matches": matches,
        "samples": samples,
    }


def _pressure_payload(
    *,
    since: str,
    older_than: str,
    job_type: str | None,
    caller_id: str | None,
    max_active_jobs: int | None,
    queue_wait_warning_seconds: float,
    run_warning_seconds: float,
    locust: dict[str, Any] | None = None,
    api_log: dict[str, Any] | None = None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    summary = payload["summary"]
    capacity_payload = payload["capacity"]
    latency_rows = payload["latency"]
    latency = _first_latency_row(latency_rows)
    stuck_rows = payload["stuck"]
    failure_groups = payload["failure_groups"]
    active_samples = payload["active_samples"]
    failed_samples = payload["failed_samples"]
    jobs = summary.get("jobs") or {}
    dispatch = summary.get("dispatch") or {}
    callbacks = summary.get("callbacks") or {}
    current = capacity_payload.get("current") or {}
    window = capacity_payload.get("window") or {}
    estimated = capacity_payload.get("estimated") or {}

    stuck_limit = payload.get("stuck_limit")
    bottlenecks: list[dict[str, Any]] = []

    def add(severity: str, area: str, signal: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        bottlenecks.append(
            {
                "severity": severity,
                "area": area,
                "signal": signal,
                "message": message,
                "evidence": evidence or {},
            }
        )

    total_jobs = _count(jobs.get("total"))
    active_jobs = _count(current.get("active_jobs"))
    queued = _count(jobs.get("queued"))
    running_active = _count(jobs.get("running_active"))
    failed = _count(jobs.get("failed"))
    dispatch_due = _count(dispatch.get("due"))
    dispatch_dead_letter = _count(dispatch.get("dead_letter"))
    callbacks_due = _count(callbacks.get("due"))
    callbacks_dead_letter = _count(callbacks.get("dead_letter"))
    stuck_total = len(stuck_rows)
    terminal_jobs = _count(window.get("terminal_jobs"))
    accepted_jobs = _count(window.get("accepted_jobs"))
    active_ratio = estimated.get("active_ratio")
    needed = estimated.get("active_jobs_needed_upper_bound")
    queue_p95 = latency.get("queue_wait_p95_seconds")
    run_p95 = latency.get("run_p95_seconds")
    success_rate = latency.get("success_rate")

    if total_jobs == 0:
        add("info", "scope", "empty_window", "当前窗口内没有 Job；扩大 --since 或确认 caller_id/job_type。")

    if locust:
        post_jobs = locust.get("post_jobs") or {}
        status_counts = locust.get("failure_status_counts") or {}
        request_count = _count(post_jobs.get("request_count"))
        failure_count = _count(post_jobs.get("failure_count"))
        if locust.get("available") is False:
            add(
                "critical",
                "http",
                "locust_csv_missing",
                "指定了 --locust-prefix，但没有找到对应的 Locust CSV；先确认前缀是否写错或压测是否完成。",
                {"prefix": locust.get("prefix"), "files": locust.get("files")},
            )
        elif _count(status_counts.get("500")) or any(int(status) >= 500 and status != "503" for status in status_counts):
            add(
                "critical",
                "http",
                "http_5xx",
                "Locust 记录到 HTTP 5xx；这不是容量保护通过，优先查 API/DB/Redis/日志。",
                {"failure_status_counts": status_counts, "post_jobs": post_jobs},
            )
        elif _count(status_counts.get("503")) and failure_count == _count(status_counts.get("503")):
            add(
                "warning",
                "capacity",
                "http_503_gate_hit",
                "Locust 失败主要是 HTTP 503，若响应体含 active_jobs/limit 且后台可排空，可判定容量门禁生效。",
                {"failure_status_counts": status_counts, "post_jobs": post_jobs},
            )
        elif failure_count:
            no_job_records = total_jobs == 0
            db_mismatch = bool(request_count and accepted_jobs < request_count and not status_counts)
            severity = "critical" if no_job_records or db_mismatch else "warning"
            signal = (
                "http_failures_no_job_records"
                if no_job_records
                else "http_failures_db_mismatch"
                if db_mismatch
                else "http_failures"
            )
            message = (
                "Locust 有失败但 DB 窗口没有 Job 记录；请求可能未到达 API、命中错误路径，或响应不是 Job JSON。"
                if no_job_records
                else "Locust 请求数与 DB 接单数明显不匹配，且 failures.csv 没有 HTTP 状态码；先查 API 是否存活、路径/前缀、认证和响应体。"
                if db_mismatch
                else "Locust 存在 HTTP 失败，需要结合 failures.csv 判断类型。"
            )
            add(
                severity,
                "http",
                signal,
                message,
                {
                    "failure_status_counts": status_counts,
                    "post_jobs": post_jobs,
                    "failures": (locust.get("failures") or [])[:3],
                },
            )

    if api_log:
        matches = api_log.get("matches") or {}
        if _count(matches.get("too_many_connections")):
            add(
                "critical",
                "database",
                "api_log_db_connection_pressure",
                "API 日志命中数据库连接耗尽签名。",
                {"matches": matches, "samples": (api_log.get("samples") or {}).get("too_many_connections")},
            )
        if _count(matches.get("traceback")):
            add("warning", "api", "api_traceback", "API 日志命中 Traceback。", {"matches": matches})

    if max_active_jobs and max_active_jobs > 0 and active_ratio is not None:
        if active_ratio >= 1:
            add(
                "critical",
                "capacity",
                "active_gate_saturated",
                "全局 active 已达到或超过 MAX_ACTIVE_JOBS，POST /jobs 可能开始返回 503。",
                {"active_jobs": active_jobs, "max_active_jobs": max_active_jobs, "active_ratio": active_ratio},
            )
        elif active_ratio >= 0.8:
            add(
                "warning",
                "capacity",
                "active_gate_near_limit",
                "全局 active 接近 MAX_ACTIVE_JOBS，继续加压前先确认可排空。",
                {"active_jobs": active_jobs, "max_active_jobs": max_active_jobs, "active_ratio": active_ratio},
            )

    if accepted_jobs and terminal_jobs < accepted_jobs:
        add(
            "warning",
            "lifecycle",
            "window_not_terminal",
            "压测窗口内仍有 Job 未到终态；当前 p95 和容量估算只能作为中间态。",
            {"accepted_jobs": accepted_jobs, "terminal_jobs": terminal_jobs},
        )

    if failed:
        add(
            "critical",
            "execution",
            "job_failures",
            "窗口内存在 failed Job；HTTP 接单成功不代表 Job 执行成功。",
            {"failed": failed, "top_failure": failure_groups[0] if failure_groups else None},
        )

    if _has_db_connection_error(failure_groups):
        add(
            "critical",
            "database",
            "db_connection_pressure",
            "失败样本包含数据库连接耗尽信号，优先查 DB 连接数、连接池和 worker/API 并发。",
            {"failure_groups": failure_groups[:3]},
        )

    if stuck_total:
        issue_counts: dict[str, int] = {}
        for row in stuck_rows:
            issue = str(row.get("issue") or "unknown")
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
        for issue, count in sorted(issue_counts.items(), key=lambda item: (-item[1], item[0])):
            area = {
                "published_dispatch_not_claimed": "worker_broker",
                "running_attempt_lease_expired": "worker",
                "callback_lease_expired": "callback",
                "terminal_callback_not_settled": "callback",
                "dispatch_due_not_published": "dispatch",
            }.get(issue, "lifecycle")
            message = {
                "published_dispatch_not_claimed": "dispatch 已发布但 attempt 未被 worker 领取，优先查 worker/broker 消费。",
                "dispatch_due_not_published": "dispatch 到期但未发布，优先查 outbox 发布路径。",
                "running_attempt_lease_expired": "running attempt lease 过期，优先查 worker 心跳和恢复路径。",
                "callback_lease_expired": "callback lease 过期，优先查 callback 投递 worker。",
                "terminal_callback_not_settled": "终态 Job 的 callback 未完成，优先查 callback 目标和重试。",
            }.get(issue, "存在疑似 stuck 记录，先 inspect 样本。")
            add("critical", area, issue, message, {"count": count})

    if dispatch_dead_letter:
        add("critical", "dispatch", "dispatch_dead_letter", "dispatch outbox 有 dead letter，任务发布路径已失败。", {"count": dispatch_dead_letter})
    elif dispatch_due:
        add("warning", "dispatch", "dispatch_due", "dispatch outbox 有到期待发布记录，任务发布可能滞后。", {"count": dispatch_due})

    if callbacks_dead_letter:
        add("critical", "callback", "callback_dead_letter", "callback outbox 有 dead letter。", {"count": callbacks_dead_letter})
    elif callbacks_due:
        add("warning", "callback", "callback_due", "callback outbox 有到期待投递记录。", {"count": callbacks_due})

    if queue_p95 is not None and float(queue_p95) >= queue_wait_warning_seconds:
        area = "worker_broker" if running_active == 0 or queued > running_active else "capacity"
        add(
            "warning",
            area,
            "queue_wait_high",
            "queue wait p95 偏高，优先判断 worker/broker 消费是否跟上。",
            {"queue_wait_p95_seconds": queue_p95, "queued": queued, "running_active": running_active},
        )

    if run_p95 is not None and float(run_p95) >= run_warning_seconds:
        add(
            "warning",
            "execution",
            "run_time_high",
            "run p95 偏高，瓶颈更可能在 Job 执行、worker 资源或外部依赖。",
            {"run_p95_seconds": run_p95},
        )

    if success_rate is not None and float(success_rate) < 1:
        add("warning", "execution", "success_rate_below_1", "窗口终态成功率低于 100%。", {"success_rate": success_rate})

    if needed is not None and max_active_jobs and max_active_jobs > 0 and float(needed) > max_active_jobs:
        add(
            "warning",
            "capacity",
            "estimated_need_exceeds_limit",
            "按当前窗口估算的 active 需求超过 MAX_ACTIVE_JOBS；先确认没有 failed/stuck 后再讨论扩容。",
            {"active_jobs_needed_upper_bound": needed, "max_active_jobs": max_active_jobs},
        )

    if total_jobs and not bottlenecks:
        add("ok", "overall", "no_obvious_bottleneck", "当前窗口没有明显瓶颈信号；继续按阶梯加压并观察。")

    severity_rank = {"critical": 3, "warning": 2, "info": 1, "ok": 0}
    worst = max((severity_rank.get(item["severity"], 0) for item in bottlenecks), default=0)
    status = {3: "critical", 2: "warning", 1: "info", 0: "ok"}[worst]
    filters = (
        (f" --job-type {job_type}" if job_type else "")
        + (f" --caller-id {caller_id}" if caller_id else "")
    )
    return {
        "scope": {
            "since": since,
            "older_than": older_than,
            "job_type": job_type,
            "caller_id": caller_id,
            "queue_wait_warning_seconds": queue_wait_warning_seconds,
            "run_warning_seconds": run_warning_seconds,
        },
        "status": status,
        "bottlenecks": bottlenecks,
        "summary": summary,
        "capacity": capacity_payload,
        "latency": latency_rows,
        "stuck": {
            "sample_count": stuck_total,
            "sample": stuck_rows,
            "truncated": bool(stuck_limit and stuck_total >= int(stuck_limit)),
        },
        "failure_groups": failure_groups,
        "http": locust,
        "api_log": api_log,
        "samples": {"active": active_samples, "failed": failed_samples},
        "next_checks": [
            f"./scripts/jobs.sh drain --since {since} --older-than {older_than}{filters} --strict",
            f"./scripts/jobs.sh list --status failed --since {since}{filters} --limit 20",
            f"./scripts/jobs.sh stuck --since {since} --older-than {older_than}{filters} --limit 20",
            f"./scripts/jobs.sh latency --since {since}{filters} --group-by job_type",
            "./scripts/dev.sh status",
        ],
    }


def _render_pressure(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    formatters.section("Job Pressure Diagnosis")
    formatters.event(
        payload["status"].upper(),
        "pressure",
        f"since={scope['since']} older_than={scope['older_than']} job_type={scope.get('job_type') or '-'} caller_id={scope.get('caller_id') or '-'}",
    )
    formatters.section("Bottlenecks")
    rows = [
        {
            "severity": item["severity"],
            "area": item["area"],
            "signal": item["signal"],
            "message": item["message"],
        }
        for item in payload["bottlenecks"]
    ]
    formatters.print_table(rows, _pressure_bottleneck_columns())
    formatters.section("Capacity")
    formatters.print_json(payload["capacity"])
    if payload.get("http") is not None:
        formatters.section("HTTP")
        formatters.print_json(payload["http"])
    if payload.get("api_log") is not None:
        formatters.section("API Log")
        formatters.print_json(payload["api_log"])
    formatters.section("Latency")
    formatters.print_table(payload["latency"], _latency_columns(), empty_message="no latency data")
    formatters.section("Failure Groups")
    formatters.print_table(payload["failure_groups"], _failure_group_columns(), empty_message="no failed jobs")
    formatters.section("Stuck Sample")
    stuck = payload["stuck"]
    formatters.event("OK", "stuck", f"sample_count={stuck['sample_count']} truncated={stuck['truncated']}")
    formatters.print_table(stuck["sample"], _stuck_columns(), empty_message="no stuck records")
    formatters.section("Next Checks")
    for item in payload["next_checks"]:
        print(f"- {item}")


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
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="只扫描指定时间窗口内创建的 Job，例如 30m。"),
    ] = None,
    limit: LimitOption = 50,
    json_output: JsonOption = False,
) -> None:
    try:
        older_than_delta = parse_duration(older_than)
        since_delta = parse_optional_duration(since)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc

    since_at = datetime.now(timezone.utc) - since_delta if since_delta else None
    rows = _with_connection(
        lambda conn: queries.stuck(
            conn,
            older_than=older_than_delta,
            limit=limit,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
        )
    )
    if json_output:
        formatters.print_json(
            {
                "scope": {
                    "older_than": older_than,
                    "since": since,
                    "job_type": job_type,
                    "caller_id": caller_id,
                },
                "items": rows,
            }
        )
        return
    _render_result(section="Stuck Jobs", target="items", rows=rows, columns=_stuck_columns())


@app.command(help="判断压测前后 Job 是否已经排空。")
def drain(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    since: Annotated[
        str,
        typer.Option("--since", help="只检查指定时间窗口内创建的 Job，例如 30m。"),
    ] = "30m",
    older_than: Annotated[
        str,
        typer.Option("--older-than", help="stuck 判定时间窗口，例如 10m。"),
    ] = "10m",
    strict: Annotated[
        bool,
        typer.Option("--strict", help="未排空时返回 exit 4，适合压测自动化脚本。"),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    window, since_at = _since_window(since)
    try:
        older_than_delta = parse_duration(older_than)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    raw_payload = _with_connection(
        lambda conn: queries.drain_status(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
            older_than=older_than_delta,
        )
    )
    payload = _drain_payload(
        since=since,
        older_than=older_than,
        job_type=job_type,
        caller_id=caller_id,
        raw_payload=raw_payload | {"scope_window_seconds": window.total_seconds()},
    )
    if json_output:
        formatters.print_json(payload)
    else:
        _render_drain(payload)
    if strict and payload["status"] != "drained":
        raise typer.Exit(4)


@app.command(help="汇总压测窗口并判断瓶颈方向。")
def pressure(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤窗口证据。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤窗口证据。")] = None,
    since: Annotated[
        str,
        typer.Option("--since", help="诊断窗口，例如 20m。"),
    ] = "20m",
    older_than: Annotated[
        str,
        typer.Option("--older-than", help="stuck 判定窗口，例如 1m。"),
    ] = "1m",
    max_active_jobs: Annotated[
        int | None,
        typer.Option("--max-active-jobs", min=0, help="用于计算水位比例；默认读取环境或 .env。"),
    ] = None,
    queue_wait_warning_seconds: Annotated[
        float,
        typer.Option("--queue-wait-warning-seconds", min=0, help="queue wait p95 超过该值时提示消费瓶颈。"),
    ] = 30.0,
    run_warning_seconds: Annotated[
        float,
        typer.Option("--run-warning-seconds", min=0, help="run p95 超过该值时提示执行瓶颈。"),
    ] = 60.0,
    sample_limit: Annotated[
        int,
        typer.Option("--sample-limit", min=1, max=100, help="失败、active、stuck 样本条数。"),
    ] = 20,
    locust_prefix: Annotated[
        str | None,
        typer.Option("--locust-prefix", help="Locust --csv 前缀，用于读取 *_stats.csv、*_failures.csv、*_exceptions.csv。"),
    ] = None,
    api_log: Annotated[
        str | None,
        typer.Option("--api-log", help="API 日志路径，用于扫描 TooManyConnectionsError、900500、Traceback 等签名。"),
    ] = None,
    api_log_tail: Annotated[
        int,
        typer.Option("--api-log-tail", min=1, max=20000, help="扫描 API 日志末尾行数。"),
    ] = 1000,
    json_output: JsonOption = False,
) -> None:
    window, since_at = _since_window(since)
    try:
        older_than_delta = parse_duration(older_than)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    limit = max_active_jobs if max_active_jobs is not None else _env_max_active_jobs()

    def action(conn):
        summary_payload = queries.summary(conn, job_type=job_type, caller_id=caller_id, since=since_at)
        capacity_payload = queries.capacity(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
            window_seconds=window.total_seconds(),
        )
        if limit is not None and limit > 0:
            capacity_payload["estimated"]["active_ratio"] = int(capacity_payload["current"].get("active_jobs") or 0) / limit
            capacity_payload["estimated"]["headroom"] = limit - int(capacity_payload["current"].get("active_jobs") or 0)
        else:
            capacity_payload["estimated"]["active_ratio"] = None
            capacity_payload["estimated"]["headroom"] = None
        return {
            "stuck_limit": sample_limit,
            "summary": _summary_payload(
                since=since,
                window=window,
                job_type=job_type,
                caller_id=caller_id,
                summary_payload=summary_payload,
            ),
            "capacity": {
                "scope": {
                    "current": "global",
                    "window": {
                        "since": since,
                        "seconds": window.total_seconds(),
                        "job_type": job_type,
                        "caller_id": caller_id,
                    },
                },
                "max_active_jobs": limit,
                "current": capacity_payload["current"],
                "window": capacity_payload["window"],
                "estimated": capacity_payload["estimated"],
            },
            "latency": queries.latency(
                conn,
                job_type=job_type,
                caller_id=caller_id,
                since=since_at,
                group_by="all",
            ),
            "stuck": queries.stuck(
                conn,
                older_than=older_than_delta,
                limit=sample_limit,
                job_type=job_type,
                caller_id=caller_id,
                since=since_at,
            ),
            "failure_groups": queries.failure_groups(
                conn,
                job_type=job_type,
                caller_id=caller_id,
                since=since_at,
                limit=sample_limit,
            ),
            "active_samples": queries.list_jobs(
                conn,
                statuses=["queued", "running"],
                job_type=job_type,
                caller_id=caller_id,
                client_request_id=None,
                since=since_at,
                limit=sample_limit,
            ),
            "failed_samples": queries.list_jobs(
                conn,
                statuses=["failed"],
                job_type=job_type,
                caller_id=caller_id,
                client_request_id=None,
                since=since_at,
                limit=sample_limit,
            ),
        }

    raw_payload = _with_connection(action)
    payload = _pressure_payload(
        since=since,
        older_than=older_than,
        job_type=job_type,
        caller_id=caller_id,
        max_active_jobs=limit,
        queue_wait_warning_seconds=queue_wait_warning_seconds,
        run_warning_seconds=run_warning_seconds,
        locust=_locust_payload(locust_prefix),
        api_log=_api_log_payload(api_log, tail_lines=api_log_tail, since_at=since_at),
        payload=raw_payload,
    )
    if json_output:
        formatters.print_json(payload)
        return
    _render_pressure(payload)


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
    payload = _fetch_summary_payload(since=since, job_type=job_type, caller_id=caller_id)
    if json_output:
        formatters.print_json(payload)
        return
    _render_summary(payload)


@app.command(help="基于 summary 数据给出维护人员排障摘要和下一步检查。")
def doctor(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    since: Annotated[
        str,
        typer.Option("--since", help="只诊断指定时间窗口内创建的 Job，例如 10m。"),
    ] = "10m",
    json_output: JsonOption = False,
) -> None:
    summary_payload = _fetch_summary_payload(since=since, job_type=job_type, caller_id=caller_id)
    payload = _diagnose_summary(summary_payload)
    if json_output:
        formatters.print_json(payload)
        return
    _render_doctor(payload)


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
    job_type: Annotated[str | None, typer.Option("--job-type", help="窗口估算按 job_type 过滤；current 仍是全局门禁口径。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="窗口估算按 caller_id 过滤；current 仍是全局门禁口径。")] = None,
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
            job_type=job_type,
            caller_id=caller_id,
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
            "window": {"since": since, "seconds": window.total_seconds(), "job_type": job_type, "caller_id": caller_id},
        },
        "max_active_jobs": limit,
        "current": payload["current"],
        "window": payload["window"],
        "estimated": payload["estimated"],
        "notes": {
            "current_active_jobs": "全局实时门禁口径：queued + running 且 active_attempt_id 非空。",
            "accepted_submit_rps": "使用窗口内 first_created_at 到 newest_created_at 的 observed span 估算；没有跨度时退回 --since 秒数。",
            "active_jobs_needed_upper_bound": "使用窗口 accepted_submit_rps * lifecycle_p95_seconds 得到的上界估算；workflow root 等待子任务时间会让它偏保守；terminal_jobs 少于 accepted_jobs 时仍应等待排空后再采信。",
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
