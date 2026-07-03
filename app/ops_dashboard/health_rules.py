from __future__ import annotations

from typing import Any


def _count(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def health_verdict(*, summary: dict[str, Any], stuck: list[dict[str, Any]], callbacks: list[dict[str, Any]]) -> dict[str, Any]:
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
        "next_checks": next_checks_for(reasons),
    }


def next_checks_for(reasons: list[str]) -> list[str]:
    checks: list[str] = []
    if "stuck_jobs" in reasons:
        checks.append("./scripts/jobs.sh stuck --since 1h --older-than 10m")
    if "failed_jobs" in reasons:
        checks.append("./scripts/jobs.sh failures --since 1h")
    if "callback_due" in reasons or "callback_dead_letter" in reasons:
        checks.append("./scripts/jobs.sh callbacks-summary --since 1h")
    if not checks:
        checks.append("./scripts/jobs.sh dashboard --since 1h")
    return checks
