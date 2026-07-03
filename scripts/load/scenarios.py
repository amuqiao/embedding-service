from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ScenarioKind = Literal["job_submit", "job_query", "job_flow", "api_request"]


@dataclass(frozen=True)
class LoadScenario:
    key: str
    title: str
    question: str
    kind: ScenarioKind
    target: str
    default_job_type: str | None = None
    default_http_method: str | None = None
    default_http_path: str | None = None
    writes_jobs: bool = False
    requires_job_ids: bool = False
    billable_risk: bool = False
    default_time: str = "60s"
    default_users: int = 4
    default_spawn_rate: float = 1.0
    default_flow_timeout_seconds: float = 45.0
    default_poll_interval_seconds: float = 0.5
    default_wait_min_seconds: float = 0.1
    default_wait_max_seconds: float = 1.0
    post_checks: tuple[str, ...] = ()


SCENARIOS: dict[str, LoadScenario] = {
    "job-flow": LoadScenario(
        key="job-flow",
        title="Job 完整链路压测",
        question="创建 Job 后，worker 能否按预期消费并在轮询窗口内进入终态？",
        kind="job_flow",
        target="job",
        default_job_type="job_test_echo",
        writes_jobs=True,
        default_time="60s",
        default_users=4,
        default_spawn_rate=1.0,
        post_checks=("drain", "pressure"),
    ),
    "job-submit": LoadScenario(
        key="job-submit",
        title="Job 接单压测",
        question="POST /jobs 接单、DB 写入和 dispatch publish 是否成为瓶颈？",
        kind="job_submit",
        target="job",
        default_job_type="job_test_echo",
        writes_jobs=True,
        default_time="30s",
        default_users=20,
        default_spawn_rate=10.0,
        post_checks=("drain", "pressure"),
    ),
    "job-query": LoadScenario(
        key="job-query",
        title="Job 查询压测",
        question="GET /jobs/{job_id} 在轮询压力下的 p95/p99 和错误率如何？",
        kind="job_query",
        target="job",
        default_job_type=None,
        requires_job_ids=True,
        default_time="60s",
        default_users=50,
        default_spawn_rate=10.0,
        post_checks=("pressure",),
    ),
    "workflow-flow": LoadScenario(
        key="workflow-flow",
        title="Workflow Job 完整链路压测",
        question="root orchestration、child fan-out 和 root finalize 是否能闭环？",
        kind="job_flow",
        target="workflow",
        default_job_type="job_test_workflow",
        writes_jobs=True,
        default_time="60s",
        default_users=4,
        default_spawn_rate=1.0,
        default_flow_timeout_seconds=90.0,
        post_checks=("drain", "pressure"),
    ),
    "api-health": LoadScenario(
        key="api-health",
        title="Health API 压测",
        question="/health 在基础 HTTP 压力下是否稳定？",
        kind="api_request",
        target="api",
        default_http_method="GET",
        default_http_path="/health",
        default_time="30s",
        default_users=20,
        default_spawn_rate=10.0,
        post_checks=(),
    ),
}


def get_scenario(key: str) -> LoadScenario:
    try:
        return SCENARIOS[key]
    except KeyError as exc:
        allowed = ", ".join(sorted(SCENARIOS))
        raise ValueError(f"unknown load scenario: {key}; expected one of: {allowed}") from exc


def scenario_rows() -> list[dict[str, object]]:
    return [
        {
            "key": item.key,
            "target": item.target,
            "writes_jobs": item.writes_jobs,
            "requires_job_ids": item.requires_job_ids,
            "default_job_type": item.default_job_type or "-",
            "question": item.question,
        }
        for item in SCENARIOS.values()
    ]
