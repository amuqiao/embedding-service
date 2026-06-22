import os
import json
import subprocess
from pathlib import Path

import pytest

from scripts.jobs.cli import parse_duration, parse_statuses
from scripts.jobs.db import normalize_database_url
from scripts.verify.env_config_check import (
    SCRIPT_OR_DEPLOYMENT_ENV_KEYS,
    check_file,
    default_env_files,
)
from scripts.verify.job_workflow_smoke import job_from_envelope


ROOT_DIR = Path(__file__).resolve().parents[1]


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


def test_shell_entrypoints_require_command_without_help():
    for script in ("./scripts/dev.sh", "./scripts/deploy.sh", "./scripts/verify.sh", "./scripts/jobs.sh"):
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
    assert {"arithmetic", "job_test_add", "job_test_echo"} <= job_types
    assert result.stderr == ""


def test_jobs_cli_parses_statuses_and_duration():
    assert parse_statuses(["queued,running", "failed"]) == ["queued", "running", "failed"]
    assert parse_duration("10m").total_seconds() == 600

    with pytest.raises(ValueError):
        parse_statuses(["cancelled"])


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
