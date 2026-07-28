from __future__ import annotations

import contextlib
import csv
import io
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

import typer

from scripts.jobs import db, formatters, queries
from scripts.redis_diag.cli import broker_payload as redis_broker_payload


VALID_JOB_STATUSES = {"queued", "running", "succeeded", "failed"}
VALID_LATENCY_GROUP_BY = {"all", "job_type", "caller_id", "status"}
DURATION_RE = re.compile(r"^(?P<value>[1-9][0-9]*)(?P<unit>s|m|h|d)$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
HTTP_STATUS_RE = re.compile(r"HTTP (?P<status>[0-9]{3})")
LOG_TIMESTAMP_RE = re.compile(r"^(?P<timestamp>[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}),")

LIST_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh list --status running --since 24h --limit 20
  ./scripts/jobs.sh list --scope family --status queued,running --caller-id default
  ./scripts/jobs.sh list --scope child --status failed --json
"""

DELETED_SUMMARY_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh deleted-summary
  ./scripts/jobs.sh deleted-summary --since-deleted 7d --json
"""

DELETED_LIST_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh deleted-list --limit 20
  ./scripts/jobs.sh deleted-list --scope family --since-deleted 7d --json
"""

DELETED_JOB_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh deleted-job <job_id>
  ./scripts/jobs.sh deleted-job <job_id> --json
"""

SHOW_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh job <job_id>
  ./scripts/jobs.sh show <job_id> --json
"""

INSPECT_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh inspect <job_id>
  ./scripts/jobs.sh workflow <job_id>
  ./scripts/jobs.sh inspect <job_id> --events-limit 50 --json
"""

PAYLOAD_HELP_EPILOG = """\b
说明：
  payload 默认只输出入参、runtime、结果和错误 payload 的结构摘要。
  使用 --full 才输出完整 payload；输出可能很大，只在明确需要排查原始内容时使用。

\b
常用示例：
  ./scripts/jobs.sh payload <job_id>
  ./scripts/jobs.sh payload <job_id> --json
  ./scripts/jobs.sh payload <job_id> --full
"""

DIAGNOSE_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh diagnose <job_id>
  ./scripts/jobs.sh workflow <job_id>
  ./scripts/jobs.sh diagnose <job_id> --older-than 1m
  ./scripts/jobs.sh diagnose <job_id> --json
"""

TIMELINE_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh timeline <job_id> --limit 50
  ./scripts/jobs.sh timeline <job_id> --json
"""

ATTEMPTS_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh attempts <job_id>
  ./scripts/jobs.sh attempts <job_id> --json
"""

AI_CALLS_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh ai-calls <job_id>
  ./scripts/jobs.sh ai-calls <job_id> --json
"""

CALLBACKS_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh callbacks <job_id>
  ./scripts/jobs.sh callbacks <job_id> --json
"""

TRACE_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh trace <job_id>
  ./scripts/jobs.sh trace <job_id> --include-children
  ./scripts/jobs.sh trace <job_id> --json
"""

STUCK_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh stuck --older-than 10m --caller-id default
  ./scripts/jobs.sh stuck --scope root --older-than 10m
  ./scripts/jobs.sh stuck --older-than 10m --caller-id default --json
"""

DRAIN_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh drain --since 30m --caller-id default
  ./scripts/jobs.sh drain --since 30m --caller-id default --strict
"""

PRESSURE_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh pressure --since 20m --caller-id default --max-active-jobs 1000
  ./scripts/jobs.sh pressure --since 20m --caller-id default --max-active-jobs 1000 --locust-prefix .run/load/<run>
"""

SUMMARY_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh summary --since 10m
  ./scripts/jobs.sh summary --since 10m --caller-id default --json
"""

DOCTOR_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh doctor --since 10m
  ./scripts/jobs.sh doctor --since 10m --caller-id default --json
"""

OBSERVE_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh observe --interval 60 --samples 5
  ./scripts/jobs.sh observe --since 30m --caller-id default --json
"""

DASHBOARD_HELP_EPILOG = """\b
说明：
  dashboard 汇总 DB 业务事实源中的系统数量、容量、吞吐、耗时和 stuck 样本。
  不读取 Redis broker 或 Pod runtime；运输层和运行时请继续使用 broker / runtime。

\b
常用示例：
  ./scripts/jobs.sh dashboard --since 1h
  ./scripts/jobs.sh dashboard --since 1h --bucket 1m --caller-id default --json
"""

BROKER_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh broker
  ./scripts/jobs.sh broker --redis-key taskiq --json
"""

RUNTIME_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh runtime
  ./scripts/jobs.sh runtime --json
"""

FAILURES_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh failures --since 1h --limit 20
  ./scripts/jobs.sh failures --since 1h --caller-id default --json
"""

CALLBACKS_SUMMARY_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh callbacks-summary --since 1h
  ./scripts/jobs.sh callbacks-summary --since 1h --caller-id default --json
"""

OVERVIEW_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh
  ./scripts/jobs.sh overview --since 10m
  ./scripts/jobs.sh overview --since 20m --caller-id default --json
"""

JOB_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh job <job_id>
  ./scripts/jobs.sh job <job_id> --json
"""

WORKFLOW_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh workflow <job_id>
  ./scripts/jobs.sh workflow <child_job_id> --json
  ./scripts/jobs.sh workflow <job_id> --events-limit 100
"""

LATENCY_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh latency --since 30m --group-by job_type
  ./scripts/jobs.sh latency --since 30m --group-by status --json
"""

INGRESS_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh ingress --since 30m --bucket 1m
  ./scripts/jobs.sh ingress --caller-id default --job-type example_sleep --since 1h --json
"""

CAPACITY_HELP_EPILOG = """\b
说明：
  capacity 同时展示当前全局 active 占用和指定窗口的容量估算。
  active 占用用于判断是否接近 MAX_ACTIVE_JOBS 上限。
  只看当前全局 active 占用时，优先使用 ./scripts/jobs.sh gate。

\b
常用示例：
  ./scripts/jobs.sh capacity --since 10m --caller-id default --max-active-jobs 1000
  ./scripts/jobs.sh capacity --worker-pods 4 --worker-concurrency 30 --api-pods 2 --db-max-connections 100
  ./scripts/jobs.sh capacity --since 10m --json
"""

GATE_HELP_EPILOG = """\b
说明：
  gate 查看当前全局 active 占用。
  可以把 active 占用理解为：现在有多少 Job 正在排队，或正在被 worker 执行。
  不使用 --since 时间窗口，也不按 job_type 或 caller_id 过滤。
  active_jobs = queued + running 且 active_attempt_id 非空。

\b
常用示例：
  ./scripts/jobs.sh gate
  ./scripts/jobs.sh gate --max-active-jobs 1000
  ./scripts/jobs.sh gate --json
"""

TYPES_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh types
  ./scripts/jobs.sh types --all
  ./scripts/jobs.sh types --json
"""

GUIDE_HELP_EPILOG = """\b
常用示例：
  ./scripts/jobs.sh guide
"""

HELP_EPILOG = """\b
作用域：
  在本地或 Pod 内查询 Job、attempt、callback 和 timeline 证据。
  本入口只执行只读查询，不提供创建、取消、重试、补偿或 callback 重放能力。

\b
默认行为：
  ./scripts/jobs.sh 等同于 overview，默认查看最近 10m，stuck 判定窗口 1m，样本条数 10。

\b
四层模型：
  系统态           dashboard / overview / doctor / gate / capacity / pressure / ingress
  恢复态           observe / drain / stuck
  运输和运行时     broker / runtime
  单 Job 轨迹      trace / inspect / diagnose / workflow / attempts / ai-calls / callbacks / timeline

\b
常用排障顺序：
  ./scripts/jobs.sh dashboard --since 1h
  ./scripts/jobs.sh overview --since 1h
  ./scripts/jobs.sh observe --interval 60 --samples 5
  ./scripts/jobs.sh broker
  ./scripts/jobs.sh runtime
  ./scripts/jobs.sh failures --since 1h
  ./scripts/jobs.sh callbacks-summary --since 1h

\b
常见问题：
  当前是否健康？         dashboard / overview / doctor / gate
  是否正在恢复？         observe
  Redis/worker 是否消费？ broker / runtime
  调用方流量是否变大？    ingress
  能不能加并发或 pod？    capacity
  单个 Job 卡在哪？       trace / inspect / diagnose

\b
关键参数：
  时间窗口只支持正整数 + 单位：30s、10m、24h、7d。
  --json 输出完整 JSON；./scripts/jobs.sh <command> -h 查看单命令参数。

\b
更多说明：
  ./scripts/jobs.sh guide

\b
副作用与保护边界：
  只读查询不修改 DB，不触发真实业务调用，不投递消息，不重试 Job，不重放 callback。
  单个 job_id 不存在时返回非 0；列表、summary 和 doctor 的空结果会在成功输出中明确说明。

\b
Exit Codes:
  0  成功
  2  参数非法或 DB 不可达
  3  查询对象不存在
  4  查询失败或证据不可达
"""

GUIDE_TEXT = """Job 排障命令骨架

先按问题找入口，不要先记命令名。

按问题找命令
  系统现在健康吗？
    首选：./scripts/jobs.sh dashboard --since 1h
    辅助：./scripts/jobs.sh overview --since 1h
    辅助：./scripts/jobs.sh doctor --since 1h
    明细：./scripts/jobs.sh summary --since 1h

  真正 active 积压是多少？
    首选：./scripts/jobs.sh gate
    辅助：./scripts/jobs.sh capacity --since 30m

  系统是否正在恢复？
    首选：./scripts/jobs.sh observe --interval 60 --samples 5
    辅助：./scripts/jobs.sh drain --since 30m --strict
    明细：./scripts/jobs.sh stuck --older-than 10m

  调用方流量和处理吞吐怎样？
    首选：./scripts/jobs.sh ingress --since 30m --bucket 1m
    辅助：./scripts/jobs.sh latency --since 30m

  能不能加并发或 pod？
    首选：./scripts/jobs.sh capacity --worker-pods 4 --worker-concurrency 30 --api-pods 2 --db-max-connections 100
    辅助：./scripts/jobs.sh runtime

  Redis/Taskiq 和 worker 是否真的在消费？
    首选：./scripts/jobs.sh broker
    首选：./scripts/jobs.sh runtime

  失败和 callback 是否集中异常？
    首选：./scripts/jobs.sh failures --since 1h
    首选：./scripts/jobs.sh callbacks-summary --since 1h

  单个 Job 卡在哪？
    首选：./scripts/jobs.sh trace <job_id>
    辅助：./scripts/jobs.sh inspect <job_id>
    明细：./scripts/jobs.sh timeline <job_id> --limit 50
    明细：./scripts/jobs.sh attempts <job_id>
    明细：./scripts/jobs.sh ai-calls <job_id>
    明细：./scripts/jobs.sh callbacks <job_id>

命令分级
  一级入口
    dashboard / overview / observe / broker / runtime / trace

  二级诊断
    doctor / gate / capacity / ingress / latency / failures / callbacks-summary / stuck / drain / pressure

  明细证据
    summary / list / inspect / diagnose / workflow / timeline / attempts / ai-calls / callbacks / payload

四层模型
  系统态
    dashboard / overview / doctor / gate / capacity / pressure / ingress
    看 active_jobs、queued、running_active、failed、dispatch_due、callback_due、stuck。

  恢复态
    observe / drain / stuck
    看 queued 或 active_jobs 是否下降、stuck 是否减少、failure 和 callback_due 是否停止增长。

  运输和运行时
    broker / runtime
    看 Redis key type、length、pending、consumer groups、WORKER_CONCURRENCY、Taskiq 进程、recovery loop、CPU/memory cgroup。

  单 Job 轨迹
    trace / inspect / diagnose / workflow / timeline / attempts / ai-calls / callbacks
    看 created -> queued -> dispatch published -> attempt claimed -> running heartbeat -> terminal -> callback delivered。

更多细节
  ./scripts/jobs.sh <command> -h
"""

app = typer.Typer(
    name="jobs.sh",
    help="Job 只读查询与排障入口。",
    epilog=HELP_EPILOG,
    no_args_is_help=False,
    invoke_without_command=True,
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


def _validate_run_id_option(value: str | None) -> str | None:
    if value is None:
        return None
    if not RUN_ID_RE.fullmatch(value):
        raise typer.BadParameter("must match [A-Za-z0-9][A-Za-z0-9_-]{0,127}")
    return value


RunIdOption = Annotated[
    str | None,
    typer.Option(
        "--run-id",
        help="按 metadata.run_id 过滤压测运行。",
        callback=_validate_run_id_option,
    ),
]
LimitOption = Annotated[
    int,
    typer.Option(
        "--limit",
        min=1,
        max=1000,
        help="返回条数，范围 1 到 1000。",
    ),
]
ScopeOption = Annotated[
    str,
    typer.Option(
        "--scope",
        help="Job 记录范围：root、child、family 或 all；默认按命令选择最常用口径。",
    ),
]
StuckScopeOption = Annotated[
    str,
    typer.Option(
        "--stuck-scope",
        help="stuck 样本记录范围：root、child、family 或 all；默认 family。",
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


def parse_record_scope(value: str) -> str:
    if value not in queries.VALID_RECORD_SCOPES:
        raise ValueError("无效 scope：" + value + "；可选值：" + ", ".join(sorted(queries.VALID_RECORD_SCOPES)))
    return value


def _jobs_columns() -> list[tuple[str, str]]:
    return [
        ("job_id", "job_id"),
        ("record_scope", "scope"),
        ("root_job_id", "root_job_id"),
        ("workflow_node_key", "node"),
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


def _deleted_job_columns() -> list[tuple[str, str]]:
    return [
        ("job_id", "job_id"),
        ("record_scope", "scope"),
        ("family_root_job_id", "family_root"),
        ("workflow_node_key", "node"),
        ("status", "status"),
        ("job_type", "job_type"),
        ("caller_id", "caller"),
        ("client_request_id", "client_request_id"),
        ("created_at", "created_at"),
        ("finished_at", "finished_at"),
        ("expires_at", "expires_at"),
        ("deleted_at", "deleted_at"),
        ("deleted_reason", "reason"),
    ]


def _deleted_summary_columns() -> list[tuple[str, str]]:
    return [
        ("total_deleted", "total_deleted"),
        ("root_deleted", "root_deleted"),
        ("child_deleted", "child_deleted"),
        ("family_count", "family_count"),
        ("oldest_deleted_at", "oldest_deleted_at"),
        ("newest_deleted_at", "newest_deleted_at"),
    ]


def _deleted_group_columns(group_key: str) -> list[tuple[str, str]]:
    return [(group_key, group_key), ("count", "count")]


def _deleted_key_columns() -> list[tuple[str, str]]:
    return [
        ("total_deleted", "total_deleted"),
        ("expired_deleted", "expired_deleted"),
    ]


def _deleted_inconsistency_columns() -> list[tuple[str, str]]:
    return [
        ("deleted_root_active_submission_keys", "deleted_root_active_keys"),
        ("active_root_deleted_submission_keys", "active_root_deleted_keys"),
        ("deleted_active_jobs", "deleted_active_jobs"),
        ("deleted_child_active_jobs", "deleted_child_active_jobs"),
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
        ("retry_eligible", "retry_eligible"),
        ("retry_decision", "retry_decision"),
        ("retry_decision_reason", "retry_reason"),
        ("policy_max_attempts", "policy_max"),
        ("policy_retryable_error_codes", "retryable_codes"),
    ]


def _ai_call_columns() -> list[tuple[str, str]]:
    return [
        ("id", "ai_call_id"),
        ("attempt_id", "attempt_id"),
        ("operation", "operation"),
        ("step_name", "step"),
        ("model_id", "model_id"),
        ("provider", "provider"),
        ("provider_model", "provider_model"),
        ("status", "status"),
        ("failure_phase", "failure_phase"),
        ("error_code", "error_code"),
        ("duration_ms", "duration_ms"),
        ("cost_amount", "cost"),
        ("billable_status", "billable"),
        ("started_at", "started_at"),
        ("completed_at", "completed_at"),
    ]


def _child_job_columns() -> list[tuple[str, str]]:
    return [
        ("workflow_node_key", "node"),
        ("job_id", "child_job_id"),
        ("status", "status"),
        ("job_type", "job_type"),
        ("progress_percent", "%"),
        ("progress_stage", "stage"),
        ("attempt_status", "attempt"),
        ("dispatch_status", "dispatch"),
        ("publish_attempts", "publish_attempts"),
        ("worker_id", "worker"),
        ("lease_expires_at", "lease_expires_at"),
        ("duration", "duration"),
        ("updated_at", "updated_at"),
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


def _ingress_columns() -> list[tuple[str, str]]:
    return [
        ("bucket_at", "bucket_at"),
        ("created", "created"),
        ("started", "started"),
        ("terminal", "terminal"),
        ("failed", "failed"),
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


def _job_inspect_columns() -> list[tuple[str, str]]:
    return [
        ("job_id", "job_id"),
        ("status", "status"),
        ("job_type", "job_type"),
        ("caller_id", "caller"),
        ("progress_percent", "%"),
        ("progress_stage", "stage"),
        ("callback_status", "callback"),
        ("created_at", "created_at"),
        ("started_at", "started_at"),
        ("finished_at", "finished_at"),
    ]


def _job_item_columns() -> list[tuple[str, str]]:
    return [
        ("item_id", "item"),
        ("language", "lang"),
        ("model_id", "model"),
        ("title_text", "title_text"),
        ("draw_count", "draws"),
        ("reference_sha256", "reference_sha256"),
    ]


def _workflow_node_columns() -> list[tuple[str, str]]:
    return [
        ("key", "node"),
        ("job_type", "job_type"),
        ("depends_on", "depends_on"),
        ("required", "required"),
        ("weight", "weight"),
    ]


def _result_item_columns() -> list[tuple[str, str]]:
    return [
        ("item_id", "item"),
        ("language", "lang"),
        ("status", "status"),
        ("image_count", "images"),
        ("error", "error"),
    ]


def _batch_summary_columns() -> list[tuple[str, str]]:
    return [
        ("total", "total"),
        ("succeeded", "succeeded"),
        ("failed", "failed"),
        ("running", "running"),
        ("pending", "pending"),
    ]


def _capacity_current_columns() -> list[tuple[str, str]]:
    return [
        ("active_jobs", "active_jobs"),
        ("queued", "queued"),
        ("running_active", "running_active"),
        ("max_active_jobs", "max_active_jobs"),
        ("active_ratio", "active_ratio"),
        ("headroom", "headroom"),
    ]


def _capacity_window_columns() -> list[tuple[str, str]]:
    return [
        ("accepted_jobs", "accepted_jobs"),
        ("terminal_jobs", "terminal_jobs"),
        ("lifecycle_p95_seconds", "lifecycle_p95_s"),
        ("accepted_submit_rps", "accepted_rps"),
        ("observed_span_seconds", "observed_span_s"),
        ("effective_window_seconds", "effective_window_s"),
    ]


def _capacity_estimated_columns() -> list[tuple[str, str]]:
    return [
        ("active_jobs_needed_upper_bound", "active_needed_upper"),
        ("active_ratio", "active_ratio"),
        ("headroom", "headroom"),
    ]


def _capacity_recommendation_columns() -> list[tuple[str, str]]:
    return [
        ("max_active_jobs", "max_active_jobs"),
        ("active_ratio", "active_ratio"),
        ("db_connection_risk", "db_risk"),
        ("message", "message"),
    ]


def _capacity_db_budget_columns() -> list[tuple[str, str]]:
    return [
        ("api_pods", "api_pods"),
        ("api_pool_per_pod", "api_pool_per_pod"),
        ("worker_pods", "worker_pods"),
        ("worker_concurrency", "worker_concurrency"),
        ("estimated_connections", "estimated_connections"),
        ("db_max_connections", "db_max_connections"),
        ("usable_connections", "usable_connections"),
        ("headroom", "headroom"),
        ("risk", "risk"),
    ]


def _capacity_db_budget_source_columns() -> list[tuple[str, str]]:
    return [
        ("api_pods", "api_pods"),
        ("worker_pods", "worker_pods"),
        ("worker_concurrency", "worker_concurrency"),
        ("db_pool_size", "db_pool_size"),
        ("db_max_overflow", "db_max_overflow"),
        ("db_max_connections", "db_max_connections"),
        ("db_usable_ratio", "db_usable_ratio"),
    ]


def _log_match_columns() -> list[tuple[str, str]]:
    return [
        ("name", "name"),
        ("count", "count"),
        ("sample", "sample"),
    ]


def _diagnosis_columns() -> list[tuple[str, str]]:
    return [
        ("severity", "severity"),
        ("area", "area"),
        ("signal", "signal"),
        ("message", "message"),
    ]


def _trace_phase_columns() -> list[tuple[str, str]]:
    return [
        ("phase", "phase"),
        ("status", "status"),
        ("from", "from"),
        ("to", "to"),
        ("duration_seconds", "duration_s"),
        ("signal", "signal"),
    ]


def _observe_columns() -> list[tuple[str, str]]:
    return [
        ("sample", "sample"),
        ("captured_at", "captured_at"),
        ("queued", "queued"),
        ("running_active", "running_active"),
        ("active_jobs", "active_jobs"),
        ("created", "created"),
        ("started", "started"),
        ("terminal", "terminal"),
        ("terminal_failed", "terminal_failed"),
        ("terminal_failed_rate", "failed_rate"),
        ("failed", "failed"),
        ("callback_due", "callback_due"),
        ("stuck", "stuck"),
        ("state", "state"),
    ]


def _broker_columns() -> list[tuple[str, str]]:
    return [
        ("broker_kind", "broker_kind"),
        ("redis_key", "redis_key"),
        ("redis_ping", "ping"),
        ("redis_key_type", "key_type"),
        ("length", "length"),
        ("pending", "pending"),
        ("lag", "lag"),
        ("oldest_message_age_seconds", "oldest_age_s"),
        ("verdict", "verdict"),
    ]


def _runtime_env_columns() -> list[tuple[str, str]]:
    return [
        ("WORKER_CONCURRENCY", "WORKER_CONCURRENCY"),
        ("WORKER_RECOVERY_LOOP", "WORKER_RECOVERY_LOOP"),
        ("TASKIQ_BROKER_KIND", "TASKIQ_BROKER_KIND"),
        ("MAX_ACTIVE_JOBS", "MAX_ACTIVE_JOBS"),
        ("DB_POOL_SIZE", "DB_POOL_SIZE"),
        ("DB_MAX_OVERFLOW", "DB_MAX_OVERFLOW"),
    ]


def _runtime_process_columns() -> list[tuple[str, str]]:
    return [
        ("name", "name"),
        ("count", "count"),
        ("sample", "sample"),
    ]


def _runtime_cgroup_columns() -> list[tuple[str, str]]:
    return [
        ("cpu_max", "cpu_max"),
        ("cpu_quota_cores", "cpu_quota_cores"),
        ("cpu_usage_usec", "cpu_usage_usec"),
        ("memory_current_bytes", "memory_current_bytes"),
        ("memory_max_bytes", "memory_max_bytes"),
    ]


def _callbacks_summary_columns() -> list[tuple[str, str]]:
    return [
        ("status", "status"),
        ("count", "count"),
        ("due", "due"),
        ("oldest_age_seconds", "oldest_age_s"),
        ("next_attempt_at", "next_attempt_at"),
        ("last_http_status_seen", "http_seen"),
        ("sample_last_error", "sample_error"),
    ]


def _job_summary(job: dict) -> dict[str, Any]:
    return {
        "job_id": str(job.get("id")),
        "status": job.get("status"),
        "job_type": job.get("job_type"),
        "caller_id": job.get("caller_id"),
        "client_request_id": job.get("client_request_id"),
        "progress_percent": job.get("progress_percent"),
        "progress_stage": job.get("progress_stage"),
        "callback_status": job.get("callback_status"),
        "attempt_status": job.get("attempt_status"),
        "dispatch_status": job.get("dispatch_status"),
        "publish_attempts": job.get("publish_attempts"),
        "worker_id": job.get("worker_id"),
        "lease_expires_at": job.get("lease_expires_at"),
        "duration": job.get("duration"),
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


def _payload_job_summary(job: dict) -> dict[str, Any]:
    return {
        "job_id": str(job.get("id") or job.get("job_id")),
        "root_job_id": job.get("root_job_id"),
        "workflow_node_key": job.get("workflow_node_key"),
        "status": job.get("status"),
        "job_type": job.get("job_type"),
        "caller_id": job.get("caller_id"),
        "client_request_id": job.get("client_request_id"),
        "progress_percent": job.get("progress_percent"),
        "progress_stage": job.get("progress_stage"),
        "callback_status": job.get("callback_status"),
        "attempt_status": job.get("attempt_status"),
        "dispatch_status": job.get("dispatch_status"),
        "publish_attempts": job.get("publish_attempts"),
        "worker_id": job.get("worker_id"),
        "lease_expires_at": job.get("lease_expires_at"),
        "duration": job.get("duration"),
        "progress": {
            "percent": job.get("progress_percent"),
            "stage": job.get("progress_stage"),
            "text": job.get("progress_text"),
        },
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "updated_at": job.get("updated_at"),
    }


def _child_job_summary(job: dict) -> dict[str, Any]:
    return {
        "workflow_node_key": job.get("workflow_node_key"),
        "job_id": str(job.get("job_id") or job.get("id")),
        "root_job_id": job.get("root_job_id"),
        "status": job.get("status"),
        "job_type": job.get("job_type"),
        "caller_id": job.get("caller_id"),
        "client_request_id": job.get("client_request_id"),
        "progress_percent": job.get("progress_percent"),
        "progress_stage": job.get("progress_stage"),
        "progress_text": job.get("progress_text"),
        "active_attempt_id": job.get("active_attempt_id"),
        "attempt_status": job.get("attempt_status"),
        "attempt_no": job.get("attempt_no"),
        "worker_id": job.get("worker_id"),
        "lease_expires_at": job.get("lease_expires_at"),
        "dispatch_status": job.get("dispatch_status"),
        "publish_attempts": job.get("publish_attempts"),
        "dispatch_next_attempt_at": job.get("dispatch_next_attempt_at"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
        "duration": job.get("duration"),
    }


def _payload_shape(value: Any) -> dict[str, Any]:
    if value is None:
        return {"present": False}
    if isinstance(value, dict):
        keys = list(value.keys())
        summary: dict[str, Any] = {
            "present": True,
            "type": "dict",
            "key_count": len(keys),
            "keys": keys[:20],
        }
        for key in ("items", "nodes", "results"):
            item = value.get(key)
            if isinstance(item, list):
                summary[f"{key}_count"] = len(item)
        return summary
    if isinstance(value, list):
        return {"present": True, "type": "list", "item_count": len(value)}
    if isinstance(value, str):
        return {"present": True, "type": "str", "length": len(value)}
    return {"present": True, "type": type(value).__name__}


def _ref_shape(value: Any) -> dict[str, Any]:
    if value is None:
        return {"present": False}
    if not isinstance(value, dict):
        return _payload_shape(value)
    return {
        "present": True,
        "type": value.get("type"),
        "name": value.get("name"),
        "storage": value.get("storage"),
        "content_hash": value.get("content_hash"),
        "content_size_bytes": value.get("content_size_bytes"),
        "payload": _payload_shape(value.get("payload")),
    }


def _payload_summary_evidence(job: dict) -> dict[str, Any]:
    return {
        "job_id": str(job.get("id") or job.get("job_id")),
        "root_job_id": job.get("root_job_id"),
        "workflow_node_key": job.get("workflow_node_key"),
        "status": job.get("status"),
        "job_type": job.get("job_type"),
        "caller_id": job.get("caller_id"),
        "client_request_id": job.get("client_request_id"),
        "metadata": _payload_shape(job.get("metadata")),
        "job_params": _payload_shape(_job_params_payload(job)),
        "job_params_ref": _ref_shape(job.get("job_params_ref")),
        "job_params_hash": job.get("job_params_hash"),
        "runtime_ref": _ref_shape(job.get("runtime_ref")),
        "result": _payload_shape(job.get("result")),
        "result_ref": _ref_shape(job.get("result_ref")),
        "canonical_result": _payload_shape(job.get("canonical_result")),
        "canonical_result_ref": _ref_shape(job.get("canonical_result_ref")),
        "error": _payload_shape(job.get("error")),
    }


def _job_inspect_row(job: dict) -> dict[str, Any]:
    return {
        "job_id": str(job.get("id")),
        "status": job.get("status"),
        "job_type": job.get("job_type"),
        "caller_id": job.get("caller_id"),
        "progress_percent": job.get("progress_percent"),
        "progress_stage": job.get("progress_stage"),
        "callback_status": job.get("callback_status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }


def _runtime_payload(job: dict) -> dict[str, Any]:
    runtime_ref = job.get("runtime_ref")
    if isinstance(runtime_ref, dict) and isinstance(runtime_ref.get("payload"), dict):
        return runtime_ref["payload"]
    return {}


def _job_params_payload(job: dict) -> dict[str, Any] | None:
    job_params_ref = job.get("job_params_ref")
    if isinstance(job_params_ref, dict) and isinstance(job_params_ref.get("payload"), dict):
        return job_params_ref["payload"]
    return None


def _workflow_plan(job: dict) -> dict[str, Any]:
    plan = _runtime_payload(job).get("workflow_plan")
    return plan if isinstance(plan, dict) else {}


def _job_param_items(job: dict) -> list[dict[str, Any]]:
    params = _job_params_payload(job)
    if not isinstance(params, dict):
        return []
    items = params.get("items")
    if not isinstance(items, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_options = item.get("model_options") if isinstance(item.get("model_options"), dict) else {}
        reference = item.get("reference_image") if isinstance(item.get("reference_image"), dict) else {}
        rows.append(
            {
                "item_id": item.get("item_id"),
                "language": item.get("language"),
                "model_id": item.get("model_id"),
                "title_text": item.get("title_text"),
                "draw_count": model_options.get("draw_count"),
                "reference_sha256": reference.get("sha256"),
            }
        )
    return rows


def _workflow_node_rows(job: dict) -> list[dict[str, Any]]:
    nodes = _workflow_plan(job).get("nodes")
    if not isinstance(nodes, list):
        return []
    rows: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        rows.append(
            {
                "key": node.get("key"),
                "job_type": node.get("job_type"),
                "depends_on": ",".join(node.get("depends_on") or []),
                "required": node.get("required"),
                "weight": node.get("weight"),
            }
        )
    return rows


def _result_items(job: dict) -> list[dict[str, Any]]:
    result = job.get("result")
    if not isinstance(result, dict):
        return []
    items = result.get("items")
    if not isinstance(items, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        images = item.get("images")
        rows.append(
            {
                "item_id": item.get("item_id"),
                "language": item.get("language"),
                "status": item.get("status"),
                "image_count": len(images) if isinstance(images, list) else 0,
                "error": item.get("error"),
            }
        )
    return rows


def _batch_summary(job: dict) -> dict[str, Any] | None:
    result = job.get("result")
    if not isinstance(result, dict):
        return None
    summary = result.get("batch_summary")
    return summary if isinstance(summary, dict) else None


def _workflow_outcome(job: dict) -> dict[str, Any] | None:
    summary = _canonical_result_summary(job.get("canonical_result"))
    workflow = summary.get("workflow") if isinstance(summary, dict) else None
    return workflow if isinstance(workflow, dict) else None


def _render_job_detail_sections(job: dict) -> None:
    items = _job_param_items(job)
    if items:
        formatters.section("Job Items")
        formatters.print_table(items, _job_item_columns())

    plan = _workflow_plan(job)
    workflow_nodes = _workflow_node_rows(job)
    if workflow_nodes:
        formatters.section("Workflow Plan")
        formatters.event(
            "OK",
            "workflow",
            "type=%s version=%s nodes=%s policy=%s"
            % (
                plan.get("workflow_type"),
                plan.get("workflow_version"),
                plan.get("node_count"),
                plan.get("failure_policy"),
            ),
        )
        formatters.print_table(workflow_nodes, _workflow_node_columns())

    batch_summary = _batch_summary(job)
    result_items = _result_items(job)
    workflow_outcome = _workflow_outcome(job)
    if batch_summary or result_items or workflow_outcome:
        formatters.section("Result Summary")
        if batch_summary:
            formatters.print_table([batch_summary], _batch_summary_columns())
        if workflow_outcome:
            formatters.event(
                "OK",
                "workflow",
                "outcome=%s succeeded=%s failed=%s nodes=%s"
                % (
                    workflow_outcome.get("outcome"),
                    workflow_outcome.get("succeeded"),
                    workflow_outcome.get("failed"),
                    workflow_outcome.get("node_count"),
                ),
            )
        if result_items:
            formatters.print_table(result_items, _result_item_columns())


def _render_capacity_human(payload: dict[str, Any], *, title: str = "Job Capacity") -> None:
    current = payload.get("current") or {}
    estimated = payload.get("estimated") or {}
    formatters.section(title)
    scope = payload.get("scope") or {}
    window_scope = scope.get("window") if isinstance(scope.get("window"), dict) else {}
    formatters.event(
        "OK",
        "capacity",
        "since=%s job_type=%s caller_id=%s run_id=%s"
        % (
            window_scope.get("since") or "-",
            window_scope.get("job_type") or "-",
            window_scope.get("caller_id") or "-",
            window_scope.get("run_id") or "-",
        ),
    )
    _render_gate_human(
        _gate_payload(current=current, max_active_jobs=payload.get("max_active_jobs")),
        title="当前全局 active 占用",
    )
    formatters.section("窗口容量估算")
    formatters.event(
        "OK",
        "window",
        "since=%s record_scope=%s job_type=%s caller_id=%s run_id=%s"
        % (
            window_scope.get("since") or "-",
            window_scope.get("record_scope") or "-",
            window_scope.get("job_type") or "-",
            window_scope.get("caller_id") or "-",
            window_scope.get("run_id") or "-",
        ),
    )
    formatters.print_table([payload.get("window") or {}], _capacity_window_columns())
    formatters.section("容量估算")
    formatters.print_table([estimated], _capacity_estimated_columns())
    db_budget = payload.get("db_connection_budget")
    if isinstance(db_budget, dict):
        formatters.section("DB 连接预算估算")
        formatters.print_table([db_budget], _capacity_db_budget_columns())
        input_sources = db_budget.get("input_sources")
        if isinstance(input_sources, dict):
            formatters.section("DB 连接预算输入来源")
            formatters.print_table([input_sources], _capacity_db_budget_source_columns())
        message = db_budget.get("message")
        if message:
            print(f"说明：{message}")
    recommendation = payload.get("recommendation")
    if isinstance(recommendation, dict):
        formatters.section("建议")
        formatters.print_table([recommendation], _capacity_recommendation_columns())


def _render_gate_human(payload: dict[str, Any], *, title: str = "当前全局 active 占用") -> None:
    formatters.section(title)
    formatters.event("OK", "gate", "scope=global_current")
    note = (payload.get("notes") or {}).get("scope")
    if note:
        print(f"说明：{note}")
    print("字段：active_jobs=queued + 正在执行的 running；active_ratio=active_jobs / MAX_ACTIVE_JOBS；headroom=剩余可接收余量。")
    formatters.print_table([payload.get("current") or {}], _capacity_current_columns())


def _log_match_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    matches = payload.get("matches") or {}
    samples = payload.get("samples") or {}
    rows = []
    for name, count in matches.items():
        sample_values = samples.get(name) if isinstance(samples, dict) else None
        sample = sample_values[-1] if isinstance(sample_values, list) and sample_values else None
        rows.append({"name": name, "count": count, "sample": sample})
    return rows


def _workflow_plan_summary(plan: Any) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    nodes = plan.get("nodes")
    node_summaries = []
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_summaries.append(
                {
                    "key": node.get("key"),
                    "job_type": node.get("job_type"),
                    "depends_on": node.get("depends_on"),
                    "required": node.get("required"),
                    "weight": node.get("weight"),
                }
            )
    return {
        "kind": plan.get("kind"),
        "workflow_type": plan.get("workflow_type"),
        "workflow_version": plan.get("workflow_version"),
        "failure_policy": plan.get("failure_policy"),
        "node_count": plan.get("node_count"),
        "max_nodes": plan.get("max_nodes"),
        "nodes": node_summaries,
    }


def _runtime_ref_summary(runtime_ref: Any) -> dict[str, Any] | None:
    if not isinstance(runtime_ref, dict):
        return None
    payload = runtime_ref.get("payload")
    if not isinstance(payload, dict):
        return formatters.trim_payload(runtime_ref, max_items=4, max_string_length=160)
    return {
        "name": runtime_ref.get("name"),
        "type": runtime_ref.get("type"),
        "storage": runtime_ref.get("storage"),
        "content_hash": runtime_ref.get("content_hash"),
        "content_size_bytes": runtime_ref.get("content_size_bytes"),
        "job_type": payload.get("job_type"),
        "output_target": formatters.trim_payload(payload.get("output_target"), max_items=8, max_string_length=160),
        "workflow_plan": _workflow_plan_summary(payload.get("workflow_plan")),
        "runtime_fields": formatters.trim_payload(payload.get("runtime_fields"), max_items=8, max_string_length=160),
        "job_params_hash": payload.get("job_params_hash"),
    }


def _canonical_result_summary(canonical_result: Any) -> dict[str, Any] | None:
    if not isinstance(canonical_result, dict):
        return None
    workflow = canonical_result.get("workflow")
    if not isinstance(workflow, dict):
        return formatters.trim_payload(canonical_result, max_items=4, max_string_length=160)
    return {
        "job_type": canonical_result.get("job_type"),
        "workflow": {
            "workflow_type": workflow.get("workflow_type"),
            "workflow_version": workflow.get("workflow_version"),
            "outcome": workflow.get("outcome"),
            "failure_policy": workflow.get("failure_policy"),
            "node_count": workflow.get("node_count"),
            "succeeded": workflow.get("succeeded"),
            "failed": workflow.get("failed"),
        },
    }


def _inspect_payload_summary(job: dict) -> dict[str, Any]:
    return {
        "job_params": formatters.trim_payload(_job_params_payload(job), max_items=4, max_string_length=160),
        "metadata": formatters.trim_payload(job.get("metadata"), max_items=8, max_string_length=160),
        "runtime_ref": _runtime_ref_summary(job.get("runtime_ref")),
        "result": formatters.trim_payload(job.get("result"), max_items=4, max_string_length=160),
        "result_ref": formatters.trim_payload(job.get("result_ref"), max_items=4, max_string_length=160),
        "canonical_result": _canonical_result_summary(job.get("canonical_result")),
        "canonical_result_ref": formatters.trim_payload(
            job.get("canonical_result_ref"),
            max_items=4,
            max_string_length=160,
        ),
        "error": formatters.trim_payload(job.get("error"), max_items=4, max_string_length=160),
        "callback_last_error": formatters.trim_payload(
            job.get("callback_last_error"),
            max_items=4,
            max_string_length=160,
        ),
    }


def _payload_evidence(job: dict) -> dict[str, Any]:
    return {
        "job_id": str(job.get("id") or job.get("job_id")),
        "root_job_id": job.get("root_job_id"),
        "workflow_node_key": job.get("workflow_node_key"),
        "status": job.get("status"),
        "job_type": job.get("job_type"),
        "caller_id": job.get("caller_id"),
        "client_request_id": job.get("client_request_id"),
        "metadata": job.get("metadata"),
        "job_params": _job_params_payload(job),
        "job_params_ref": job.get("job_params_ref"),
        "job_params_hash": job.get("job_params_hash"),
        "runtime_ref": job.get("runtime_ref"),
        "result": job.get("result"),
        "canonical_result": job.get("canonical_result"),
        "error": job.get("error"),
    }


def _payload_sections(evidence: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        ("Job Params", evidence.get("job_params")),
        ("Job Params Ref", evidence.get("job_params_ref")),
        ("Runtime Ref", evidence.get("runtime_ref")),
        ("Result", evidence.get("result")),
        ("Canonical Result", evidence.get("canonical_result")),
        ("Error", evidence.get("error")),
    ]


def _print_payload_value(value: Any) -> None:
    if value is None:
        print("null")
        return
    formatters.print_json(value)


def _render_payload_human(payload: dict[str, Any], *, include_children: bool, full: bool) -> None:
    job = payload["job"]
    formatters.section("Job Payload" if full else "Job Payload Summary")
    formatters.event(
        "OK",
        "job",
        f"job_id={job['job_id']} status={job.get('status') or '-'} job_type={job.get('job_type') or '-'} mode={payload.get('mode') or '-'}",
    )
    formatters.print_table([job], _job_inspect_columns())
    if full:
        for section_name, value in _payload_sections(payload["payload"]):
            formatters.section(section_name)
            _print_payload_value(value)
    else:
        formatters.section("Payload Summary")
        formatters.print_json(payload["payload"])
    if include_children:
        children = payload.get("children") or []
        formatters.section("Children Payloads" if full else "Children Payload Summaries")
        formatters.event("OK", "children", f"count={len(children)}")
        if not children:
            print("no workflow children")
            return
        for child in children:
            child_job = child["job"]
            formatters.section(f"Child {child_job['job_id']}")
            formatters.print_table([child_job], _child_job_columns())
            if full:
                for section_name, value in _payload_sections(child["payload"]):
                    formatters.section(section_name)
                    _print_payload_value(value)
            else:
                formatters.section("Payload Summary")
                formatters.print_json(child["payload"])


def _as_aware_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_due(value: Any, *, now: datetime) -> bool:
    due_at = _as_aware_utc(value)
    return due_at is None or due_at <= now


def _older_than(value: Any, *, now: datetime, older_than: timedelta) -> bool:
    started_at = _as_aware_utc(value)
    if started_at is None:
        return True
    return started_at <= now - older_than


def _event_types(timeline: list[dict]) -> set[str]:
    return {str(row.get("event_type")) for row in timeline if row.get("event_type") is not None}


def _retry_decision_evidence(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempt_id": attempt.get("id"),
        "attempt_no": attempt.get("attempt_no") or attempt.get("purpose_attempt_no"),
        "error_kind": attempt.get("error_kind"),
        "failure_phase": attempt.get("failure_phase"),
        "retry_eligible": attempt.get("retry_eligible"),
        "retry_decision": attempt.get("retry_decision"),
        "retry_decision_reason": attempt.get("retry_decision_reason"),
        "policy_max_attempts": attempt.get("policy_max_attempts"),
        "policy_retryable_error_codes": attempt.get("policy_retryable_error_codes"),
        "next_attempt_scheduled_at": attempt.get("next_attempt_scheduled_at"),
    }


def _retry_not_retried_message(attempt: dict[str, Any]) -> str:
    reason = attempt.get("retry_decision_reason")
    if reason == "policy_exhausted":
        return "failed attempt 未重试：已达到 retry policy 最大 attempt 数。"
    if reason == "not_retry_eligible":
        return "failed attempt 未重试：错误未被当前 retry policy 判定为可重试。"
    if reason == "dispatch_publish_exhausted":
        return "failed attempt 未重试：dispatch 发布重试已耗尽。"
    if reason == "force_mark_failed":
        return "failed attempt 未重试：运维动作强制标记失败。"
    if reason:
        return f"failed attempt 未重试：retry_decision_reason={reason}。"
    if attempt.get("retry_eligible") is False:
        return "failed attempt 未重试：retry_eligible=false，但未记录更细的 retry_decision_reason。"
    return "failed attempt 未重试：retry_decision=do_not_retry，但未记录更细的 retry_decision_reason。"


def _ai_call_summary(ai_calls: list[dict]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_error_code: dict[str, int] = {}
    for row in ai_calls:
        status = str(row.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        error_code = row.get("error_code")
        if error_code:
            key = str(error_code)
            by_error_code[key] = by_error_code.get(key, 0) + 1
    return {
        "total": len(ai_calls),
        "by_status": by_status,
        "by_error_code": by_error_code,
        "sample": [
            {
                "ai_call_id": row.get("id"),
                "attempt_id": row.get("attempt_id"),
                "operation": row.get("operation"),
                "step_name": row.get("step_name"),
                "model_id": row.get("model_id"),
                "provider": row.get("provider"),
                "provider_model": row.get("provider_model"),
                "status": row.get("status"),
                "failure_phase": row.get("failure_phase"),
                "error_code": row.get("error_code"),
                "duration_ms": row.get("duration_ms"),
                "cost_amount": row.get("cost_amount"),
                "billable_status": row.get("billable_status"),
            }
            for row in ai_calls[:5]
        ],
    }


def _diagnose_job(
    payload: dict[str, Any],
    *,
    include_children: bool,
    older_than: timedelta | None = None,
) -> dict[str, Any]:
    job = payload["job"]
    attempts = payload.get("attempts") or []
    ai_calls = payload.get("ai_calls") or []
    callbacks = payload.get("callbacks") or []
    timeline = payload.get("timeline") or []
    children = payload.get("children") or []
    job_id = str(job.get("id"))
    now = datetime.now(timezone.utc)
    older_than = older_than or timedelta(minutes=1)
    older_than_seconds = int(older_than.total_seconds())
    findings: list[dict[str, Any]] = []

    def add(severity: str, area: str, signal: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        findings.append(
            {
                "severity": severity,
                "area": area,
                "signal": signal,
                "message": message,
                "evidence": evidence or {},
            }
        )

    job_status = job.get("status")
    active_attempt_id = job.get("active_attempt_id")
    if job_status in {"queued", "running"} and active_attempt_id is None:
        if job_status == "running":
            if include_children:
                active_children = [row for row in children if row.get("status") in {"queued", "running"}]
                failed_children = [row for row in children if row.get("status") == "failed"]
                if failed_children:
                    add(
                        "critical",
                        "workflow",
                        "workflow_child_failed",
                        "root 正在等待 child 收敛，且存在 failed child。",
                        {"failed_children": len(failed_children), "sample": [_child_job_summary(row) for row in failed_children[:3]]},
                    )
                elif active_children:
                    add(
                        "info",
                        "workflow",
                        "job_waiting_children",
                        "root 没有 active attempt，正在等待 internal child Job。",
                        {"active_children": len(active_children), "sample": [_child_job_summary(row) for row in active_children[:3]]},
                    )
                else:
                    add("info", "workflow", "job_waiting_reconcile", "root 没有 active attempt，可能正在等待 workflow reconciler 收敛。")
            else:
                add(
                    "info",
                    "workflow",
                    "job_waiting_children_unchecked",
                    "running Job 没有 active attempt；如它是 workflow root，请用 workflow 命令查看 child 状态。",
                )
        else:
            add("critical", "job", "active_attempt_missing", "queued Job 缺少 active_attempt_id，worker 无法领取。")

    if job_status == "failed":
        add("warning", "job", "job_failed", "Job 已失败；需要原始错误 payload 时使用 payload --full。", {"error": _payload_shape(job.get("error"))})

    events = _event_types(timeline)
    for attempt in attempts:
        attempt_id = attempt.get("id")
        attempt_status = attempt.get("status")
        dispatch_status = attempt.get("dispatch_status")
        if dispatch_status == "dead_letter":
            add(
                "critical",
                "dispatch",
                "dispatch_dead_letter",
                "dispatch outbox 已 dead-letter，worker 任务发布路径已经失败。",
                {"attempt_id": attempt_id, "dispatch_last_error": attempt.get("dispatch_last_error")},
            )
        elif dispatch_status in {"pending", "retrying"} and _is_due(attempt.get("next_attempt_at"), now=now):
            reference_at = attempt.get("next_attempt_at") or attempt.get("created_at")
            stale = _older_than(reference_at, now=now, older_than=older_than)
            add(
                "warning" if stale else "info",
                "dispatch",
                "dispatch_due",
                "dispatch 到期但尚未成功发布；短时间内可能正常，超过阈值后检查 outbox 发布和 broker。",
                {
                    "attempt_id": attempt_id,
                    "dispatch_status": dispatch_status,
                    "next_attempt_at": attempt.get("next_attempt_at"),
                    "older_than_seconds": older_than_seconds,
                    "stale": stale,
                },
            )
        elif attempt_status == "pending" and dispatch_status == "published":
            signal = "published_dispatch_not_claimed"
            stale = _older_than(attempt.get("published_at"), now=now, older_than=older_than)
            severity = "warning" if stale and job_status in {"queued", "running"} else "info"
            add(
                severity,
                "claim",
                signal,
                "dispatch 已发布但 attempt 仍是 pending；短时间内可能正常，超过阈值或持续 stuck 时查 worker/broker 消费。",
                {
                    "attempt_id": attempt_id,
                    "published_at": attempt.get("published_at"),
                    "older_than_seconds": older_than_seconds,
                    "stale": stale,
                },
            )

        if attempt_status == "running":
            lease_expires_at = _as_aware_utc(attempt.get("lease_expires_at"))
            if lease_expires_at is not None and lease_expires_at <= now:
                add(
                    "critical",
                    "attempt",
                    "running_attempt_lease_expired",
                    "running attempt lease 已过期；检查 worker 心跳和 recovery。",
                    {"attempt_id": attempt_id, "lease_expires_at": attempt.get("lease_expires_at"), "worker_id": attempt.get("worker_id")},
                )
            else:
                add(
                    "info",
                    "attempt",
                    "active_attempt_running",
                    "attempt 正在运行；如长期无进展，继续看 worker 日志和外部依赖。",
                    {"attempt_id": attempt_id, "worker_id": attempt.get("worker_id"), "lease_expires_at": attempt.get("lease_expires_at")},
                )
        elif attempt_status == "failed":
            severity = "info" if job_status == "succeeded" else "warning"
            retry_evidence = _retry_decision_evidence(attempt)
            add(
                severity,
                "attempt",
                "attempt_failed",
                "存在 failed attempt；evidence 中包含 error_kind、failure_phase、retry_decision 和 retry policy 快照。",
                retry_evidence,
            )
            if attempt.get("retry_decision") == "do_not_retry" or attempt.get("retry_eligible") is False:
                add(
                    severity,
                    "attempt",
                    "attempt_not_retried",
                    _retry_not_retried_message(attempt),
                    retry_evidence,
                )

    if ai_calls:
        summary = _ai_call_summary(ai_calls)
        failed_ai_calls = summary["by_status"].get("failed", 0)
        pending_ai_calls = summary["by_status"].get("pending", 0)
        if failed_ai_calls:
            add(
                "info" if job_status == "succeeded" else "warning",
                "ai_call",
                "ai_call_ledger_failed",
                "AI call ledger 中存在 failed 调用；查看 ai-calls 明细可定位 provider、model、error_code 和 failure_phase。",
                summary,
            )
        elif pending_ai_calls and job_status in {"succeeded", "failed"}:
            add(
                "warning",
                "ai_call",
                "ai_call_ledger_pending_after_terminal",
                "Job 已终态但 AI call ledger 仍有 pending 记录；检查 ledger terminal update 或 recovery reconcile。",
                summary,
            )
        else:
            add(
                "info",
                "ai_call",
                "ai_call_ledger_present",
                "AI call ledger 已记录，可用 ai-calls 查看 provider、model、用量、成本和错误证据。",
                summary,
            )

    for callback in callbacks:
        callback_id = callback.get("id")
        callback_status = callback.get("status")
        if callback_status == "dead_letter":
            add(
                "critical",
                "callback",
                "callback_dead_letter",
                "callback delivery 已 dead-letter；Job 终态不受影响，但回调没有送达。",
                {"callback_id": callback_id, "last_error": callback.get("last_error")},
            )
        elif callback_status == "leased":
            lease_expires_at = _as_aware_utc(callback.get("lease_expires_at"))
            if lease_expires_at is not None and lease_expires_at <= now:
                add(
                    "critical",
                    "callback",
                    "callback_lease_expired",
                    "callback lease 已过期；检查 callback 投递和 recovery。",
                    {"callback_id": callback_id, "lease_expires_at": callback.get("lease_expires_at")},
                )
        elif callback_status in {"pending", "retrying"} and _is_due(callback.get("next_attempt_at"), now=now):
            reference_at = callback.get("next_attempt_at") or callback.get("created_at")
            stale = _older_than(reference_at, now=now, older_than=older_than)
            add(
                "warning" if stale else "info",
                "callback",
                "callback_due",
                "callback 已到期等待投递或重试；短时间内可能正常，超过阈值后检查 callback worker 和目标服务。",
                {
                    "callback_id": callback_id,
                    "status": callback_status,
                    "next_attempt_at": callback.get("next_attempt_at"),
                    "older_than_seconds": older_than_seconds,
                    "stale": stale,
                },
            )

    if "dispatch.published" in events and "attempt.claimed" not in events and job_status in {"queued", "running"}:
        add(
            "info",
            "timeline",
            "published_without_claim_event",
            "timeline 中已有 dispatch.published 但没有 attempt.claimed；结合 Attempts 表判断是否仍 pending。",
        )

    if include_children:
        child_dispatch_dead = [row for row in children if row.get("dispatch_status") == "dead_letter"]
        if child_dispatch_dead:
            add(
                "critical",
                "workflow",
                "child_dispatch_dead_letter",
                "存在 child Job 的 dispatch dead-letter。",
                {"count": len(child_dispatch_dead), "sample": [_child_job_summary(row) for row in child_dispatch_dead[:3]]},
            )

    if not findings:
        add("ok", "job", "no_obvious_risk", "当前 Job 证据没有明显 attempt、dispatch、callback 或 claim 风险。")

    severity_rank = {"critical": 3, "warning": 2, "info": 1, "ok": 0}
    worst = max((severity_rank.get(item["severity"], 0) for item in findings), default=0)
    status = {3: "critical", 2: "warning", 1: "info", 0: "ok"}[worst]
    next_checks: list[str] = []
    signals = {item["signal"] for item in findings}
    if signals & {"published_dispatch_not_claimed", "published_without_claim_event", "dispatch_due", "dispatch_dead_letter"}:
        next_checks.extend(
            [
                f"./scripts/jobs.sh timeline {job_id} --limit 100",
                f"./scripts/jobs.sh stuck --older-than 1m --limit 20",
                "tail -n 100 logs/worker.log",
            ]
        )
    if signals & {"running_attempt_lease_expired", "active_attempt_running", "attempt_failed"}:
        next_checks.append(f"./scripts/jobs.sh attempts {job_id}")
    if signals & {"attempt_failed", "attempt_not_retried", "ai_call_ledger_failed", "ai_call_ledger_pending_after_terminal", "ai_call_ledger_present"}:
        next_checks.append(f"./scripts/jobs.sh ai-calls {job_id}")
    if signals & {"callback_dead_letter", "callback_lease_expired", "callback_due"}:
        next_checks.append(f"./scripts/jobs.sh callbacks {job_id}")
    if signals & {"job_waiting_children", "workflow_child_failed", "child_dispatch_dead_letter", "job_waiting_children_unchecked"}:
        next_checks.append(f"./scripts/jobs.sh workflow {job_id} --events-limit 50")
    if status in {"critical", "warning"}:
        next_checks.append("docker compose logs --tail=100 postgres")

    deduped_checks: list[str] = []
    for check in next_checks:
        if check not in deduped_checks:
            deduped_checks.append(check)
    return {"status": status, "findings": findings, "next_checks": deduped_checks}


def _render_inspect_human(payload: dict[str, Any], *, include_children: bool) -> None:
    job = payload["job"]
    attempts = payload["attempts"]
    ai_calls = payload.get("ai_calls") or []
    callbacks = payload["callbacks"]
    timeline = payload["timeline"]
    formatters.section("Job Inspect")
    formatters.event(
        "OK",
        "job",
        "attempts=%s ai_calls=%s callbacks=%s timeline=%s"
        % (len(attempts), len(ai_calls), len(callbacks), len(timeline)),
    )
    formatters.print_table([_job_inspect_row(job)], _job_inspect_columns())

    _render_diagnosis_human(payload["diagnosis"], title="Diagnosis")

    formatters.section("Attempts")
    formatters.print_table(attempts, _attempt_columns(), empty_message="no attempts")
    formatters.section("AI Calls")
    formatters.print_table(ai_calls, _ai_call_columns(), empty_message="no ai calls")
    if callbacks:
        formatters.section("Callbacks")
        formatters.print_table(callbacks, _callback_columns(), empty_message="no callbacks")
    formatters.section("Timeline")
    formatters.print_table(timeline, _timeline_columns(), empty_message="no events")

    if include_children:
        formatters.section("Workflow Children")
        children = payload.get("children") or []
        formatters.event("OK", "children", f"count={len(children)}")
        formatters.print_table(children, _child_job_columns(), empty_message="no workflow children")


def _render_diagnosis_human(diagnosis: dict[str, Any], *, title: str = "Job Diagnosis") -> None:
    formatters.section(title)
    formatters.event(diagnosis["status"].upper(), "diagnosis", f"findings={len(diagnosis['findings'])}")
    rows = [
        {
            "severity": item["severity"],
            "area": item["area"],
            "signal": item["signal"],
            "message": item["message"],
        }
        for item in diagnosis["findings"]
    ]
    formatters.print_table(rows, _diagnosis_columns())
    if diagnosis.get("next_checks"):
        formatters.section("Next Checks")
        for item in diagnosis["next_checks"]:
            print(f"- {item}")


def _render_ai_calls_human(ai_calls: list[dict]) -> None:
    formatters.section("AI Calls")
    formatters.print_table(ai_calls, _ai_call_columns(), empty_message="no ai calls")


def _inspect_json_payload(payload: dict[str, Any], *, include_children: bool) -> dict[str, Any]:
    result = {
        "job": _job_summary(payload["job"]),
        "attempts": payload["attempts"],
        "ai_calls": payload.get("ai_calls") or [],
        "callbacks": payload["callbacks"],
        "timeline": payload["timeline"],
        "diagnosis": payload["diagnosis"],
    }
    if include_children:
        result["children"] = [_child_job_summary(child) for child in payload.get("children") or []]
    return result


def _workflow_json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_job": _job_summary(payload["source_job"]),
        "root_job": _job_summary(payload["root_job"]),
        "children": [_child_job_summary(child) for child in payload.get("children") or []],
        "attempts": payload["attempts"],
        "callbacks": payload["callbacks"],
        "timeline": payload["timeline"],
        "diagnosis": payload["diagnosis"],
    }


def _event_time(timeline: list[dict], event_type: str) -> datetime | None:
    for row in timeline:
        if row.get("event_type") == event_type:
            return _as_aware_utc(row.get("created_at"))
    return None


def _duration_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


def _phase_row(phase: str, status: str, start: datetime | None, end: datetime | None, signal: str) -> dict[str, Any]:
    return {
        "phase": phase,
        "status": status,
        "from": start,
        "to": end,
        "duration_seconds": _duration_seconds(start, end),
        "signal": signal,
    }


def _job_trace_payload(payload: dict[str, Any], *, include_children: bool) -> dict[str, Any]:
    job = payload["job"]
    attempts = payload.get("attempts") or []
    callbacks = payload.get("callbacks") or []
    timeline = payload.get("timeline") or []
    now = datetime.now(timezone.utc)
    created_at = _as_aware_utc(job.get("created_at"))
    queued_at = _as_aware_utc(job.get("queued_at")) or created_at
    started_at = _as_aware_utc(job.get("started_at"))
    finished_at = _as_aware_utc(job.get("finished_at"))
    dispatch_published_at = _event_time(timeline, "dispatch.published")
    attempt_claimed_at = _event_time(timeline, "attempt.claimed")
    if dispatch_published_at is None:
        dispatch_published_at = min(
            (_as_aware_utc(row.get("published_at")) for row in attempts if row.get("published_at")),
            default=None,
        )
    if attempt_claimed_at is None:
        attempt_claimed_at = min(
            (
                value
                for value in (
                    _as_aware_utc(row.get("leased_at")) or _as_aware_utc(row.get("started_at"))
                    for row in attempts
                )
                if value is not None
            ),
            default=None,
        )
    if attempt_claimed_at is None and started_at is not None:
        attempt_claimed_at = started_at
    if dispatch_published_at is None and attempt_claimed_at is not None:
        dispatch_published_at = attempt_claimed_at
    callback_created_at = _event_time(timeline, "callback.created")
    callback_delivered_at = _event_time(timeline, "callback.delivered")
    callback_dead_lettered_at = _event_time(timeline, "callback.dead_letter")
    callback_settled_at = callback_delivered_at or callback_dead_lettered_at

    phases = [
        _phase_row("accepted", "done" if queued_at else "unknown", created_at, queued_at, "job_created"),
        _phase_row(
            "dispatch_wait",
            "done" if dispatch_published_at else "waiting" if job.get("status") in {"queued", "running"} else "unknown",
            queued_at,
            dispatch_published_at or (now if job.get("status") in {"queued", "running"} else None),
            "dispatch.published" if dispatch_published_at else "dispatch_not_published",
        ),
        _phase_row(
            "claim_wait",
            "done" if attempt_claimed_at else "waiting" if dispatch_published_at and job.get("status") in {"queued", "running"} else "unknown",
            dispatch_published_at,
            attempt_claimed_at or (now if dispatch_published_at and job.get("status") in {"queued", "running"} else None),
            "attempt.claimed" if attempt_claimed_at else "attempt_not_claimed",
        ),
        _phase_row(
            "running",
            "done" if finished_at else "running" if started_at or attempt_claimed_at else "not_started",
            started_at or attempt_claimed_at,
            finished_at or (now if job.get("status") == "running" else None),
            "job_terminal" if finished_at else "active_execution",
        ),
        _phase_row(
            "callback",
            "done" if callback_delivered_at else "failed" if callback_dead_lettered_at else "waiting" if callback_created_at else "not_configured",
            callback_created_at or finished_at,
            callback_settled_at or (now if callback_created_at else None),
            "callback.delivered" if callback_delivered_at else "callback.dead_letter" if callback_dead_lettered_at else "callback_pending",
        ),
    ]

    current_phase = next((row for row in phases if row["status"] in {"waiting", "running", "failed"}), phases[-1])
    worker_ids = sorted({str(row.get("worker_id")) for row in attempts if row.get("worker_id")})
    trace = {
        "job": _job_summary(job),
        "current": {
            "phase": current_phase["phase"],
            "status": current_phase["status"],
            "signal": current_phase["signal"],
        },
        "phases": phases,
        "attempts": {
            "count": len(attempts),
            "worker_ids": worker_ids,
            "latest_status": attempts[-1].get("status") if attempts else None,
            "latest_heartbeat_at": max((row.get("heartbeat_at") for row in attempts if row.get("heartbeat_at")), default=None),
        },
        "callbacks": {
            "count": len(callbacks),
            "latest_status": callbacks[0].get("status") if callbacks else None,
            "latest_http_status": callbacks[0].get("last_http_status") if callbacks else None,
        },
        "diagnosis": payload["diagnosis"],
    }
    if include_children:
        children = payload.get("children") or []
        trace["children"] = {
            "count": len(children),
            "active": len([row for row in children if row.get("status") in {"queued", "running"}]),
            "failed": len([row for row in children if row.get("status") == "failed"]),
            "sample": [_child_job_summary(row) for row in children[:10]],
        }
    return trace


def _render_trace_human(payload: dict[str, Any]) -> None:
    formatters.section("Job Trace")
    job = payload["job"]
    current = payload["current"]
    formatters.event(
        payload["diagnosis"]["status"].upper(),
        "trace",
        "job_id=%s status=%s current_phase=%s signal=%s"
        % (job.get("job_id"), job.get("status"), current.get("phase"), current.get("signal")),
    )
    formatters.section("Phases")
    formatters.print_table(payload["phases"], _trace_phase_columns())
    formatters.section("Runtime Evidence")
    formatters.print_table([payload["attempts"]], [("count", "attempts"), ("worker_ids", "workers"), ("latest_status", "latest_attempt"), ("latest_heartbeat_at", "heartbeat")])
    formatters.section("Callback Evidence")
    formatters.print_table([payload["callbacks"]], [("count", "callbacks"), ("latest_status", "latest_callback"), ("latest_http_status", "http")])
    if "children" in payload:
        formatters.section("Workflow Children")
        formatters.print_table([payload["children"]], [("count", "count"), ("active", "active"), ("failed", "failed"), ("sample", "sample")])
    _render_diagnosis_human(payload["diagnosis"], title="Trace Diagnosis")


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


def _filter_job_type_specs(
    specs: list[dict[str, Any]],
    *,
    all_types: bool,
    visibility: str | None,
    role: str | None,
    default_human_catalog: bool,
) -> tuple[list[dict[str, Any]], dict[str, str | bool | None]]:
    from app.jobs.base import JOB_TYPE_ROLES, JOB_TYPE_VISIBILITIES

    if visibility is not None and visibility not in JOB_TYPE_VISIBILITIES:
        raise typer.BadParameter(
            f"visibility must be one of: {', '.join(sorted(JOB_TYPE_VISIBILITIES))}",
            param_hint="--visibility",
        )
    if role is not None and role not in JOB_TYPE_ROLES:
        raise typer.BadParameter(
            f"role must be one of: {', '.join(sorted(JOB_TYPE_ROLES))}",
            param_hint="--role",
        )

    filtered = specs
    applied: dict[str, str | bool | None] = {
        "all": all_types,
        "visibility": visibility,
        "role": role,
        "default_human_catalog": False,
    }
    if visibility is not None:
        filtered = [spec for spec in filtered if spec.get("visibility") == visibility]
    if role is not None:
        filtered = [spec for spec in filtered if spec.get("role") == role]
    if not all_types and visibility is None and role is None and default_human_catalog:
        filtered = [
            spec
            for spec in filtered
            if spec.get("visibility") != "internal" and spec.get("role") == "root"
        ]
        applied["visibility"] = "not_internal"
        applied["role"] = "root"
        applied["default_human_catalog"] = True
    return filtered, applied


def _render_result(*, section: str, target: str, rows: list[dict], columns: list[tuple[str, str]]) -> None:
    formatters.section(section)
    formatters.event("OK", target, f"count={len(rows)}")
    formatters.print_table(rows, columns)


def _render_jobs_result(*, rows: list[dict], scope: dict[str, Any]) -> None:
    formatters.section("Jobs")
    statuses = scope.get("statuses") or []
    status_text = ",".join(statuses) if statuses else "-"
    formatters.event(
        "OK",
        "jobs",
        "count=%s since=%s scope=%s status=%s job_type=%s caller_id=%s"
        % (
            len(rows),
            scope.get("since") or "all",
            scope.get("record_scope") or "-",
            status_text,
            scope.get("job_type") or "-",
            scope.get("caller_id") or "-",
        ),
    )
    formatters.print_table(rows, _jobs_columns())


def _render_deleted_summary(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    formatters.section("Deleted Job Summary")
    formatters.event(
        "OK",
        "deleted",
        "since_deleted=%s scope=%s job_type=%s caller_id=%s"
        % (
            scope.get("since_deleted") or "all",
            scope.get("record_scope") or "-",
            scope.get("job_type") or "-",
            scope.get("caller_id") or "-",
        ),
    )
    summary = payload["summary"]
    formatters.section("Counts")
    formatters.print_table([summary.get("counts") or {}], _deleted_summary_columns())
    formatters.section("By Reason")
    formatters.print_table(summary.get("by_reason") or [], _deleted_group_columns("deleted_reason"))
    formatters.section("By Status")
    formatters.print_table(summary.get("by_status") or [], _deleted_group_columns("status"))
    formatters.section("By Job Type")
    formatters.print_table(summary.get("by_job_type") or [], _deleted_group_columns("job_type"))
    formatters.section("Submission Keys")
    formatters.print_table([summary.get("submission_keys") or {}], _deleted_key_columns())
    formatters.section("Consistency")
    formatters.print_table([summary.get("inconsistencies") or {}], _deleted_inconsistency_columns())


def _render_deleted_jobs_result(*, rows: list[dict], scope: dict[str, Any]) -> None:
    formatters.section("Deleted Jobs")
    formatters.event(
        "OK",
        "deleted",
        "count=%s since_deleted=%s scope=%s job_type=%s caller_id=%s"
        % (
            len(rows),
            scope.get("since_deleted") or "all",
            scope.get("record_scope") or "-",
            scope.get("job_type") or "-",
            scope.get("caller_id") or "-",
        ),
    )
    formatters.print_table(rows, _deleted_job_columns())


def _retry_policy_summary(spec: dict[str, Any]) -> str:
    retry_policy = spec.get("retry_policy")
    if not isinstance(retry_policy, dict):
        return "-"
    parts: list[str] = []
    for key, label in (("business_execution", "business"), ("workflow_orchestration", "orchestration")):
        policy = retry_policy.get(key)
        if isinstance(policy, dict):
            parts.append(f"{label}:{policy.get('max_attempts', '-')}")
    return ", ".join(parts) if parts else "-"


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


def _env_int(name: str, *, min_value: int | None = None) -> int | None:
    value, _source = _env_int_with_source(name, min_value=min_value)
    return value


def _env_int_with_source(name: str, *, min_value: int | None = None) -> tuple[int | None, str]:
    raw, source = db.env_value_with_source(name)
    if raw is None or raw.strip() == "":
        return None, source
    try:
        value = int(raw)
    except ValueError:
        print(f"ERROR: invalid {name}: {raw}", file=sys.stderr)
        raise typer.Exit(2)
    if min_value is not None and value < min_value:
        print(f"ERROR: invalid {name}: {raw}; expected >= {min_value}", file=sys.stderr)
        raise typer.Exit(2)
    return value, source


def _capacity_db_budget(
    *,
    api_pods: int | None,
    worker_pods: int | None,
    worker_concurrency: int | None,
    db_pool_size: int | None,
    db_max_overflow: int | None,
    db_max_connections: int | None,
    db_usable_ratio: float,
) -> dict[str, Any]:
    if worker_concurrency is not None:
        resolved_worker_concurrency, worker_concurrency_source = worker_concurrency, "cli"
    else:
        resolved_worker_concurrency, worker_concurrency_source = _env_int_with_source("WORKER_CONCURRENCY", min_value=1)
    if db_pool_size is not None:
        resolved_db_pool_size, db_pool_size_source = db_pool_size, "cli"
    else:
        resolved_db_pool_size, db_pool_size_source = _env_int_with_source("DB_POOL_SIZE", min_value=1)
    if db_max_overflow is not None:
        resolved_db_max_overflow, db_max_overflow_source = db_max_overflow, "cli"
    else:
        resolved_db_max_overflow, db_max_overflow_source = _env_int_with_source("DB_MAX_OVERFLOW", min_value=0)
    input_sources = {
        "api_pods": "cli" if api_pods is not None else "missing",
        "worker_pods": "cli" if worker_pods is not None else "missing",
        "worker_concurrency": worker_concurrency_source,
        "db_pool_size": db_pool_size_source,
        "db_max_overflow": db_max_overflow_source,
        "db_max_connections": "cli" if db_max_connections is not None else "missing",
        "db_usable_ratio": "cli_or_default",
    }
    resolved_inputs = {
        "api_pods": api_pods,
        "worker_pods": worker_pods,
        "worker_concurrency": resolved_worker_concurrency,
        "db_pool_size": resolved_db_pool_size,
        "db_max_overflow": resolved_db_max_overflow,
        "db_max_connections": db_max_connections,
    }
    missing_inputs = sorted(key for key, value in resolved_inputs.items() if value is None)
    api_pool_per_pod = (
        resolved_db_pool_size + resolved_db_max_overflow
        if resolved_db_pool_size is not None and resolved_db_max_overflow is not None
        else None
    )
    worker_slots = (
        worker_pods * resolved_worker_concurrency
        if worker_pods is not None and resolved_worker_concurrency is not None
        else None
    )
    estimated_connections = (
        (api_pods * api_pool_per_pod) + worker_slots
        if api_pods is not None and api_pool_per_pod is not None and worker_slots is not None
        else None
    )
    usable_connections = int(db_max_connections * db_usable_ratio) if db_max_connections is not None else None
    headroom = (
        usable_connections - estimated_connections
        if usable_connections is not None and estimated_connections is not None
        else None
    )
    if estimated_connections is None or usable_connections is None:
        risk = "unknown"
        message = "缺少 %s，无法估算 DB 连接预算。" % ", ".join(missing_inputs or ["必要输入"])
    elif estimated_connections > usable_connections:
        risk = "critical"
        message = "估算连接数超过可用连接预算；不要继续提高 API/worker 并发，先治理 DB 连接。"
    elif estimated_connections >= int(usable_connections * 0.8):
        risk = "warning"
        message = "估算连接数接近可用连接预算；提高并发前先确认 PostgreSQL 实际连接和等待情况。"
    else:
        risk = "ok"
        message = "估算连接预算仍有余量；仍需结合实际 PostgreSQL 连接数、CPU 和慢查询判断。"
    return {
        "api_pods": api_pods,
        "api_pool_per_pod": api_pool_per_pod,
        "worker_pods": worker_pods,
        "worker_concurrency": resolved_worker_concurrency,
        "worker_slots": worker_slots,
        "db_pool_size": resolved_db_pool_size,
        "db_max_overflow": resolved_db_max_overflow,
        "db_max_connections": db_max_connections,
        "db_usable_ratio": db_usable_ratio,
        "usable_connections": usable_connections,
        "estimated_connections": estimated_connections,
        "headroom": headroom,
        "risk": risk,
        "message": message,
        "missing_inputs": missing_inputs,
        "input_sources": input_sources,
    }


def _capacity_recommendation(payload: dict[str, Any], max_active_jobs: int | None) -> dict[str, Any]:
    active_jobs = int(payload["current"].get("active_jobs") or 0)
    needed = payload["estimated"].get("active_jobs_needed_upper_bound")
    active_ratio = active_jobs / max_active_jobs if max_active_jobs and max_active_jobs > 0 else None
    db_budget = payload.get("db_connection_budget") if isinstance(payload, dict) else None
    db_risk = db_budget.get("risk") if isinstance(db_budget, dict) else None
    if db_risk == "critical":
        message = "DB 连接预算已超限；不要提高 WORKER_CONCURRENCY、API pod 或 worker pod，先治理连接预算。"
    elif db_risk == "warning":
        message = "DB 连接预算接近上限；提高并发或 pod 前，先确认 PostgreSQL 实际连接、等待和慢查询。"
    elif max_active_jobs is None:
        message = "未提供 MAX_ACTIVE_JOBS，无法判断当前 active 占用比例。"
    elif max_active_jobs == 0:
        message = "MAX_ACTIVE_JOBS=0 表示不限制 active 占用；生产不建议用它做容量保护。"
    elif active_ratio is not None and active_ratio >= 1:
        message = "当前 active 已达到或超过 MAX_ACTIVE_JOBS；若组件健康且可排空，才小步提高这个上限。"
    elif active_ratio is not None and active_ratio >= 0.8:
        message = "当前 active 接近 MAX_ACTIVE_JOBS；先确认 queued/running 可排空和 DB/Redis/worker 健康。"
    elif needed is not None and max_active_jobs is not None and needed > max_active_jobs:
        message = "按当前窗口生命周期上界估算，业务 active 需求可能高于 MAX_ACTIVE_JOBS；先确认环境硬上限，再调整。"
    else:
        message = "当前窗口未显示 active 占用压力；继续结合延迟、失败率和排空趋势判断。"
    return {
        "max_active_jobs": max_active_jobs,
        "active_ratio": active_ratio,
        "db_connection_risk": db_risk,
        "message": message,
    }


def _gate_payload(*, current: dict[str, Any], max_active_jobs: int | None) -> dict[str, Any]:
    current_row = dict(current)
    active_jobs = int(current_row.get("active_jobs") or 0)
    if max_active_jobs is not None and max_active_jobs > 0:
        active_ratio = active_jobs / max_active_jobs
        headroom = max_active_jobs - active_jobs
    else:
        active_ratio = None
        headroom = None
    current_row["max_active_jobs"] = max_active_jobs
    current_row["active_ratio"] = active_ratio
    current_row["headroom"] = headroom
    return {
        "scope": {
            "current": "global_gate",
            "window": "none",
            "filters": "none",
        },
        "current": current_row,
        "notes": {
            "scope": "当前全局 active 占用；不使用时间窗口，也不按 job_type/caller_id 过滤。",
            "active_jobs": "queued + running 且 active_attempt_id 非空。",
        },
    }


def _count(value: Any) -> int:
    return int(value or 0)


def _summary_payload(
    *,
    since: str,
    window: timedelta,
    job_type: str | None,
    caller_id: str | None,
    record_scope: str,
    run_id: str | None = None,
    summary_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scope": {
            "since": since,
            "seconds": window.total_seconds(),
            "job_type": job_type,
            "caller_id": caller_id,
            "run_id": run_id,
            "record_scope": record_scope,
            "query_scopes": summary_payload.get("query_scopes") or {},
        },
        **summary_payload,
    }


def _summary_next_checks(scope: dict[str, Any], *, no_jobs_found: bool) -> list[str]:
    filters = []
    if scope.get("job_type"):
        filters.append(f"--job-type {scope['job_type']}")
    if scope.get("caller_id"):
        filters.append(f"--caller-id {scope['caller_id']}")
    if scope.get("run_id"):
        filters.append(f"--run-id {scope['run_id']}")
    filter_text = (" " + " ".join(filters)) if filters else ""
    checks = [
        f"./scripts/jobs.sh list --since {scope['since']}{filter_text} --limit 20",
        f"./scripts/jobs.sh drain --since {scope['since']}{filter_text}",
        "./scripts/jobs.sh job <job_id>",
    ]
    if no_jobs_found:
        checks.insert(0, f"扩大 --since 窗口后重试，例如 ./scripts/jobs.sh doctor --since 1h{filter_text}")
        checks.append("./scripts/run.sh status dev")
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


def _fetch_summary_payload(
    *,
    since: str,
    job_type: str | None,
    caller_id: str | None,
    run_id: str | None = None,
    record_scope: str = "root",
) -> dict[str, Any]:
    window, since_at = _since_window(since)
    execution_scope = "family" if record_scope == "root" else record_scope
    raw_payload = _with_connection(
        lambda conn: queries.summary(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            run_id=run_id,
            since=since_at,
            record_scope=record_scope,
            execution_scope=execution_scope,
        )
    )
    return _summary_payload(
        since=since,
        window=window,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=record_scope,
        summary_payload=raw_payload,
    )


def _render_summary(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    query_scopes = scope.get("query_scopes") or {}
    jobs = payload.get("jobs") or {}
    no_jobs_found = _count(jobs.get("total")) == 0

    formatters.section("Job 窗口汇总")
    formatters.event(
        "OK",
        "summary",
        f"since={scope['since']} record_scope={scope.get('record_scope') or '-'} job_type={scope.get('job_type') or '-'} caller_id={scope.get('caller_id') or '-'} run_id={scope.get('run_id') or '-'}",
    )
    if no_jobs_found:
        print("no jobs found in the selected window.")
    formatters.section("Jobs")
    formatters.event("OK", "scope", f"since={scope['since']} record_scope={query_scopes.get('jobs') or scope.get('record_scope') or '-'}")
    formatters.print_table([jobs], _summary_job_columns())
    formatters.section("Attempts")
    formatters.event("OK", "scope", f"since={scope['since']} record_scope={query_scopes.get('attempts') or '-'}")
    formatters.print_table([payload.get("attempts") or {}], _summary_attempt_columns())
    formatters.section("Dispatch")
    formatters.event("OK", "scope", f"since={scope['since']} record_scope={query_scopes.get('dispatch') or '-'}")
    formatters.print_table([payload.get("dispatch") or {}], _summary_dispatch_columns())
    formatters.section("Callbacks")
    formatters.event("OK", "scope", f"since={scope['since']} record_scope={query_scopes.get('callbacks') or '-'}")
    formatters.print_table([payload.get("callbacks") or {}], _summary_callback_columns())
    formatters.section("By Job Type")
    formatters.event("OK", "scope", f"since={scope['since']} record_scope={query_scopes.get('by_job_type') or scope.get('record_scope') or '-'}")
    formatters.print_table(payload.get("by_job_type") or [], _summary_by_job_type_columns(), empty_message="no jobs found")
    if no_jobs_found:
        formatters.section("下一步检查")
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
    run_id: str | None,
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
        + (f" --run-id {run_id}" if run_id else "")
    )
    active_list_command = (
        f"./scripts/jobs.sh list --status queued,running --scope family{filters} --limit 20"
        if current_active > window_active
        else f"./scripts/jobs.sh list --status queued,running --scope family --since {since}{filters} --limit 20"
    )
    return {
        "scope": {
            "since": since,
            "older_than": older_than,
            "job_type": job_type,
            "caller_id": caller_id,
            "run_id": run_id,
            "query_scopes": raw_payload.get("query_scopes") or {},
        },
        **raw_payload,
        "status": status,
        "message": message,
        "next_checks": [
            f"./scripts/jobs.sh summary --since {since}{filters}",
            f"./scripts/jobs.sh capacity --since {since}{filters}",
            f"./scripts/jobs.sh stuck --older-than {older_than} --since {since}{filters}",
            active_list_command,
            f"./scripts/jobs.sh list --status failed --scope family --since {since}{filters} --limit 20",
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
            f" run_id={scope.get('run_id') or '-'}"
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


def _system_state_from_bottlenecks(status: str, bottlenecks: list[dict[str, Any]]) -> str:
    signals = {str(item.get("signal")) for item in bottlenecks}
    if signals & {"dispatch_dead_letter", "job_failures", "http_5xx", "db_connection_pressure", "api_log_db_connection_pressure"}:
        return "degraded"
    if signals & {"callback_dead_letter", "callback_due", "callback_lease_expired", "terminal_callback_not_settled"}:
        return "callback_backlog"
    if signals & {"published_dispatch_not_claimed", "dispatch_due_not_published", "queue_wait_high"}:
        return "worker_or_broker_lag"
    if signals & {"running_attempt_lease_expired", "run_time_high"}:
        return "execution_slow"
    if signals & {"active_gate_saturated", "active_gate_near_limit", "estimated_need_exceeds_limit"}:
        return "capacity_pressure"
    if signals & {"window_empty_but_global_active"}:
        return "old_active_remaining"
    if status == "ok":
        return "healthy"
    return status


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
    payload: dict[str, Any],
    run_id: str | None = None,
    locust: dict[str, Any] | None = None,
    api_log: dict[str, Any] | None = None,
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
        if active_jobs > 0:
            add(
                "info",
                "scope",
                "window_empty_but_global_active",
                "最近窗口内没有 root Job，但当前全局 active 占用仍不为 0；这些任务可能创建于窗口外。",
                {"active_jobs": active_jobs},
            )

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
                "Locust 失败主要是 HTTP 503，若响应体含 active_jobs/limit 且后台可排空，可判定 MAX_ACTIVE_JOBS 保护生效。",
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
                "当前全局 active 占用已达到或超过 MAX_ACTIVE_JOBS，POST /jobs 可能开始返回 503。",
                {"active_jobs": active_jobs, "max_active_jobs": max_active_jobs, "active_ratio": active_ratio},
            )
        elif active_ratio >= 0.8:
            add(
                "warning",
                "capacity",
                "active_gate_near_limit",
                "当前全局 active 占用接近 MAX_ACTIVE_JOBS，继续加压前先确认可排空。",
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
        + (f" --run-id {run_id}" if run_id else "")
    )
    return {
        "scope": {
            "since": since,
            "older_than": older_than,
            "job_type": job_type,
            "caller_id": caller_id,
            "run_id": run_id,
            "queue_wait_warning_seconds": queue_wait_warning_seconds,
            "run_warning_seconds": run_warning_seconds,
        },
        "status": status,
        "system_state": _system_state_from_bottlenecks(status, bottlenecks),
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
            "./scripts/jobs.sh gate",
            f"./scripts/jobs.sh drain --since {since} --older-than {older_than}{filters} --strict",
            f"./scripts/jobs.sh list --status failed --scope family --since {since}{filters} --limit 20",
            f"./scripts/jobs.sh stuck --since {since} --older-than {older_than}{filters} --limit 20",
            f"./scripts/jobs.sh latency --since {since}{filters} --group-by job_type",
            f"./scripts/jobs.sh list --status queued,running --scope family{filters} --limit 20",
            "./scripts/run.sh status dev",
        ],
    }


def _render_pressure(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    formatters.section("Job Pressure Diagnosis")
    formatters.event(
        payload["status"].upper(),
        "pressure",
        f"system_state={payload.get('system_state') or '-'} since={scope['since']} older_than={scope['older_than']} job_type={scope.get('job_type') or '-'} caller_id={scope.get('caller_id') or '-'} run_id={scope.get('run_id') or '-'}",
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
    _render_capacity_human(payload["capacity"], title="Capacity")
    if payload.get("http") is not None:
        formatters.section("HTTP")
        http_payload = payload["http"] or {}
        if http_payload.get("available") is False:
            formatters.event("MISSING", "http", f"prefix={http_payload.get('prefix') or '-'}")
        else:
            post_jobs = http_payload.get("post_jobs") if isinstance(http_payload.get("post_jobs"), dict) else {}
            status_counts = http_payload.get("failure_status_counts") or {}
            formatters.event(
                "OK",
                "http",
                "requests=%s failures=%s statuses=%s"
                % (
                    post_jobs.get("request_count") or "-",
                    post_jobs.get("failure_count") or 0,
                    formatters.compact(status_counts),
                ),
            )
    if payload.get("api_log") is not None:
        formatters.section("API Log")
        api_log = payload["api_log"] or {}
        if api_log.get("available") is False:
            formatters.event("MISSING", "api_log", f"path={api_log.get('path') or '-'}")
        else:
            formatters.event(
                "OK",
                "api_log",
                "path=%s scanned_lines=%s"
                % (api_log.get("path") or "-", api_log.get("scanned_lines") or 0),
            )
            formatters.print_table(_log_match_rows(api_log), _log_match_columns(), empty_message="no log matches")
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


def _overview_raw_payload(
    conn,
    *,
    since: str,
    window: timedelta,
    since_at: datetime,
    older_than: str,
    older_than_delta: timedelta,
    job_type: str | None,
    caller_id: str | None,
    max_active_jobs: int | None,
    sample_limit: int,
) -> dict[str, Any]:
    summary_payload = queries.summary(conn, job_type=job_type, caller_id=caller_id, since=since_at)
    capacity_payload = queries.capacity(
        conn,
        job_type=job_type,
        caller_id=caller_id,
        since=since_at,
        window_seconds=window.total_seconds(),
    )
    return {
        "stuck_limit": sample_limit,
        "summary": _summary_payload(
            since=since,
            window=window,
            job_type=job_type,
            caller_id=caller_id,
            run_id=run_id,
            record_scope="root",
            summary_payload=summary_payload,
        ),
        "capacity": _capacity_payload_from_result(
            capacity_payload,
            since=since,
            window=window,
            job_type=job_type,
            caller_id=caller_id,
            record_scope="root",
            max_active_jobs=max_active_jobs,
        ),
        "latency": queries.latency(conn, job_type=job_type, caller_id=caller_id, since=since_at, group_by="all"),
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
            record_scope="family",
        ),
        "failed_samples": queries.list_jobs(
            conn,
            statuses=["failed"],
            job_type=job_type,
            caller_id=caller_id,
            client_request_id=None,
            since=since_at,
            limit=sample_limit,
            record_scope="family",
        ),
    }


def _overview_payload(
    *,
    since: str,
    older_than: str,
    job_type: str | None,
    caller_id: str | None,
    max_active_jobs: int | None,
    sample_limit: int,
) -> dict[str, Any]:
    window, since_at = _since_window(since)
    try:
        older_than_delta = parse_duration(older_than)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    raw_payload = _with_connection(
        lambda conn: _overview_raw_payload(
            conn,
            since=since,
            window=window,
            since_at=since_at,
            older_than=older_than,
            older_than_delta=older_than_delta,
            job_type=job_type,
            caller_id=caller_id,
            max_active_jobs=max_active_jobs,
            sample_limit=sample_limit,
        )
    )
    return _pressure_payload(
        since=since,
        older_than=older_than,
        job_type=job_type,
        caller_id=caller_id,
        run_id=None,
        max_active_jobs=max_active_jobs,
        queue_wait_warning_seconds=30.0,
        run_warning_seconds=60.0,
        payload=raw_payload,
    )


def _render_overview(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    formatters.section("Job 总览")
    formatters.event(
        payload["status"].upper(),
        "overview",
        f"system_state={payload.get('system_state') or '-'} window={scope['since']} root_window=root family_risk=family current_global_active=global_gate",
    )
    bottleneck_rows = [
        {
            "severity": item["severity"],
            "area": item["area"],
            "signal": item["signal"],
            "message": item["message"],
        }
        for item in payload["bottlenecks"][:5]
    ]
    formatters.section("关键发现")
    formatters.print_table(bottleneck_rows, _pressure_bottleneck_columns(), empty_message="no findings")
    formatters.section("最近窗口 Root Job 汇总")
    formatters.event(
        "OK",
        "scope",
        f"since={scope['since']} record_scope=root job_type={scope.get('job_type') or '-'} caller_id={scope.get('caller_id') or '-'}",
    )
    formatters.print_table([payload["summary"].get("jobs") or {}], _summary_job_columns())
    _render_gate_human(
        _gate_payload(
            current=payload["capacity"].get("current") or {},
            max_active_jobs=payload["capacity"].get("max_active_jobs"),
        ),
    )
    formatters.section("最近窗口 Family 风险样本")
    stuck = payload["stuck"]
    formatters.event(
        "OK",
        "stuck",
        f"since={scope['since']} older_than={scope['older_than']} record_scope=family sample_count={stuck['sample_count']} truncated={stuck['truncated']}",
    )
    formatters.print_table(stuck["sample"], _stuck_columns(), empty_message="no stuck records")
    formatters.section("下一步检查")
    for item in payload["next_checks"]:
        print(f"- {item}")


def _run_overview(
    *,
    since: str,
    older_than: str,
    job_type: str | None,
    caller_id: str | None,
    max_active_jobs: int | None,
    sample_limit: int,
    json_output: bool,
) -> None:
    limit = max_active_jobs if max_active_jobs is not None else _env_max_active_jobs()
    payload = _overview_payload(
        since=since,
        older_than=older_than,
        job_type=job_type,
        caller_id=caller_id,
        max_active_jobs=limit,
        sample_limit=sample_limit,
    )
    if json_output:
        formatters.print_json(payload)
        return
    _render_overview(payload)


def _ingress_window_totals(payload: dict[str, Any] | None) -> dict[str, Any]:
    rows = (payload or {}).get("ingress") or []
    created = sum(_count(row.get("created")) for row in rows)
    started = sum(_count(row.get("started")) for row in rows)
    terminal = sum(_count(row.get("terminal")) for row in rows)
    failed = sum(_count(row.get("failed")) for row in rows)
    return {
        "created": created,
        "started": started,
        "terminal": terminal,
        "terminal_failed": failed,
        "terminal_failed_rate": round(failed / terminal, 4) if terminal else None,
    }


def _observe_row(index: int, payload: dict[str, Any], ingress_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = payload.get("summary") or {}
    jobs = summary.get("jobs") or {}
    callbacks = summary.get("callbacks") or {}
    capacity = payload.get("capacity") or {}
    current = capacity.get("current") or {}
    stuck = payload.get("stuck") or {}
    ingress = _ingress_window_totals(ingress_payload)
    return {
        "sample": index,
        "captured_at": datetime.now(timezone.utc),
        "status": payload.get("status"),
        "state": payload.get("system_state"),
        "queued": _count(jobs.get("queued")),
        "running_active": _count(jobs.get("running_active")),
        "active_jobs": _count(current.get("active_jobs")),
        "created": ingress["created"],
        "started": ingress["started"],
        "terminal": ingress["terminal"],
        "terminal_failed": ingress["terminal_failed"],
        "terminal_failed_rate": ingress["terminal_failed_rate"],
        "failed": _count(jobs.get("failed")),
        "callback_due": _count(callbacks.get("due")),
        "stuck": _count(stuck.get("sample_count")),
    }


def _observe_verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"state": "unknown", "message": "no samples collected"}
    first = rows[0]
    last = rows[-1]
    if len(rows) == 1:
        return {"state": last.get("state") or last.get("status") or "unknown", "message": "single snapshot; trend unavailable"}
    failed_delta = _count(last.get("failed")) - _count(first.get("failed"))
    queued_delta = _count(last.get("queued")) - _count(first.get("queued"))
    active_delta = _count(last.get("active_jobs")) - _count(first.get("active_jobs"))
    stuck_delta = _count(last.get("stuck")) - _count(first.get("stuck"))
    callback_delta = _count(last.get("callback_due")) - _count(first.get("callback_due"))
    terminal_delta = _count(last.get("terminal")) - _count(first.get("terminal"))
    terminal_failed_delta = _count(last.get("terminal_failed")) - _count(first.get("terminal_failed"))
    if failed_delta > 0:
        return {"state": "degrading", "message": "failed count increased during observation"}
    if terminal_failed_delta > 0:
        return {"state": "degrading", "message": "terminal failed throughput increased during observation"}
    if stuck_delta > 0 or callback_delta > 0:
        return {"state": "degrading", "message": "stuck or callback backlog increased during observation"}
    if active_delta < 0 and terminal_delta > 0:
        return {"state": "recovering", "message": "active backlog decreased and terminal throughput increased during observation"}
    if active_delta < 0:
        return {"state": "recovering", "message": "active backlog decreased during observation"}
    if queued_delta < 0 and active_delta <= 0 and terminal_delta > 0:
        return {"state": "recovering", "message": "queued decreased and terminal throughput increased during observation"}
    if queued_delta > 0 or active_delta > 0:
        return {"state": "backlog_expanding", "message": "queued or active backlog increased during observation"}
    if queued_delta < 0:
        return {"state": last.get("state") or "stable", "message": "queued decreased but active backlog stayed flat"}
    return {"state": last.get("state") or "stable", "message": "sampled counters stayed flat"}


def _render_observe_human(payload: dict[str, Any]) -> None:
    verdict = payload["verdict"]
    scope = payload["scope"]
    formatters.section("Job Observe")
    formatters.event(
        verdict["state"].upper(),
        "observe",
        "since=%s samples=%s interval=%ss - %s"
        % (scope["since"], len(payload["samples"]), scope["interval_seconds"], verdict["message"]),
    )
    formatters.print_table(payload["samples"], _observe_columns())
    formatters.section("Next Checks")
    for item in payload["next_checks"]:
        print(f"- {item}")


def _dashboard_status(payload: dict[str, Any]) -> dict[str, str]:
    diagnosis = _diagnose_summary(payload["summary"])
    status = diagnosis["status"]
    message = "summary=%s" % status
    stuck_count = len((payload.get("stuck") or {}).get("items") or [])
    if stuck_count:
        if status == "ok":
            status = "warning"
        message = f"{message} stuck_sample={stuck_count}"
    return {"status": status, "message": message}


def _render_dashboard_human(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    summary = payload["summary"]
    capacity = payload["capacity"]
    stuck = payload["stuck"]
    status = _dashboard_status(payload)
    formatters.section("Job Dashboard")
    formatters.event(
        status["status"].upper(),
        "dashboard",
        "since=%s bucket=%s record_scope=%s job_type=%s caller_id=%s - %s"
        % (
            scope["since"],
            scope["bucket"],
            scope["record_scope"],
            scope.get("job_type") or "-",
            scope.get("caller_id") or "-",
            status["message"],
        ),
    )
    formatters.section("Job Counts")
    formatters.print_table([summary["jobs"]], _summary_job_columns())
    formatters.section("Attempts")
    formatters.print_table([summary["attempts"]], _summary_attempt_columns())
    formatters.section("Dispatch")
    formatters.print_table([summary["dispatch"]], _summary_dispatch_columns())
    formatters.section("Callbacks")
    formatters.print_table([summary["callbacks"]], _summary_callback_columns())
    _render_capacity_human(capacity, title="Capacity")
    formatters.section("Ingress")
    formatters.print_table(payload["ingress"]["ingress"], _ingress_columns(), empty_message="no job events")
    formatters.section("Latency")
    formatters.print_table(payload["latency"]["latency"], _latency_columns(), empty_message="no latency data")
    formatters.section("Stuck Sample")
    formatters.event("OK", "stuck", f"sample_count={len(stuck['items'])} older_than={scope['older_than']} record_scope={scope['stuck_scope']}")
    formatters.print_table(stuck["items"], _stuck_columns(), empty_message="no stuck records")
    formatters.section("Notes")
    for key, value in payload["notes"].items():
        print(f"- {key}: {value}")


def _broker_payload(*, redis_key: str) -> dict[str, Any]:
    redis_url = db.env_value("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL is required")
    return redis_broker_payload(
        redis_url=redis_url,
        redis_key=redis_key,
        broker_kind=db.env_value("TASKIQ_BROKER_KIND"),
    )


def _render_broker_human(payload: dict[str, Any]) -> None:
    formatters.section("Taskiq Broker")
    formatters.event(payload["verdict"].upper(), "broker", f"key={payload['redis_key']} kind={payload['broker_kind']}")
    formatters.print_table([payload], _broker_columns())
    groups = payload.get("consumer_groups") or []
    if groups:
        formatters.section("Consumer Groups")
        formatters.print_table(groups, [("name", "name"), ("consumers", "consumers"), ("pending", "pending"), ("lag", "lag"), ("last-delivered-id", "last_delivered_id")])


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _process_rows() -> list[dict[str, Any]]:
    proc = Path("/proc")
    if not proc.is_dir():
        return [{"name": "procfs", "count": 0, "sample": "unavailable"}]
    patterns = {
        "taskiq_worker": "taskiq worker",
        "recovery_loop": "app.tasks.recovery_loop",
        "start_worker": "start-worker.sh",
        "api_server": "uvicorn",
    }
    matches = {name: [] for name in patterns}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        raw = _read_text(entry / "cmdline")
        if not raw:
            continue
        cmdline = raw.replace("\x00", " ").strip()
        for name, needle in patterns.items():
            if needle in cmdline:
                matches[name].append(cmdline)
    return [{"name": name, "count": len(values), "sample": values[0] if values else "-"} for name, values in matches.items()]


def _runtime_cgroup_payload() -> dict[str, Any]:
    root = Path("/sys/fs/cgroup")
    cpu_max = _read_text(root / "cpu.max")
    cpu_quota_cores: float | str | None = None
    if cpu_max:
        parts = cpu_max.split()
        if len(parts) >= 2 and parts[0] != "max":
            try:
                cpu_quota_cores = int(parts[0]) / int(parts[1])
            except (ValueError, ZeroDivisionError):
                cpu_quota_cores = "unavailable"
        elif parts and parts[0] == "max":
            cpu_quota_cores = "unlimited"
    cpu_stat = _read_text(root / "cpu.stat") or ""
    cpu_usage_usec = None
    for line in cpu_stat.splitlines():
        if line.startswith("usage_usec "):
            cpu_usage_usec = line.split()[1]
            break
    return {
        "cpu_max": cpu_max or "unavailable",
        "cpu_quota_cores": cpu_quota_cores or "unavailable",
        "cpu_usage_usec": cpu_usage_usec or "unavailable",
        "memory_current_bytes": _read_text(root / "memory.current") or "unavailable",
        "memory_max_bytes": _read_text(root / "memory.max") or "unavailable",
    }


def _runtime_payload() -> dict[str, Any]:
    env_keys = ["WORKER_CONCURRENCY", "WORKER_RECOVERY_LOOP", "TASKIQ_BROKER_KIND", "MAX_ACTIVE_JOBS", "DB_POOL_SIZE", "DB_MAX_OVERFLOW"]
    env = {key: db.env_value(key) or "-" for key in env_keys}
    processes = _process_rows()
    cgroup = _runtime_cgroup_payload()
    return {
        "scope": "current_pod",
        "environment": env,
        "processes": processes,
        "cgroup": cgroup,
        "verdict": "runtime_visible" if any(row["count"] for row in processes if row["name"] != "procfs") else "runtime_processes_not_detected",
    }


def _render_runtime_human(payload: dict[str, Any]) -> None:
    formatters.section("Pod Runtime")
    formatters.event(payload["verdict"].upper(), "runtime", f"scope={payload['scope']}")
    formatters.section("Environment")
    formatters.print_table([payload["environment"]], _runtime_env_columns())
    formatters.section("Processes")
    formatters.print_table(payload["processes"], _runtime_process_columns())
    formatters.section("Cgroup")
    formatters.print_table([payload["cgroup"]], _runtime_cgroup_columns())


def _capacity_payload_from_result(
    raw_payload: dict[str, Any],
    *,
    since: str,
    window: timedelta,
    job_type: str | None,
    caller_id: str | None,
    record_scope: str,
    run_id: str | None = None,
    max_active_jobs: int | None,
) -> dict[str, Any]:
    current = dict(raw_payload.get("current") or {})
    estimated = dict(raw_payload.get("estimated") or {})
    if max_active_jobs is not None and max_active_jobs > 0:
        active_jobs = int(current.get("active_jobs") or 0)
        estimated["active_ratio"] = active_jobs / max_active_jobs
        estimated["headroom"] = max_active_jobs - active_jobs
    else:
        estimated["active_ratio"] = None
        estimated["headroom"] = None
    return {
        "scope": {
            "current": "global_gate",
            "window": {
                "record_scope": record_scope,
                "since": since,
                "seconds": window.total_seconds(),
                "job_type": job_type,
                "caller_id": caller_id,
                "run_id": run_id,
            },
        },
        "max_active_jobs": max_active_jobs,
        "current": current,
        "window": dict(raw_payload.get("window") or {}),
        "estimated": estimated,
    }


def _capacity_notes() -> dict[str, str]:
    return {
        "current_active_jobs": "当前全局 active 占用：queued + running 且 active_attempt_id 非空，包含 root 与 child。",
        "accepted_submit_rps": "使用窗口内 first_created_at 到 newest_created_at 的 observed span 估算；没有跨度时退回 --since 秒数。",
        "active_jobs_needed_upper_bound": "使用窗口 accepted_submit_rps * lifecycle_p95_seconds 得到的上界估算；workflow root 等待子任务时间会让它偏保守；terminal_jobs 少于 accepted_jobs 时仍应等待排空后再采信。",
        "db_connection_budget": "估算公式：api_pods * (DB_POOL_SIZE + DB_MAX_OVERFLOW) + worker_pods * WORKER_CONCURRENCY；再与 db_max_connections * db_usable_ratio 比较。",
    }


def _capacity_payload(
    *,
    since: str,
    job_type: str | None,
    caller_id: str | None,
    record_scope: str,
    run_id: str | None = None,
    max_active_jobs: int | None,
    worker_pods: int | None,
    worker_concurrency: int | None,
    api_pods: int | None,
    db_max_connections: int | None,
    db_pool_size: int | None,
    db_max_overflow: int | None,
    db_usable_ratio: float,
) -> dict[str, Any]:
    window, since_at = _since_window(since)
    raw_payload = _with_connection(
        lambda conn: queries.capacity(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            run_id=run_id,
            since=since_at,
            window_seconds=window.total_seconds(),
            window_scope=record_scope,
        )
    )
    payload = _capacity_payload_from_result(
        raw_payload,
        since=since,
        window=window,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=record_scope,
        max_active_jobs=max_active_jobs,
    )
    payload["notes"] = _capacity_notes()
    payload["db_connection_budget"] = _capacity_db_budget(
        api_pods=api_pods,
        worker_pods=worker_pods,
        worker_concurrency=worker_concurrency,
        db_pool_size=db_pool_size,
        db_max_overflow=db_max_overflow,
        db_max_connections=db_max_connections,
        db_usable_ratio=db_usable_ratio,
    )
    payload["recommendation"] = _capacity_recommendation(payload, max_active_jobs)
    return payload


def _ingress_payload_from_rows(
    rows: list[dict[str, Any]],
    *,
    since: str,
    window: timedelta,
    bucket: str,
    bucket_seconds: int,
    job_type: str | None,
    caller_id: str | None,
    record_scope: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "scope": {
            "since": since,
            "seconds": window.total_seconds(),
            "bucket": bucket,
            "bucket_seconds": bucket_seconds,
            "job_type": job_type,
            "caller_id": caller_id,
            "run_id": run_id,
            "record_scope": record_scope,
        },
        "ingress": rows,
        "notes": {
            "created": "created_at 落入该时间桶的 Job 数。",
            "started": "started_at 落入该时间桶的 Job 数。",
            "terminal": "finished_at 落入该时间桶的 succeeded/failed Job 数。",
            "failed": "finished_at 落入该时间桶且 status=failed 的 Job 数。",
        },
    }


def _ingress_payload(
    *,
    since: str,
    bucket: str,
    job_type: str | None,
    caller_id: str | None,
    record_scope: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    window, since_at = _since_window(since)
    try:
        bucket_delta = parse_duration(bucket)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    bucket_seconds = int(bucket_delta.total_seconds())
    if bucket_seconds <= 0:
        print("ERROR: --bucket must be greater than 0 seconds", file=sys.stderr)
        raise typer.Exit(2)
    rows = _with_connection(
        lambda conn: queries.ingress(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            run_id=run_id,
            since=since_at,
            bucket_seconds=bucket_seconds,
            record_scope=record_scope,
        )
    )
    return _ingress_payload_from_rows(
        rows,
        since=since,
        window=window,
        bucket=bucket,
        bucket_seconds=bucket_seconds,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=record_scope,
    )


def _latency_payload_from_rows(
    rows: list[dict[str, Any]],
    *,
    since: str,
    window: timedelta,
    job_type: str | None,
    caller_id: str | None,
    record_scope: str,
    group_by: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "scope": {
            "since": since,
            "seconds": window.total_seconds(),
            "job_type": job_type,
            "caller_id": caller_id,
            "run_id": run_id,
            "record_scope": record_scope,
        },
        "group_by": group_by,
        "latency": rows,
    }


def _latency_payload(
    *,
    since: str,
    job_type: str | None,
    caller_id: str | None,
    record_scope: str,
    group_by: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    window, since_at = _since_window(since)
    rows = _with_connection(
        lambda conn: queries.latency(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            run_id=run_id,
            since=since_at,
            group_by=group_by,
            record_scope=record_scope,
        )
    )
    return _latency_payload_from_rows(
        rows,
        since=since,
        window=window,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=record_scope,
        group_by=group_by,
    )


def _stuck_payload_from_rows(
    rows: list[dict[str, Any]],
    *,
    older_than: str,
    since: str | None,
    job_type: str | None,
    caller_id: str | None,
    record_scope: str,
) -> dict[str, Any]:
    return {
        "scope": {
            "older_than": older_than,
            "since": since,
            "job_type": job_type,
            "caller_id": caller_id,
            "record_scope": record_scope,
        },
        "items": rows,
    }


def _stuck_payload(
    *,
    older_than: str,
    since: str | None,
    job_type: str | None,
    caller_id: str | None,
    run_id: str | None,
    record_scope: str,
    limit: int,
) -> dict[str, Any]:
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
            run_id=run_id,
            since=since_at,
            record_scope=record_scope,
        )
    )
    return _stuck_payload_from_rows(
        rows,
        older_than=older_than,
        since=since,
        job_type=job_type,
        caller_id=caller_id,
        record_scope=record_scope,
    )


def _dashboard_payload(
    *,
    since: str,
    bucket: str,
    older_than: str,
    job_type: str | None,
    caller_id: str | None,
    max_active_jobs: int | None,
    record_scope: str = "root",
    stuck_scope: str = "family",
    stuck_limit: int = 20,
) -> dict[str, Any]:
    window, since_at = _since_window(since)
    try:
        bucket_delta = parse_duration(bucket)
        older_than_delta = parse_duration(older_than)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    bucket_seconds = int(bucket_delta.total_seconds())
    if bucket_seconds <= 0:
        print("ERROR: --bucket must be greater than 0 seconds", file=sys.stderr)
        raise typer.Exit(2)

    def action(conn):
        execution_scope = "family" if record_scope == "root" else record_scope
        summary_raw = queries.summary(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
            record_scope=record_scope,
            execution_scope=execution_scope,
        )
        capacity_raw = queries.capacity(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
            window_seconds=window.total_seconds(),
            window_scope=record_scope,
        )
        ingress_rows = queries.ingress(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
            bucket_seconds=bucket_seconds,
            record_scope=record_scope,
        )
        latency_rows = queries.latency(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
            group_by="all",
            record_scope=record_scope,
        )
        stuck_rows = queries.stuck(
            conn,
            older_than=older_than_delta,
            limit=stuck_limit,
            job_type=job_type,
            caller_id=caller_id,
            since=since_at,
            record_scope=stuck_scope,
        )
        return {
            "summary_raw": summary_raw,
            "capacity_raw": capacity_raw,
            "ingress_rows": ingress_rows,
            "latency_rows": latency_rows,
            "stuck_rows": stuck_rows,
        }

    raw_payload = _with_connection(action)
    return {
        "scope": {
            "since": since,
            "seconds": window.total_seconds(),
            "bucket": bucket,
            "bucket_seconds": bucket_seconds,
            "older_than": older_than,
            "job_type": job_type,
            "caller_id": caller_id,
            "record_scope": record_scope,
            "stuck_scope": stuck_scope,
        },
        "summary": _summary_payload(
            since=since,
            window=window,
            job_type=job_type,
            caller_id=caller_id,
            record_scope=record_scope,
            summary_payload=raw_payload["summary_raw"],
        ),
        "capacity": _capacity_payload_from_result(
            raw_payload["capacity_raw"],
            since=since,
            window=window,
            job_type=job_type,
            caller_id=caller_id,
            record_scope=record_scope,
            max_active_jobs=max_active_jobs,
        ),
        "ingress": _ingress_payload_from_rows(
            raw_payload["ingress_rows"],
            since=since,
            window=window,
            bucket=bucket,
            bucket_seconds=bucket_seconds,
            job_type=job_type,
            caller_id=caller_id,
            record_scope=record_scope,
        ),
        "latency": _latency_payload_from_rows(
            raw_payload["latency_rows"],
            since=since,
            window=window,
            job_type=job_type,
            caller_id=caller_id,
            record_scope=record_scope,
            group_by="all",
        ),
        "stuck": _stuck_payload_from_rows(
            raw_payload["stuck_rows"],
            older_than=older_than,
            since=since,
            job_type=job_type,
            caller_id=caller_id,
            record_scope=stuck_scope,
        ),
        "notes": {
            "current": "capacity.current 使用 global_gate，始终是当前全局 active 占用，不受 --since/job_type/caller_id 过滤。",
            "window": "summary、capacity.window、ingress、latency 按 record_scope 和时间窗口过滤。",
            "transport_runtime": "dashboard 基础 payload 不读取 Redis、/proc 或 cgroup；broker/runtime 仍是显式检查。",
        },
    }


def _callbacks_summary_payload(
    *,
    since: str,
    job_type: str | None,
    caller_id: str | None,
    record_scope: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    window, since_at = _since_window(since)
    rows = _with_connection(
        lambda conn: queries.callbacks_summary(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            run_id=run_id,
            since=since_at,
            record_scope=record_scope,
        )
    )
    due = sum(_count(row.get("due")) for row in rows)
    dead = sum(_count(row.get("count")) for row in rows if row.get("status") == "dead_letter")
    status = "critical" if dead else "warning" if due else "ok"
    return {
        "scope": {
            "since": since,
            "seconds": window.total_seconds(),
            "job_type": job_type,
            "caller_id": caller_id,
            "run_id": run_id,
            "record_scope": record_scope,
        },
        "status": status,
        "callbacks": rows,
    }


def _render_callbacks_summary_human(payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    formatters.section("Callback Summary")
    formatters.event(
        payload["status"].upper(),
        "callbacks",
        f"since={scope['since']} record_scope={scope['record_scope']} job_type={scope.get('job_type') or '-'} caller_id={scope.get('caller_id') or '-'} run_id={scope.get('run_id') or '-'}",
    )
    formatters.print_table(payload["callbacks"], _callbacks_summary_columns(), empty_message="no callbacks")


@app.callback()
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _run_overview(
            since="10m",
            older_than="1m",
            job_type=None,
            caller_id=None,
            max_active_jobs=None,
            sample_limit=10,
            json_output=False,
        )


@app.command(help="查看 Job 排障四层模型和场景化用法。", epilog=GUIDE_HELP_EPILOG)
def guide() -> None:
    print(GUIDE_TEXT)


@app.command(help="查看 Job 总览；默认等同于 jobs.sh 无参。", epilog=OVERVIEW_HELP_EPILOG)
def overview(
    since: Annotated[str, typer.Option("--since", help="诊断窗口，例如 10m。")] = "10m",
    older_than: Annotated[str, typer.Option("--older-than", help="stuck 判定窗口，例如 1m。")] = "1m",
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    max_active_jobs: Annotated[
        int | None,
        typer.Option("--max-active-jobs", min=0, help="用于计算 active 占用比例；默认读取环境或 .env。"),
    ] = None,
    sample_limit: Annotated[int, typer.Option("--sample-limit", min=1, max=100, help="样本条数。")] = 10,
    json_output: JsonOption = False,
) -> None:
    _run_overview(
        since=since,
        older_than=older_than,
        job_type=job_type,
        caller_id=caller_id,
        max_active_jobs=max_active_jobs,
        sample_limit=sample_limit,
        json_output=json_output,
    )


@app.command(help="查看当前全局 active 占用。", epilog=GATE_HELP_EPILOG)
def gate(
    max_active_jobs: Annotated[
        int | None,
        typer.Option("--max-active-jobs", min=0, help="用于计算 active 占用比例；默认读取环境或 .env。"),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    limit = max_active_jobs if max_active_jobs is not None else _env_max_active_jobs()
    current = _with_connection(lambda conn: queries.global_gate(conn))
    payload = _gate_payload(current=current, max_active_jobs=limit)
    if json_output:
        formatters.print_json(payload)
        return
    _render_gate_human(payload)


@app.command("list", help="查看最近 Job 摘要。", epilog=LIST_HELP_EPILOG)
def list_jobs(
    status: Annotated[
        list[str] | None,
        typer.Option("--status", help="按 Job 状态过滤；可重复传入，也可用逗号分隔。"),
    ] = None,
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    run_id: RunIdOption = None,
    client_request_id: Annotated[
        str | None,
        typer.Option("--client-request-id", help="按 client_request_id 过滤。"),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="只查看指定时间窗口内创建的 Job，例如 24h。"),
    ] = None,
    record_scope: ScopeOption = "root",
    limit: LimitOption = 20,
    json_output: JsonOption = False,
) -> None:
    try:
        statuses = parse_statuses(status)
        since_delta = parse_optional_duration(since)
        parsed_scope = parse_record_scope(record_scope)
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
            run_id=run_id,
            since=since_at,
            limit=limit,
            record_scope=parsed_scope,
        )
    )
    scope_payload = {
        "record_scope": parsed_scope,
        "since": since,
        "statuses": statuses,
        "job_type": job_type,
        "caller_id": caller_id,
        "run_id": run_id,
        "client_request_id": client_request_id,
    }
    if json_output:
        formatters.print_json({"scope": scope_payload, "jobs": rows})
        return
    _render_jobs_result(rows=rows, scope=scope_payload)


@app.command("deleted-summary", help="查看软删除 Job 数量和一致性摘要。", epilog=DELETED_SUMMARY_HELP_EPILOG)
def deleted_summary(
    since_deleted: Annotated[
        str | None,
        typer.Option("--since-deleted", help="只查看指定窗口内软删除的 Job，例如 7d。"),
    ] = None,
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 root/job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 root/caller_id 过滤。")] = None,
    record_scope: ScopeOption = "all",
    json_output: JsonOption = False,
) -> None:
    try:
        since_delta = parse_optional_duration(since_deleted)
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    since_at = datetime.now(timezone.utc) - since_delta if since_delta else None
    summary = _with_connection(
        lambda conn: queries.deleted_summary(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            deleted_since=since_at,
            record_scope=parsed_scope,
        )
    )
    payload = {
        "scope": {
            "since_deleted": since_deleted,
            "record_scope": parsed_scope,
            "job_type": job_type,
            "caller_id": caller_id,
        },
        "summary": summary,
    }
    if json_output:
        formatters.print_json(payload)
        return
    _render_deleted_summary(payload)


@app.command("deleted-list", help="查看软删除 Job 摘要。", epilog=DELETED_LIST_HELP_EPILOG)
def deleted_list(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 root/job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 root/caller_id 过滤。")] = None,
    client_request_id: Annotated[
        str | None,
        typer.Option("--client-request-id", help="按 root/client_request_id 过滤。"),
    ] = None,
    since_deleted: Annotated[
        str | None,
        typer.Option("--since-deleted", help="只查看指定窗口内软删除的 Job，例如 7d。"),
    ] = None,
    record_scope: ScopeOption = "root",
    limit: LimitOption = 20,
    json_output: JsonOption = False,
) -> None:
    try:
        since_delta = parse_optional_duration(since_deleted)
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    since_at = datetime.now(timezone.utc) - since_delta if since_delta else None
    rows = _with_connection(
        lambda conn: queries.deleted_jobs(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            client_request_id=client_request_id,
            deleted_since=since_at,
            limit=limit,
            record_scope=parsed_scope,
        )
    )
    scope_payload = {
        "record_scope": parsed_scope,
        "since_deleted": since_deleted,
        "job_type": job_type,
        "caller_id": caller_id,
        "client_request_id": client_request_id,
    }
    if json_output:
        formatters.print_json({"scope": scope_payload, "jobs": rows})
        return
    _render_deleted_jobs_result(rows=rows, scope=scope_payload)


@app.command("deleted-job", help="查看单个软删除 Job 的审计摘要。", epilog=DELETED_JOB_HELP_EPILOG)
def deleted_job(job_id: JobIdArgument, json_output: JsonOption = False) -> None:
    job = _with_connection(lambda conn: queries.get_deleted_job(conn, job_id))
    if job is None:
        print(f"ERROR: deleted job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json({"job": _job_summary(job)})
        return
    formatters.section("Deleted Job")
    formatters.event("OK", "deleted", f"job_id={job_id}")
    formatters.print_table([job], _deleted_job_columns())


@app.command(help="查看单个 Job 权威状态。", epilog=SHOW_HELP_EPILOG)
def show(job_id: JobIdArgument, json_output: JsonOption = False) -> None:
    job = _with_connection(lambda conn: queries.get_job(conn, job_id))
    if job is None:
        print(f"ERROR: job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json({"job": _job_summary(job)})
        return
    formatters.section("Job")
    formatters.event("OK", "job", f"job_id={job_id}")
    formatters.print_table([_job_inspect_row(job)], _job_inspect_columns())


@app.command("job", help="查看单个 Job 轻量状态。", epilog=JOB_HELP_EPILOG)
def job_status(job_id: JobIdArgument, json_output: JsonOption = False) -> None:
    show(job_id=job_id, json_output=json_output)


@app.command(help="聚合查看单个 Job。", epilog=INSPECT_HELP_EPILOG)
def inspect(
    job_id: JobIdArgument,
    events_limit: Annotated[
        int,
        typer.Option("--events-limit", min=1, max=1000, help="展示的最近事件条数。"),
    ] = 10,
    include_children: Annotated[
        bool,
        typer.Option("--include-children", help="包含 workflow internal child jobs；默认只展示 root job 聚合证据。"),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    def action(conn):
        job = queries.get_job(conn, job_id)
        if job is None:
            return None
        payload = {
            "job": job,
            "attempts": queries.attempts(conn, job_id),
            "ai_calls": queries.ai_calls(conn, job_id),
            "callbacks": queries.callbacks(conn, job_id),
            "timeline": queries.timeline(conn, job_id, limit=events_limit),
        }
        if include_children:
            payload["children"] = queries.child_jobs(conn, job_id)
        payload["diagnosis"] = _diagnose_job(payload, include_children=include_children)
        return payload

    payload = _with_connection(action)
    if payload is None:
        print(f"ERROR: job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json(_inspect_json_payload(payload, include_children=include_children))
        return
    _render_inspect_human(payload, include_children=include_children)


@app.command(help="查看单个 Job 的入参、runtime、结果和错误 payload。", epilog=PAYLOAD_HELP_EPILOG)
def payload(
    job_id: JobIdArgument,
    include_children: Annotated[
        bool,
        typer.Option("--include-children", help="包含 workflow internal child jobs 的入参和结果 payload。"),
    ] = False,
    full: Annotated[
        bool,
        typer.Option("--full", help="输出完整入参、runtime、结果和错误 payload；可能很大。"),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    def action(conn):
        job = queries.get_job(conn, job_id)
        if job is None:
            return None
        evidence_fn = _payload_evidence if full else _payload_summary_evidence
        result = {
            "mode": "full" if full else "summary",
            "job": _payload_job_summary(job),
            "payload": evidence_fn(job),
        }
        if include_children:
            result["children"] = [
                {
                    "job": _payload_job_summary(child),
                    "payload": evidence_fn(child),
                }
                for child in queries.child_jobs(conn, job_id)
            ]
        return result

    result = _with_connection(action)
    if result is None:
        print(f"ERROR: job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json(result)
        return
    _render_payload_human(result, include_children=include_children, full=full)


@app.command(help="诊断单个 Job 的 attempt、dispatch、callback 和 claim 风险。", epilog=DIAGNOSE_HELP_EPILOG)
def diagnose(
    job_id: JobIdArgument,
    include_children: Annotated[
        bool,
        typer.Option("--include-children/--no-children", help="是否包含 workflow internal child jobs；默认只诊断 root job。"),
    ] = False,
    events_limit: Annotated[
        int,
        typer.Option("--events-limit", min=1, max=1000, help="用于诊断的最近事件条数。"),
    ] = 100,
    older_than: Annotated[
        str,
        typer.Option("--older-than", help="把刚发布/刚到期状态升为 warning 的最小年龄，例如 1m。"),
    ] = "1m",
    json_output: JsonOption = False,
) -> None:
    try:
        older_than_delta = parse_duration(older_than)
    except ValueError as exc:
        print(f"ERROR: invalid --older-than: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc

    def action(conn):
        job = queries.get_job(conn, job_id)
        if job is None:
            return None
        payload = {
            "job": job,
            "attempts": queries.attempts(conn, job_id),
            "ai_calls": queries.ai_calls(conn, job_id),
            "callbacks": queries.callbacks(conn, job_id),
            "timeline": queries.timeline(conn, job_id, limit=events_limit),
        }
        if include_children:
            payload["children"] = queries.child_jobs(conn, job_id)
        return {
            "job_id": job_id,
            "ai_calls": payload["ai_calls"],
            "diagnosis": _diagnose_job(payload, include_children=include_children, older_than=older_than_delta),
        }

    payload = _with_connection(action)
    if payload is None:
        print(f"ERROR: job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json(payload)
        return
    _render_diagnosis_human(payload["diagnosis"])
    _render_ai_calls_human(payload["ai_calls"])


def _root_job_id_for(job: dict[str, Any]) -> str:
    root_job_id = job.get("root_job_id")
    return str(root_job_id or job.get("id"))


def _workflow_payload(conn, job_id: str, *, events_limit: int) -> dict[str, Any] | None:
    source_job = queries.get_job(conn, job_id)
    if source_job is None:
        return None
    root_job_id = _root_job_id_for(source_job)
    root_job = queries.get_job(conn, root_job_id)
    if root_job is None:
        return None
    payload = {
        "source_job": source_job,
        "root_job": root_job,
        "children": queries.child_jobs(conn, root_job_id),
        "attempts": queries.attempts(conn, root_job_id),
        "callbacks": queries.callbacks(conn, root_job_id),
        "timeline": queries.timeline(conn, root_job_id, limit=events_limit),
    }
    diagnosis_input = {
        "job": root_job,
        "children": payload["children"],
        "attempts": payload["attempts"],
        "callbacks": payload["callbacks"],
        "timeline": payload["timeline"],
    }
    payload["diagnosis"] = _diagnose_job(diagnosis_input, include_children=True)
    return payload


def _render_workflow_human(payload: dict[str, Any]) -> None:
    source_job = payload["source_job"]
    root_job = payload["root_job"]
    children = payload["children"]
    formatters.section("Workflow")
    formatters.event(
        "OK",
        "workflow",
        "root_job_id=%s source_job_id=%s children=%s"
        % (root_job.get("id"), source_job.get("id"), len(children)),
    )
    formatters.section("Root Job")
    formatters.print_table([_job_inspect_row(root_job)], _job_inspect_columns())
    _render_diagnosis_human(payload["diagnosis"], title="Workflow Diagnosis")
    formatters.section("Workflow Children")
    formatters.print_table(children, _child_job_columns(), empty_message="no workflow children")
    formatters.section("Root Attempts")
    formatters.print_table(payload["attempts"], _attempt_columns(), empty_message="no root attempts")
    if payload["callbacks"]:
        formatters.section("Root Callbacks")
        formatters.print_table(payload["callbacks"], _callback_columns(), empty_message="no root callbacks")
    formatters.section("Root Timeline")
    formatters.print_table(payload["timeline"], _timeline_columns(), empty_message="no root events")


@app.command(help="查看 workflow root 与 children；可传 root 或 child job_id。", epilog=WORKFLOW_HELP_EPILOG)
def workflow(
    job_id: JobIdArgument,
    events_limit: Annotated[
        int,
        typer.Option("--events-limit", min=1, max=1000, help="展示的 root 最近事件条数。"),
    ] = 50,
    json_output: JsonOption = False,
) -> None:
    payload = _with_connection(lambda conn: _workflow_payload(conn, job_id, events_limit=events_limit))
    if payload is None:
        print(f"ERROR: job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json(_workflow_json_payload(payload))
        return
    _render_workflow_human(payload)


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


@app.command(help="查看 lifecycle job events。", epilog=TIMELINE_HELP_EPILOG)
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


@app.command(help="查看 lifecycle attempts。", epilog=ATTEMPTS_HELP_EPILOG)
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


@app.command("ai-calls", help="查看单个 Job 的 AI call ledger。", epilog=AI_CALLS_HELP_EPILOG)
def ai_calls(job_id: JobIdArgument, json_output: JsonOption = False) -> None:
    _run_related_collection(
        job_id,
        query_fn=queries.ai_calls,
        section="AI Calls",
        target="ai_calls",
        columns=_ai_call_columns(),
        key="ai_calls",
        json_output=json_output,
    )


@app.command(help="查看 lifecycle callback outbox。", epilog=CALLBACKS_HELP_EPILOG)
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


@app.command(help="查看单个 Job 的阶段耗时和当前卡点。", epilog=TRACE_HELP_EPILOG)
def trace(
    job_id: JobIdArgument,
    events_limit: Annotated[
        int,
        typer.Option("--events-limit", min=1, max=1000, help="用于计算阶段的最近事件条数。"),
    ] = 100,
    include_children: Annotated[
        bool,
        typer.Option("--include-children", help="包含 workflow internal child 汇总。"),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    def action(conn):
        job = queries.get_job(conn, job_id)
        if job is None:
            return None
        payload = {
            "job": job,
            "attempts": queries.attempts(conn, job_id),
            "callbacks": queries.callbacks(conn, job_id),
            "timeline": queries.timeline(conn, job_id, limit=events_limit),
        }
        if include_children:
            payload["children"] = queries.child_jobs(conn, _root_job_id_for(job))
        payload["diagnosis"] = _diagnose_job(payload, include_children=include_children)
        return _job_trace_payload(payload, include_children=include_children)

    payload = _with_connection(action)
    if payload is None:
        print(f"ERROR: job not found: {job_id}", file=sys.stderr)
        raise typer.Exit(3)
    if json_output:
        formatters.print_json(payload)
        return
    _render_trace_human(payload)


@app.command(help="扫描疑似卡住的 Job、attempt 或 callback lease。", epilog=STUCK_HELP_EPILOG)
def stuck(
    older_than: Annotated[
        str,
        typer.Option("--older-than", help="卡住判定时间窗口，例如 10m。"),
    ] = "10m",
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    run_id: RunIdOption = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="只扫描指定时间窗口内创建的 Job，例如 30m。"),
    ] = None,
    record_scope: ScopeOption = "family",
    limit: LimitOption = 50,
    json_output: JsonOption = False,
) -> None:
    try:
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc

    payload = _stuck_payload(
        older_than=older_than,
        since=since,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=parsed_scope,
        limit=limit,
    )
    if json_output:
        formatters.print_json(payload)
        return
    _render_result(section="Stuck Jobs", target="items", rows=payload["items"], columns=_stuck_columns())


@app.command(help="判断压测前后 Job 是否已经排空。", epilog=DRAIN_HELP_EPILOG)
def drain(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    run_id: RunIdOption = None,
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
    record_scope: ScopeOption = "family",
    json_output: JsonOption = False,
) -> None:
    window, since_at = _since_window(since)
    try:
        older_than_delta = parse_duration(older_than)
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    raw_payload = _with_connection(
        lambda conn: queries.drain_status(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            run_id=run_id,
            since=since_at,
            older_than=older_than_delta,
            record_scope=parsed_scope,
        )
    )
    payload = _drain_payload(
        since=since,
        older_than=older_than,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        raw_payload=raw_payload | {"scope_window_seconds": window.total_seconds()},
    )
    if json_output:
        formatters.print_json(payload)
    else:
        _render_drain(payload)
    if strict and payload["status"] != "drained":
        raise typer.Exit(4)


@app.command(help="汇总压测窗口并判断瓶颈方向。", epilog=PRESSURE_HELP_EPILOG)
def pressure(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤窗口证据。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤窗口证据。")] = None,
    run_id: RunIdOption = None,
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
        typer.Option("--max-active-jobs", min=0, help="用于计算 active 占用比例；默认读取环境或 .env。"),
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
        summary_payload = queries.summary(conn, job_type=job_type, caller_id=caller_id, since=since_at, run_id=run_id)
        capacity_payload = queries.capacity(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            run_id=run_id,
            since=since_at,
            window_seconds=window.total_seconds(),
        )
        return {
            "stuck_limit": sample_limit,
            "summary": _summary_payload(
                since=since,
                window=window,
                job_type=job_type,
                caller_id=caller_id,
                run_id=run_id,
                record_scope="root",
                summary_payload=summary_payload,
            ),
            "capacity": _capacity_payload_from_result(
                capacity_payload,
                since=since,
                window=window,
                job_type=job_type,
                caller_id=caller_id,
                run_id=run_id,
                record_scope="root",
                max_active_jobs=limit,
            ),
            "latency": queries.latency(
                conn,
                job_type=job_type,
                caller_id=caller_id,
                run_id=run_id,
                since=since_at,
                group_by="all",
                record_scope="root",
            ),
            "stuck": queries.stuck(
                conn,
                older_than=older_than_delta,
                limit=sample_limit,
                job_type=job_type,
                caller_id=caller_id,
                run_id=run_id,
                since=since_at,
                record_scope="family",
            ),
            "failure_groups": queries.failure_groups(
                conn,
                job_type=job_type,
                caller_id=caller_id,
                run_id=run_id,
                since=since_at,
                limit=sample_limit,
                record_scope="family",
            ),
            "active_samples": queries.list_jobs(
                conn,
                statuses=["queued", "running"],
                job_type=job_type,
                caller_id=caller_id,
                client_request_id=None,
                run_id=run_id,
                since=since_at,
                limit=sample_limit,
                record_scope="family",
            ),
            "failed_samples": queries.list_jobs(
                conn,
                statuses=["failed"],
                job_type=job_type,
                caller_id=caller_id,
                client_request_id=None,
                run_id=run_id,
                since=since_at,
                limit=sample_limit,
                record_scope="family",
            ),
        }

    raw_payload = _with_connection(action)
    payload = _pressure_payload(
        since=since,
        older_than=older_than,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
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


@app.command(help="汇总 Job、attempt、dispatch 和 callback 当前状态。", epilog=SUMMARY_HELP_EPILOG)
def summary(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    run_id: RunIdOption = None,
    since: Annotated[
        str,
        typer.Option("--since", help="只统计指定时间窗口内创建的 Job，例如 10m。"),
    ] = "10m",
    record_scope: ScopeOption = "root",
    json_output: JsonOption = False,
) -> None:
    try:
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    payload = _fetch_summary_payload(
        since=since,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=parsed_scope,
    )
    if json_output:
        formatters.print_json(payload)
        return
    _render_summary(payload)


@app.command(help="基于 summary 数据给出维护人员排障摘要和下一步检查。", epilog=DOCTOR_HELP_EPILOG)
def doctor(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    run_id: RunIdOption = None,
    since: Annotated[
        str,
        typer.Option("--since", help="只诊断指定时间窗口内创建的 Job，例如 10m。"),
    ] = "10m",
    json_output: JsonOption = False,
) -> None:
    summary_payload = _fetch_summary_payload(
        since=since,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope="root",
    )
    payload = _diagnose_summary(summary_payload)
    if json_output:
        formatters.print_json(payload)
        return
    _render_doctor(payload)


@app.command(help="连续采样 Job 宏观状态，判断是否正在恢复。", epilog=OBSERVE_HELP_EPILOG)
def observe(
    since: Annotated[str, typer.Option("--since", help="每次采样的诊断窗口，例如 30m。")] = "30m",
    older_than: Annotated[str, typer.Option("--older-than", help="stuck 判定窗口，例如 1m。")] = "1m",
    ingress_bucket: Annotated[str, typer.Option("--ingress-bucket", help="吞吐统计时间桶，例如 1m、5m。")] = "1m",
    interval: Annotated[int, typer.Option("--interval", min=0, help="采样间隔秒数；生产建议 60。")] = 60,
    samples: Annotated[int, typer.Option("--samples", min=1, max=100, help="采样次数。")] = 5,
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤窗口证据。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤窗口证据。")] = None,
    max_active_jobs: Annotated[
        int | None,
        typer.Option("--max-active-jobs", min=0, help="用于计算 active 占用比例；默认读取环境或 .env。"),
    ] = None,
    sample_limit: Annotated[int, typer.Option("--sample-limit", min=1, max=100, help="stuck 样本条数。")] = 20,
    json_output: JsonOption = False,
) -> None:
    limit = max_active_jobs if max_active_jobs is not None else _env_max_active_jobs()
    rows: list[dict[str, Any]] = []
    for index in range(1, samples + 1):
        payload = _overview_payload(
            since=since,
            older_than=older_than,
            job_type=job_type,
            caller_id=caller_id,
            max_active_jobs=limit,
            sample_limit=sample_limit,
        )
        ingress_payload = _ingress_payload(
            since=since,
            bucket=ingress_bucket,
            job_type=job_type,
            caller_id=caller_id,
            record_scope="root",
        )
        rows.append(_observe_row(index, payload, ingress_payload))
        if index < samples and interval > 0:
            time.sleep(interval)
    filters = (f" --job-type {job_type}" if job_type else "") + (f" --caller-id {caller_id}" if caller_id else "")
    payload = {
        "scope": {
            "since": since,
            "older_than": older_than,
            "ingress_bucket": ingress_bucket,
            "interval_seconds": interval,
            "samples": samples,
            "job_type": job_type,
            "caller_id": caller_id,
        },
        "samples": rows,
        "verdict": _observe_verdict(rows),
        "next_checks": [
            f"./scripts/jobs.sh overview --since {since}{filters}",
            f"./scripts/jobs.sh stuck --since {since} --older-than {older_than}{filters} --limit 20",
            f"./scripts/jobs.sh ingress --since {since} --bucket {ingress_bucket}{filters}",
            f"./scripts/jobs.sh failures --since {since}{filters}",
            f"./scripts/jobs.sh callbacks-summary --since {since}{filters}",
            "./scripts/jobs.sh broker",
            "./scripts/jobs.sh runtime",
        ],
    }
    if json_output:
        formatters.print_json(payload)
        return
    _render_observe_human(payload)


@app.command(help="查看 Job 系统数量、容量、吞吐、耗时和 stuck 样本大盘。", epilog=DASHBOARD_HELP_EPILOG)
def dashboard(
    since: Annotated[str, typer.Option("--since", help="统计窗口，例如 1h。")] = "1h",
    bucket: Annotated[str, typer.Option("--bucket", help="吞吐时间桶，例如 1m、5m。")] = "1m",
    older_than: Annotated[str, typer.Option("--older-than", help="stuck 判定窗口，例如 10m。")] = "10m",
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤窗口证据。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤窗口证据。")] = None,
    max_active_jobs: Annotated[
        int | None,
        typer.Option("--max-active-jobs", min=0, help="用于计算 active 占用比例；默认读取环境或 .env。"),
    ] = None,
    record_scope: ScopeOption = "root",
    stuck_scope: StuckScopeOption = "family",
    stuck_limit: Annotated[int, typer.Option("--stuck-limit", min=1, max=100, help="stuck 样本条数。")] = 20,
    json_output: JsonOption = False,
) -> None:
    try:
        parsed_record_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: --scope {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    try:
        parsed_stuck_scope = parse_record_scope(stuck_scope)
    except ValueError as exc:
        print(f"ERROR: --stuck-scope {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    limit = max_active_jobs if max_active_jobs is not None else _env_max_active_jobs()
    payload = _dashboard_payload(
        since=since,
        bucket=bucket,
        older_than=older_than,
        job_type=job_type,
        caller_id=caller_id,
        max_active_jobs=limit,
        record_scope=parsed_record_scope,
        stuck_scope=parsed_stuck_scope,
        stuck_limit=stuck_limit,
    )
    if json_output:
        formatters.print_json(payload)
        return
    _render_dashboard_human(payload)


@app.command(help="查看 Redis/Taskiq broker 只读运输层状态。", epilog=BROKER_HELP_EPILOG)
def broker(
    redis_key: Annotated[str, typer.Option("--redis-key", help="Taskiq Redis 队列 key；默认 taskiq。")] = "taskiq",
    json_output: JsonOption = False,
) -> None:
    try:
        payload = _broker_payload(redis_key=redis_key)
    except Exception as exc:
        print(f"ERROR: broker evidence unavailable: {exc}", file=sys.stderr)
        raise typer.Exit(4) from exc
    if json_output:
        formatters.print_json(payload)
        return
    _render_broker_human(payload)


@app.command(help="查看当前 Pod/容器内 worker runtime、进程和 cgroup 资源证据。", epilog=RUNTIME_HELP_EPILOG)
def runtime(json_output: JsonOption = False) -> None:
    payload = _runtime_payload()
    if json_output:
        formatters.print_json(payload)
        return
    _render_runtime_human(payload)


@app.command(help="聚合查看 failed Job 的错误 code、kind 和 phase。", epilog=FAILURES_HELP_EPILOG)
def failures(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    run_id: RunIdOption = None,
    since: Annotated[str, typer.Option("--since", help="只统计指定窗口内创建的 Job，例如 1h。")] = "1h",
    record_scope: ScopeOption = "family",
    limit: LimitOption = 20,
    json_output: JsonOption = False,
) -> None:
    try:
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    window, since_at = _since_window(since)
    rows = _with_connection(
        lambda conn: queries.failure_groups(
            conn,
            job_type=job_type,
            caller_id=caller_id,
            run_id=run_id,
            since=since_at,
            limit=limit,
            record_scope=parsed_scope,
        )
    )
    payload = {
        "scope": {
            "since": since,
            "seconds": window.total_seconds(),
            "job_type": job_type,
            "caller_id": caller_id,
            "run_id": run_id,
            "record_scope": parsed_scope,
        },
        "failure_groups": rows,
    }
    if json_output:
        formatters.print_json(payload)
        return
    formatters.section("Failure Groups")
    formatters.event(
        "OK",
        "failures",
        f"since={since} record_scope={parsed_scope} job_type={job_type or '-'} caller_id={caller_id or '-'} run_id={run_id or '-'}",
    )
    formatters.print_table(rows, _failure_group_columns(), empty_message="no failed jobs")


@app.command("callbacks-summary", help="宏观查看 callback outbox 是否闭环。", epilog=CALLBACKS_SUMMARY_HELP_EPILOG)
def callbacks_summary(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    run_id: RunIdOption = None,
    since: Annotated[str, typer.Option("--since", help="只统计指定窗口内创建的 root Job，例如 1h。")] = "1h",
    record_scope: ScopeOption = "root",
    json_output: JsonOption = False,
) -> None:
    try:
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    payload = _callbacks_summary_payload(
        since=since,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=parsed_scope,
    )
    if json_output:
        formatters.print_json(payload)
        return
    _render_callbacks_summary_human(payload)


@app.command(help="按时间桶查看 Job 创建、开始、终态和失败速率。", epilog=INGRESS_HELP_EPILOG)
def ingress(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    run_id: RunIdOption = None,
    since: Annotated[str, typer.Option("--since", help="统计窗口，例如 30m。")] = "30m",
    bucket: Annotated[str, typer.Option("--bucket", help="时间桶大小，例如 1m、5m、1h。")] = "1m",
    record_scope: ScopeOption = "root",
    json_output: JsonOption = False,
) -> None:
    try:
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    payload = _ingress_payload(
        since=since,
        bucket=bucket,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=parsed_scope,
    )
    if json_output:
        formatters.print_json(payload)
        return
    formatters.section("Job Ingress")
    formatters.event(
        "OK",
        "ingress",
        f"since={since} bucket={bucket} record_scope={parsed_scope} job_type={job_type or '-'} caller_id={caller_id or '-'} run_id={run_id or '-'}",
    )
    formatters.print_table(payload["ingress"], _ingress_columns(), empty_message="no job events")


@app.command(help="统计 Job 生命周期耗时。", epilog=LATENCY_HELP_EPILOG)
def latency(
    job_type: Annotated[str | None, typer.Option("--job-type", help="按 job_type 过滤。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="按 caller_id 过滤。")] = None,
    run_id: RunIdOption = None,
    since: Annotated[
        str,
        typer.Option("--since", help="只统计指定时间窗口内创建的 Job，例如 30m。"),
    ] = "30m",
    group_by: Annotated[
        str,
        typer.Option("--group-by", help="分组字段：all、job_type、caller_id 或 status。"),
    ] = "job_type",
    record_scope: ScopeOption = "root",
    json_output: JsonOption = False,
) -> None:
    try:
        group = parse_latency_group_by(group_by)
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    payload = _latency_payload(
        since=since,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=parsed_scope,
        group_by=group,
    )
    if json_output:
        formatters.print_json(payload)
        return
    _render_result(section="Job Latency", target="groups", rows=payload["latency"], columns=_latency_columns())


@app.command(help="查看当前全局 active 占用和窗口容量估算。", epilog=CAPACITY_HELP_EPILOG)
def capacity(
    job_type: Annotated[str | None, typer.Option("--job-type", help="窗口估算按 job_type 过滤；current 仍是全局 active 占用口径。")] = None,
    caller_id: Annotated[str | None, typer.Option("--caller-id", help="窗口估算按 caller_id 过滤；current 仍是全局 active 占用口径。")] = None,
    run_id: RunIdOption = None,
    since: Annotated[
        str,
        typer.Option("--since", help="估算窗口，例如 10m。"),
    ] = "10m",
    max_active_jobs: Annotated[
        int | None,
        typer.Option("--max-active-jobs", min=0, help="用于计算 active 占用比例；默认读取环境或 .env。"),
    ] = None,
    worker_pods: Annotated[int | None, typer.Option("--worker-pods", min=1, help="worker Pod 数，用于估算 DB 连接预算。")] = None,
    worker_concurrency: Annotated[int | None, typer.Option("--worker-concurrency", min=1, help="单 worker Pod 并发；默认读取 WORKER_CONCURRENCY。")] = None,
    api_pods: Annotated[int | None, typer.Option("--api-pods", min=1, help="API Pod 数，用于估算 DB 连接预算。")] = None,
    db_max_connections: Annotated[int | None, typer.Option("--db-max-connections", min=1, help="PostgreSQL max_connections，用于估算 DB 连接预算。")] = None,
    db_pool_size: Annotated[int | None, typer.Option("--db-pool-size", min=1, help="API 单 Pod DB_POOL_SIZE；默认读取环境。")] = None,
    db_max_overflow: Annotated[int | None, typer.Option("--db-max-overflow", min=0, help="API 单 Pod DB_MAX_OVERFLOW；默认读取环境。")] = None,
    db_usable_ratio: Annotated[
        float,
        typer.Option("--db-usable-ratio", min=0.1, max=1.0, help="可用于应用的 DB 连接比例；默认 0.8。"),
    ] = 0.8,
    record_scope: ScopeOption = "root",
    json_output: JsonOption = False,
) -> None:
    try:
        parsed_scope = parse_record_scope(record_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    limit = max_active_jobs if max_active_jobs is not None else _env_max_active_jobs()
    payload = _capacity_payload(
        since=since,
        job_type=job_type,
        caller_id=caller_id,
        run_id=run_id,
        record_scope=parsed_scope,
        max_active_jobs=limit,
        worker_pods=worker_pods,
        worker_concurrency=worker_concurrency,
        api_pods=api_pods,
        db_max_connections=db_max_connections,
        db_pool_size=db_pool_size,
        db_max_overflow=db_max_overflow,
        db_usable_ratio=db_usable_ratio,
    )
    if json_output:
        formatters.print_json(payload)
        return
    _render_capacity_human(payload)


@app.command(help="查看当前注册的 job_type。", epilog=TYPES_HELP_EPILOG)
def types(
    json_output: JsonOption = False,
    all_types: Annotated[
        bool,
        typer.Option("--all", help="显示全部 job_type；人读输出默认只展示非 internal 的 root 入口。"),
    ] = False,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="按 visibility 过滤：public、demo 或 internal。"),
    ] = None,
    role: Annotated[
        str | None,
        typer.Option("--role", help="按 role 过滤：root、leaf 或 root_or_leaf。"),
    ] = None,
) -> None:
    try:
        specs = _registered_job_type_specs()
    except Exception as exc:
        print(f"ERROR: job type registry unavailable: {exc}", file=sys.stderr)
        raise typer.Exit(4) from exc
    specs, applied_filters = _filter_job_type_specs(
        specs,
        all_types=all_types,
        visibility=visibility,
        role=role,
        default_human_catalog=not json_output,
    )
    if json_output:
        formatters.print_json({"job_types": specs, "applied_filters": applied_filters})
        return
    rows = [
        {
            "job_type": spec["job_type"],
            "visibility": spec["visibility"],
            "role": spec["role"],
            "params_schema": spec["params_schema"],
            "public_result_schema": spec["public_result_schema"],
            "allow_callback": spec["allow_callback"],
            "retry_policy": _retry_policy_summary(spec),
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
            ("visibility", "visibility"),
            ("role", "role"),
            ("params_schema", "params_schema"),
            ("public_result_schema", "public_result_schema"),
            ("allow_callback", "callback"),
            ("retry_policy", "retry"),
            ("timeout_seconds", "timeout"),
        ],
    )
    if applied_filters["default_human_catalog"]:
        print(
            "NOTE      showing non-internal role=root catalog; use --all for the full registry.",
        )


if __name__ == "__main__":
    app(prog_name="jobs.sh")
