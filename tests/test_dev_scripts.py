import os
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripts.jobs import queries
from scripts.jobs.cli import app as jobs_cli_app
from scripts.jobs.cli import _capacity_recommendation, parse_duration, parse_latency_group_by, parse_statuses
from scripts.jobs.db import normalize_database_url
from scripts.verify.env_config_check import (
    SCRIPT_OR_DEPLOYMENT_ENV_KEYS,
    check_file,
    default_env_files,
)
from scripts.verify.job_workflow_smoke import job_from_envelope
from scripts.verify.workflow_modes_smoke import WORKFLOW_MODE_CASES, _validate_result


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()


def _service_command(service: str, **env_overrides: str) -> str:
    env = os.environ.copy()
    env.update(env_overrides)
    result = subprocess.run(
        ["bash", "-lc", f"source scripts/dev/services.sh >/dev/null; service_command {service}"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _api_service_command(**env_overrides: str) -> str:
    return _service_command("api", **env_overrides)


def test_dev_api_service_command_uses_uvicorn_reload_when_enabled():
    command = _api_service_command(DEV_API_RELOAD="true", WATCHFILES_FORCE_POLLING="true")

    assert ".venv/bin/python" in command
    assert "-m uvicorn" in command
    assert "app.main:app" in command
    assert "--reload" in command
    assert "WATCHFILES_FORCE_POLLING=true" in command
    assert "start-api.sh" not in command


def test_dev_api_service_command_uses_start_api_by_default():
    command = _api_service_command()

    assert "start-api.sh" in command
    assert "--reload" not in command
    assert ".venv/bin/uvicorn" not in command


@pytest.mark.parametrize("flag", ["DISABLE_HTTP_AUTH_HEADER", "DISABLE_CALLER_ID_HEADER"])
def test_start_api_rejects_public_bind_when_auth_headers_are_disabled(flag):
    env = os.environ.copy()
    env.update({"API_HOST": "0.0.0.0", flag: "true"})

    result = subprocess.run(
        ["./start-api.sh"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "API_HOST must be 127.0.0.1" in result.stderr


@pytest.mark.parametrize("flag", ["DISABLE_HTTP_AUTH_HEADER", "DISABLE_CALLER_ID_HEADER"])
def test_dev_start_rejects_public_bind_when_auth_headers_are_disabled(flag):
    env = os.environ.copy()
    env.update({"API_HOST": "0.0.0.0", flag: "true"})

    result = subprocess.run(
        ["bash", "-lc", "source scripts/dev/services.sh >/dev/null; start_service api"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "API_HOST must be 127.0.0.1" in result.stderr


def test_dev_worker_service_command_injects_script_env(tmp_path):
    script_env = tmp_path / "scripts.env"
    script_env.write_text(
        "WORKER_CONCURRENCY=7\nWORKER_LOGLEVEL=DEBUG\nWORKER_RECOVERY_LOOP=false\n",
        encoding="utf-8",
    )

    command = _service_command("worker", SCRIPT_ENV_FILE=str(script_env))

    assert "WORKER_CONCURRENCY=7" in command
    assert "WORKER_LOGLEVEL=DEBUG" in command
    assert "WORKER_RECOVERY_LOOP=false" in command
    assert "start-worker.sh" in command


def test_jobs_cli_help_is_available_without_db():
    result = subprocess.run(
        ["./scripts/jobs.sh", "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "Job 只读查询与排障入口" in result.stdout
    assert "list" in result.stdout
    assert "types" in result.stdout
    assert "summary" in result.stdout
    assert "latency" in result.stdout
    assert "capacity" in result.stdout


def test_real_flow_cli_help_is_available_without_api():
    result = subprocess.run(
        ["./scripts/real-flow.sh", "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "真实业务流程验证入口" in result.stdout
    assert "llm-job-billing" in result.stdout
    assert "llm-job-double-billing" in result.stdout


def test_shell_entrypoints_require_command_without_help():
    for script in (
        "./scripts/dev.sh",
        "./scripts/deploy.sh",
        "./scripts/verify.sh",
        "./scripts/jobs.sh",
        "./scripts/real-flow.sh",
    ):
        result = subprocess.run(
            [script],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2
        assert result.stdout == ""
        assert "用法：" in result.stderr or "Usage:" in result.stderr


def test_jobs_types_json_is_machine_readable_without_app_log_noise():
    result = subprocess.run(
        ["./scripts/jobs.sh", "types", "--json"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    job_types = {item["job_type"] for item in payload["job_types"]}
    assert {"arithmetic", "job_test_add", "job_test_echo", "job_test_collect", "job_test_workflow"} <= job_types
    assert "job_real_llm_echo" in job_types
    assert "job_real_llm_double_echo" in job_types
    assert result.stderr == ""


def test_jobs_cli_parses_statuses_and_duration():
    assert parse_statuses(["queued,running", "failed"]) == ["queued", "running", "failed"]
    assert parse_duration("10m").total_seconds() == 600
    assert parse_latency_group_by("job_type") == "job_type"

    with pytest.raises(ValueError):
        parse_statuses(["cancelled"])

    with pytest.raises(ValueError):
        parse_latency_group_by("worker")


def test_jobs_capacity_recommendation_reports_gate_pressure():
    payload = {
        "current": {"active_jobs": 760},
        "estimated": {"active_jobs_needed_upper_bound": 800},
    }

    recommendation = _capacity_recommendation(payload, 750)

    assert recommendation["active_ratio"] > 1
    assert "达到或超过门禁" in recommendation["message"]


def test_jobs_summary_json_uses_stable_scope(monkeypatch):
    def fake_with_connection(action):
        return {
            "jobs": {"active_jobs": 0},
            "by_job_type": [],
            "attempts": {},
            "dispatch": {},
            "callbacks": {},
        }

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["summary", "--since", "10m", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["scope"]["since"] == "10m"
    assert payload["jobs"]["active_jobs"] == 0


def test_jobs_latency_json_uses_lifecycle_fields(monkeypatch):
    def fake_with_connection(action):
        return [
            {
                "group_key": "job_test_echo",
                "total": 2,
                "lifecycle_p95_seconds": 15.0,
            }
        ]

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["latency", "--since", "30m", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["scope"]["since"] == "30m"
    assert payload["latency"][0]["lifecycle_p95_seconds"] == 15.0
    assert "active_p95_seconds" not in payload["latency"][0]


def test_jobs_capacity_json_separates_global_current_from_window(monkeypatch):
    def fake_with_connection(action):
        return {
            "current": {"active_jobs": 12, "queued": 10, "running_active": 2},
            "window": {"accepted_jobs": 20, "terminal_jobs": 18, "lifecycle_p95_seconds": 15.0},
            "estimated": {"active_jobs_needed_upper_bound": 5.0},
        }

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["capacity", "--since", "10m", "--max-active-jobs", "50", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["scope"]["current"] == "global"
    assert payload["scope"]["window"]["since"] == "10m"
    assert payload["current"]["active_jobs"] == 12
    assert payload["window"]["lifecycle_p95_seconds"] == 15.0
    assert payload["estimated"]["active_jobs_needed_upper_bound"] == 5.0
    assert payload["estimated"]["active_ratio"] == 0.24
    assert "workflow root" in payload["notes"]["active_jobs_needed_upper_bound"]


def test_jobs_latency_rejects_invalid_group_by():
    result = RUNNER.invoke(jobs_cli_app, ["latency", "--group-by", "worker", "--json"])

    assert result.exit_code == 2
    assert "无效 group-by" in result.stderr


def test_jobs_summary_dispatch_counts_only_run_attempt_task(monkeypatch):
    captured_sql: list[str] = []

    def fake_fetch_one(conn, sql, params):
        captured_sql.append(sql)
        return {}

    monkeypatch.setattr(queries, "_fetch_one", fake_fetch_one)
    monkeypatch.setattr(queries, "_fetch_all", lambda conn, sql, params: [])

    queries.summary(None, job_type=None, caller_id=None, since=None)

    dispatch_sql = captured_sql[2]
    assert "FROM dispatch_outbox d" in dispatch_sql
    assert "d.task_name = 'jobs.run_attempt'" in dispatch_sql


def test_jobs_db_normalizes_async_database_url_for_psycopg2():
    normalized = normalize_database_url(
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/app",
        db_ssl="false",
    )

    assert normalized == "postgresql://postgres:postgres@127.0.0.1:25432/app?sslmode=disable"


def test_env_config_check_rejects_env_file_keys_missing_from_manifest(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DEV_API_RELOAD=false\nWATCHFILES_FORCE_POLLING=false\n", encoding="utf-8")

    issues = check_file(env_file)

    assert issues == [
        f"{env_file}:1: unknown config key: DEV_API_RELOAD",
        f"{env_file}:2: unknown config key: WATCHFILES_FORCE_POLLING",
    ]


def test_env_config_check_rejects_script_keys_inside_env_example(tmp_path):
    env_file = tmp_path / ".env.example"
    env_file.write_text("API_PORT=8100\nWORKER_CONCURRENCY=4\n", encoding="utf-8")

    issues = check_file(env_file)

    assert "API_PORT" in SCRIPT_OR_DEPLOYMENT_ENV_KEYS
    assert "WORKER_CONCURRENCY" in SCRIPT_OR_DEPLOYMENT_ENV_KEYS
    assert issues == [
        f"{env_file}:1: script key must be set in scripts/.env, not application env: API_PORT",
        f"{env_file}:2: script key must be set in scripts/.env, not application env: WORKER_CONCURRENCY",
    ]


def test_verify_check_uses_default_env_config_scan():
    result = subprocess.run(
        [
            "bash",
            "-lc",
            """
            source scripts/verify/tasks.sh >/dev/null
            run_script_syntax() { :; }
            run_cli_smoke() { :; }
            run_python_syntax() { :; }
            run_registry_check() { :; }
            run_tests() { :; }
            run_env_config_check() {
              printf 'env-config-argc=%s\\n' "$#"
              for arg in "$@"; do
                printf 'env-config-arg=%s\\n' "$arg"
              done
            }
            run_check
            """,
        ],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "env-config-argc=0" in result.stdout
    assert "env-config-arg=" not in result.stdout


def test_env_config_default_scan_includes_env_variants():
    relative_paths = {
        path.relative_to(ROOT_DIR).as_posix()
        for path in default_env_files()
        if path.is_relative_to(ROOT_DIR)
    }

    assert ".env.example" in relative_paths
    assert "scripts/.env.example" in relative_paths
    assert any(path.startswith("scripts/.env") for path in relative_paths)


def test_workflow_smoke_accepts_standard_string_success_code():
    job = {"job_id": "job-1", "job_status": "succeeded"}

    parsed = job_from_envelope(
        {
            "code": "0",
            "msg": "success",
            "data": {"job": job},
            "request_id": "req-1",
            "server_time": "2026-06-22T00:00:00+00:00",
        }
    )

    assert parsed is job


def test_verify_sh_documents_workflow_modes_smoke_command():
    result = subprocess.run(
        ["./scripts/verify.sh", "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    verify_sh = (ROOT_DIR / "scripts/verify.sh").read_text(encoding="utf-8")
    tasks_sh = (ROOT_DIR / "scripts/verify/tasks.sh").read_text(encoding="utf-8")

    assert "workflow-modes-smoke" in result.stdout
    assert "workflow-modes-smoke)" in verify_sh
    assert "run_workflow_modes_smoke" in verify_sh
    assert "run_workflow_modes_smoke()" in tasks_sh


def test_workflow_modes_smoke_validates_successful_root_result():
    case = WORKFLOW_MODE_CASES[0]
    job = {
        "job_id": "job-1",
        "job_status": "succeeded",
        "job_result": {
            "schema_version": 1,
            "job_type": "job_test_workflow",
            "workflow": {
                "workflow_type": "job_test_workflow",
                "outcome": "success",
                "node_count": case.expected_node_count,
                "nodes": [
                    {
                        "node_key": key,
                        "job_id": f"child-{key}",
                        "status": "succeeded",
                        "result": {"message": key, "repeated": [key], "count": 1},
                    }
                    for key in case.expected_node_keys
                ],
            },
        },
    }

    _validate_result(job, case)


def test_workflow_modes_smoke_rejects_missing_child_node():
    case = WORKFLOW_MODE_CASES[0]
    job = {
        "job_id": "job-1",
        "job_status": "succeeded",
        "job_result": {
            "schema_version": 1,
            "job_type": "job_test_workflow",
            "workflow": {
                "workflow_type": "job_test_workflow",
                "outcome": "success",
                "node_count": case.expected_node_count,
                "nodes": [
                    {
                        "node_key": case.expected_node_keys[0],
                        "job_id": "child-1",
                        "status": "succeeded",
                        "result": {"message": "a", "repeated": ["a"], "count": 1},
                    }
                ],
            },
        },
    }

    with pytest.raises(RuntimeError, match="wrong node keys"):
        _validate_result(job, case)


def test_workflow_modes_smoke_rejects_invalid_child_result_shape():
    case = WORKFLOW_MODE_CASES[4]
    job = {
        "job_id": "job-1",
        "job_status": "succeeded",
        "job_result": {
            "schema_version": 1,
            "job_type": "job_test_workflow",
            "workflow": {
                "workflow_type": "job_test_workflow",
                "outcome": "success",
                "node_count": case.expected_node_count,
                "nodes": [
                    {
                        "node_key": key,
                        "job_id": f"child-{key}",
                        "status": "succeeded",
                        "result": {"a": 1, "b": 2, "result": 999},
                    }
                    for key in case.expected_node_keys
                ],
            },
        },
    }

    with pytest.raises(RuntimeError, match="invalid add result"):
        _validate_result(job, case)
