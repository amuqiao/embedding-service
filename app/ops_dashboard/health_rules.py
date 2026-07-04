from __future__ import annotations

from typing import Any


def _count(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def health_verdict(
    *,
    summary: dict[str, Any],
    stuck: list[dict[str, Any]],
    callbacks: list[dict[str, Any]],
    filters: Any | None = None,
) -> dict[str, Any]:
    jobs = summary.get("jobs") or {}
    dispatch = summary.get("dispatch") or {}
    reasons: list[str] = []
    severity = "ok"

    if stuck:
        severity = "critical"
        reasons.append("stuck_jobs")
    if _count(jobs.get("failed")):
        if severity != "critical":
            severity = "warning"
        reasons.append("failed_jobs")
    if _count(dispatch.get("dead_letter")):
        severity = "critical"
        reasons.append("dispatch_dead_letter")
    if any(_count(row.get("due")) for row in callbacks):
        if severity != "critical":
            severity = "warning"
        reasons.append("callback_due")
    if any(row.get("status") == "dead_letter" and _count(row.get("count")) for row in callbacks):
        severity = "critical"
        reasons.append("callback_dead_letter")

    return {
        "status": severity,
        "reasons": reasons,
        "next_checks": next_checks_for(reasons, filters=filters),
    }


def _jobs_cli_filter_args(filters: Any | None) -> str:
    if filters is None:
        return ""
    args = ""
    if getattr(filters, "job_type", None):
        args += f" --job-type {filters.job_type}"
    if getattr(filters, "caller_id", None):
        args += f" --caller-id {filters.caller_id}"
    if getattr(filters, "run_id", None):
        args += f" --run-id {filters.run_id}"
    return args


def next_checks_for(reasons: list[str], *, filters: Any | None = None) -> list[str]:
    window = getattr(filters, "window", "1h")
    filter_args = _jobs_cli_filter_args(filters)
    checks: list[str] = []
    if "stuck_jobs" in reasons:
        checks.append(f"./scripts/jobs.sh stuck --since {window} --older-than 10m{filter_args}")
    if "failed_jobs" in reasons:
        checks.append(f"./scripts/jobs.sh failures --since {window}{filter_args}")
    if "callback_due" in reasons or "callback_dead_letter" in reasons:
        checks.append(f"./scripts/jobs.sh callbacks-summary --since {window}{filter_args}")
    if not checks:
        checks.append(f"./scripts/jobs.sh dashboard --since {window}{filter_args}")
    return checks
