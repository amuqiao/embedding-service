import os
import subprocess
from pathlib import Path

from scripts.verify.env_config_check import (
    DEPLOYMENT_OR_SCRIPT_KEYS,
    check_file,
    settings_keys_from_config,
)
from scripts.verify.job_workflow_smoke import job_from_envelope


ROOT_DIR = Path(__file__).resolve().parents[1]


def _api_service_command(**env_overrides: str) -> str:
    env = os.environ.copy()
    env.update(env_overrides)
    result = subprocess.run(
        ["bash", "-lc", "source scripts/dev/services.sh >/dev/null; service_command api"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


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


def test_env_config_check_rejects_dev_runtime_reload_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DEV_API_RELOAD=false\nWATCHFILES_FORCE_POLLING=false\n", encoding="utf-8")

    issues = check_file(env_file, settings_keys_from_config() | DEPLOYMENT_OR_SCRIPT_KEYS)

    assert issues == [
        f"{env_file}:1: unknown config key: DEV_API_RELOAD",
        f"{env_file}:2: unknown config key: WATCHFILES_FORCE_POLLING",
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
