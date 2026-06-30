import os
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripts.jobs import queries
from scripts.jobs import formatters as job_formatters
from scripts.jobs.cli import app as jobs_cli_app
from scripts.jobs.cli import (
    _api_log_payload,
    _capacity_recommendation,
    _locust_payload,
    _pressure_payload,
    parse_duration,
    parse_latency_group_by,
    parse_statuses,
)
from scripts.jobs.db import normalize_database_url
from scripts.verify.env_config_check import (
    APPLICATION_ENV_KEYS,
    LAUNCHER_ENV_KEYS,
    check_file,
    default_env_files,
)
from scripts.verify.job_workflow_smoke import job_from_envelope
from scripts.verify.workflow_modes_smoke import WORKFLOW_MODE_CASES, _validate_result


ROOT_DIR = Path(__file__).resolve().parents[1]


def _workflow_mode_case(mode: str):
    return next(case for case in WORKFLOW_MODE_CASES if case.mode == mode)


RUNNER = CliRunner()


def _clean_application_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("ENV_FILE", None)
    for key in APPLICATION_ENV_KEYS:
        env.pop(key, None)
    return env


def _clean_root_env() -> dict[str, str]:
    env = _clean_application_env()
    for key in LAUNCHER_ENV_KEYS:
        env.pop(key, None)
    return env


def _service_command(service: str, **env_overrides: str) -> str:
    env = _clean_root_env()
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


def _write_fake_command(bin_dir: Path, name: str, body: str) -> Path:
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


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
    env = _clean_root_env()
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


def test_start_api_does_not_read_env_file_directly():
    script = (ROOT_DIR / "start-api.sh").read_text(encoding="utf-8")

    assert "ENV_FILE" not in script
    assert "grep -E" not in script


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


@pytest.mark.parametrize("flag", ["DISABLE_HTTP_AUTH_HEADER", "DISABLE_CALLER_ID_HEADER"])
def test_dev_start_rejects_public_bind_when_env_file_disables_auth_headers(tmp_path, flag):
    env_file = tmp_path / ".env.test"
    env_file.write_text(f"{flag}=true\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({"API_HOST": "0.0.0.0", "ENV_FILE": str(env_file)})
    env.pop(flag, None)

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


def test_dev_local_env_guard_reads_selected_env_file(tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/app",
                "REDIS_URL=redis://redis.example.com:6379/0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = _clean_root_env()
    env["ENV_FILE"] = str(env_file)

    result = subprocess.run(
        ["bash", "-lc", "source scripts/lib/common.sh >/dev/null; guard_local_env"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 3
    assert f"REDIS_URL in {env_file}" in result.stderr


def test_dev_migrate_rejects_database_name_mismatch(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/app_a",
                "REDIS_URL=redis://127.0.0.1:26379/0",
                "POSTGRES_DB=app_b",
                "POSTGRES_HOST_PORT=25432",
                "REDIS_HOST_PORT=26379",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = _clean_root_env()
    env["ENV_FILE"] = str(env_file)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "exit 0\n",
    )
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "-c", "source scripts/dev/services.sh >/dev/null; migrate"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "DATABASE_URL database (app_a) must match POSTGRES_DB (app_b)" in result.stderr
    assert "== Database ==" not in result.stdout


def test_dev_start_target_rejects_database_port_mismatch(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:25433/app",
                "REDIS_URL=redis://127.0.0.1:26379/0",
                "POSTGRES_DB=app",
                "POSTGRES_HOST_PORT=25432",
                "REDIS_HOST_PORT=26379",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = _clean_root_env()
    env["ENV_FILE"] = str(env_file)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "exit 0\n",
    )
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "-c", "source scripts/dev/services.sh >/dev/null; start_target api"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "DATABASE_URL port (25433) must match POSTGRES_HOST_PORT (25432)" in result.stderr
    assert "== Application ==" not in result.stdout


def test_dev_start_target_worker_returns_success_without_api_health_wait():
    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    "source scripts/dev/services.sh >/dev/null",
                    "assert_no_compose_full_app_running_for_local() { :; }",
                    "guard_local_env() { :; }",
                    "assert_local_config_consistency() { :; }",
                    "section() { :; }",
                    "start_service() { [[ \"$1\" == worker ]]; }",
                    "wait_for_api() { exit 99; }",
                    "start_target worker",
                ]
            ),
        ],
        cwd=ROOT_DIR,
        env=_clean_root_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0


def test_dev_stop_service_returns_success_after_removing_stale_pid(tmp_path):
    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("999999\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    "source scripts/dev/services.sh >/dev/null",
                    "service_pid_file() { printf '%s'; }" % pid_file,
                    "local_service_pids() { :; }",
                    "event() { :; }",
                    "stop_service worker",
                ]
            ),
        ],
        cwd=ROOT_DIR,
        env=_clean_root_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert not pid_file.exists()


def test_dev_stop_worker_keeps_residual_fail_fast_outside_restart(tmp_path):
    pid_file = tmp_path / "worker.pid"

    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    "source scripts/dev/services.sh >/dev/null",
                    "service_pid_file() { printf '%s'; }" % pid_file,
                    "local_service_pids() { printf '12345\\n'; }",
                    "event() { :; }",
                    "stop_service worker",
                ]
            ),
        ],
        cwd=ROOT_DIR,
        env=_clean_root_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "worker residual local processes are still running" in result.stderr


def test_dev_restart_worker_waits_for_residual_processes_before_continuing(tmp_path):
    pid_file = tmp_path / "worker.pid"
    events_file = tmp_path / "events.txt"
    calls_file = tmp_path / "calls.txt"

    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    "source scripts/dev/services.sh >/dev/null",
                    "service_pid_file() { printf '%s'; }" % pid_file,
                    "printf '0' > '%s'" % calls_file,
                    (
                        "local_service_pids() { "
                        "calls=$(cat '%s'); "
                        "calls=$((calls + 1)); "
                        "printf '%%s' \"$calls\" > '%s'; "
                        "if (( calls <= 2 )); then printf '12345\\n'; fi; "
                        "}"
                    ) % (calls_file, calls_file),
                    "terminate_service_residuals() { echo unexpected residual kill >&2; exit 98; }",
                    f"event() {{ printf '%s %s %s\\n' \"$1\" \"$2\" \"${{3:-}}\" >> '{events_file}'; }}",
                    "stop_service worker restart",
                ]
            ),
        ],
        cwd=ROOT_DIR,
        env=_clean_root_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    events = events_file.read_text(encoding="utf-8")
    assert "WAITING worker residual pid=12345 reason=missing_pid_file" in events
    assert "STOPPED worker already stopped" in events


def test_dev_restart_worker_refuses_to_kill_unknown_residual_writer(tmp_path):
    pid_file = tmp_path / "worker.pid"
    calls_file = tmp_path / "calls.txt"

    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    "source scripts/dev/services.sh >/dev/null",
                    "service_pid_file() { printf '%s'; }" % pid_file,
                    "local_service_pids() { printf '12345\\n'; }",
                    "wait_for_service_residual_exit() { return 1; }",
                    "ps() { printf 'tail -f logs/worker.log\\n'; }",
                    f"event() {{ printf '%s %s %s\\n' \"$1\" \"$2\" \"${{3:-}}\" >> '{calls_file}'; }}",
                    "stop_service worker restart",
                ]
            ),
        ],
        cwd=ROOT_DIR,
        env=_clean_root_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "not a recognized worker process" in result.stderr


def test_dev_restart_worker_ignores_residual_pid_that_exits_before_ps(tmp_path):
    pid_file = tmp_path / "worker.pid"
    signals_file = tmp_path / "signals.txt"

    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    "source scripts/dev/services.sh >/dev/null",
                    "service_pid_file() { printf '%s'; }" % pid_file,
                    "local_service_pids() { printf '12345\\n'; }",
                    "wait_for_service_residual_exit() { return 1; }",
                    "ps() { :; }",
                    f"kill() {{ printf '%s %s\\n' \"$1\" \"$2\" >> '{signals_file}'; }}",
                    "event() { :; }",
                    "stop_service worker restart",
                ]
            ),
        ],
        cwd=ROOT_DIR,
        env=_clean_root_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "not a recognized worker process" not in result.stderr
    assert not signals_file.exists()
    assert "after cleanup" in result.stderr


def test_dev_restart_worker_stale_pid_file_enters_residual_cleanup(tmp_path):
    pid_file = tmp_path / "worker.pid"
    events_file = tmp_path / "events.txt"
    pid_file.write_text("999999\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    "source scripts/dev/services.sh >/dev/null",
                    "service_pid_file() { printf '%s'; }" % pid_file,
                    "kill() { return 1; }",
                    "local_service_pids() { printf '23456\\n'; }",
                    "wait_for_service_residual_exit() { return 0; }",
                    f"event() {{ printf '%s %s %s\\n' \"$1\" \"$2\" \"${{3:-}}\" >> '{events_file}'; }}",
                    "stop_service worker restart",
                ]
            ),
        ],
        cwd=ROOT_DIR,
        env=_clean_root_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert not pid_file.exists()
    events = events_file.read_text(encoding="utf-8")
    assert "STALE worker removed pid=999999" in events
    assert "WAITING worker residual pid=23456 reason=stale_pid_file" in events


def test_dev_restart_worker_after_stop_cleans_recognized_residual_process(tmp_path):
    pid_file = tmp_path / "worker.pid"
    events_file = tmp_path / "events.txt"
    term_file = tmp_path / "term.txt"
    wait_calls_file = tmp_path / "wait-calls.txt"
    pid_file.write_text("999999\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    "source scripts/dev/services.sh >/dev/null",
                    "service_pid_file() { printf '%s'; }" % pid_file,
                    f"kill() {{ if [[ \"$1\" == '-0' ]]; then return 0; fi; printf '%s %s\\n' \"$1\" \"$2\" >> '{term_file}'; }}",
                    "wait_for_pid_exit() { return 0; }",
                    "local_service_pids() { printf '23456\\n'; }",
                    "printf '0' > '%s'" % wait_calls_file,
                    (
                        "wait_for_service_residual_exit() { "
                        "calls=$(cat '%s'); "
                        "calls=$((calls + 1)); "
                        "printf '%%s' \"$calls\" > '%s'; "
                        "(( calls >= 2 )); "
                        "}"
                    ) % (wait_calls_file, wait_calls_file),
                    "ps() { printf '/Users/admin/Downloads/Code/fastapi-best-ai-architecture/start-worker.sh\\n'; }",
                    f"event() {{ printf '%s %s %s\\n' \"$1\" \"$2\" \"${{3:-}}\" >> '{events_file}'; }}",
                    "stop_service worker restart",
                ]
            ),
        ],
        cwd=ROOT_DIR,
        env=_clean_root_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "-TERM 23456" in term_file.read_text(encoding="utf-8")
    events = events_file.read_text(encoding="utf-8")
    assert "WAITING worker residual pid=23456 reason=after_stop" in events
    assert "STOPPING worker residual pid=23456" in events


def test_dev_restart_worker_escalates_residual_cleanup_to_kill(tmp_path):
    pid_file = tmp_path / "worker.pid"
    signals_file = tmp_path / "signals.txt"

    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    "source scripts/dev/services.sh >/dev/null",
                    "service_pid_file() { printf '%s'; }" % pid_file,
                    "local_service_pids() { printf '34567\\n'; }",
                    "wait_for_service_residual_exit() { return 1; }",
                    "ps() { printf 'taskiq worker app.tasks.taskiq_app:broker --workers 1\\n'; }",
                    f"kill() {{ printf '%s %s\\n' \"$1\" \"$2\" >> '{signals_file}'; }}",
                    "event() { :; }",
                    "stop_service worker restart",
                ]
            ),
        ],
        cwd=ROOT_DIR,
        env=_clean_root_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    signals = signals_file.read_text(encoding="utf-8")
    assert "-TERM 34567" in signals
    assert "-KILL 34567" in signals
    assert "after cleanup" in result.stderr


def test_dev_worker_service_command_injects_root_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WORKER_CONCURRENCY=7\nWORKER_LOGLEVEL=DEBUG\nWORKER_RECOVERY_LOOP=false\n",
        encoding="utf-8",
    )

    command = _service_command("worker", ENV_FILE=str(env_file))

    assert "WORKER_CONCURRENCY=7" in command
    assert "WORKER_LOGLEVEL=DEBUG" in command
    assert "WORKER_RECOVERY_LOOP=false" in command
    assert "start-worker.sh" in command


def test_start_worker_without_recovery_loop_does_not_require_python(tmp_path):
    script = tmp_path / "start-worker.sh"
    script.write_text((ROOT_DIR / "start-worker.sh").read_text(encoding="utf-8"), encoding="utf-8")
    script.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "dirname",
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  */*) printf '%s\\n' \"${1%/*}\" ;;\n"
        "  *) printf '.\\n' ;;\n"
        "esac\n",
    )
    _write_fake_command(
        fake_bin,
        "taskiq",
        "#!/bin/sh\n"
        "printf 'taskiq %s\\n' \"$*\"\n"
        "exit 0\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "PATH": str(fake_bin),
            "WORKER_RECOVERY_LOOP": "false",
            "WORKER_CONCURRENCY": "1",
            "WORKER_LOGLEVEL": "INFO",
        }
    )

    result = subprocess.run(
        [str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0
    assert "taskiq worker app.tasks.taskiq_app:broker" in result.stdout
    assert "python not found" not in result.stderr


def test_compose_wrapper_injects_root_env_file_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TEMPLATE_NAME=template-from-env",
                "COMPOSE_PROJECT_NAME=project-from-env",
                "POSTGRES_DB=db_from_env",
                "POSTGRES_HOST_PORT=35432",
                "REDIS_HOST_PORT=36379",
                "API_HOST_PORT=38100",
                "WORKER_CONCURRENCY=6",
                "WORKER_LOGLEVEL=DEBUG",
                "WORKER_RECOVERY_LOOP=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "printf '%s\\n' \"COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME\"\n"
        "printf '%s\\n' \"ENV_FILE=$ENV_FILE\"\n"
        "printf '%s\\n' \"POSTGRES_DB=$POSTGRES_DB\"\n"
        "printf '%s\\n' \"POSTGRES_HOST_PORT=$POSTGRES_HOST_PORT\"\n"
        "printf '%s\\n' \"REDIS_HOST_PORT=$REDIS_HOST_PORT\"\n"
        "printf '%s\\n' \"API_HOST_PORT=$API_HOST_PORT\"\n"
        "printf '%s\\n' \"WORKER_CONCURRENCY=$WORKER_CONCURRENCY\"\n"
        "printf '%s\\n' \"WORKER_LOGLEVEL=$WORKER_LOGLEVEL\"\n"
        "printf '%s\\n' \"WORKER_RECOVERY_LOOP=$WORKER_RECOVERY_LOOP\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = _clean_root_env()
    env["ENV_FILE"] = str(env_file)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "-c", "source scripts/lib/compose.sh >/dev/null; compose config"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "COMPOSE_PROJECT_NAME=project-from-env" in result.stdout
    assert f"ENV_FILE={env_file}" in result.stdout
    assert "POSTGRES_DB=db_from_env" in result.stdout
    assert "POSTGRES_HOST_PORT=35432" in result.stdout
    assert "REDIS_HOST_PORT=36379" in result.stdout
    assert "API_HOST_PORT=38100" in result.stdout
    assert "WORKER_CONCURRENCY=6" in result.stdout
    assert "WORKER_LOGLEVEL=DEBUG" in result.stdout
    assert "WORKER_RECOVERY_LOOP=false" in result.stdout


def test_deploy_check_rejects_compose_project_used_by_other_working_dir(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_PROJECT_NAME=shared-project\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"compose config\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"compose\" && \"$2 $3 $4\" == \"--profile app config\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"ps -a\" ]]; then\n"
        "  if [[ \"$*\" == *\"label=com.docker.compose.project=shared-project\"* ]]; then\n"
        "    printf '/srv/other-service\\n'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    env = _clean_root_env()
    env.update({"ENV_FILE": str(env_file), "PATH": f"{fake_bin}:{env['PATH']}"})

    result = subprocess.run(
        ["./scripts/deploy.sh", "check"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "COMPOSE_PROJECT_NAME conflict" in result.stderr
    assert "shared-project" in result.stderr
    assert "/srv/other-service" in result.stderr
    assert str(ROOT_DIR) in result.stderr


def test_deploy_check_allows_compose_project_from_current_working_dir(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_PROJECT_NAME=current-project\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"compose config\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"compose\" && \"$2 $3 $4\" == \"--profile app config\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"ps -a\" ]]; then\n"
        "  if [[ \"$*\" == *\"label=com.docker.compose.project=current-project\"* ]]; then\n"
        f"    printf '%s\\n' '{ROOT_DIR}'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    env = _clean_root_env()
    env.update({"ENV_FILE": str(env_file), "PATH": f"{fake_bin}:{env['PATH']}"})

    result = subprocess.run(
        ["./scripts/deploy.sh", "check"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "OK        compose-project no working_dir conflict" in result.stdout


def test_deploy_check_allows_current_working_dir_symlink_path(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_PROJECT_NAME=current-project\n", encoding="utf-8")
    repo_link = tmp_path / "repo-link"
    repo_link.symlink_to(ROOT_DIR, target_is_directory=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"compose config\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"compose\" && \"$2 $3 $4\" == \"--profile app config\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"ps -a\" ]]; then\n"
        "  if [[ \"$*\" == *\"label=com.docker.compose.project=current-project\"* ]]; then\n"
        f"    printf '%s\\n' '{repo_link}'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    env = _clean_root_env()
    env.update({"ENV_FILE": str(env_file), "PATH": f"{fake_bin}:{env['PATH']}"})

    result = subprocess.run(
        ["./scripts/deploy.sh", "check"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "OK        compose-project no working_dir conflict" in result.stdout


def test_deploy_check_uses_runtime_compose_project_override(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_PROJECT_NAME=file-project\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"compose config\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"compose\" && \"$2 $3 $4\" == \"--profile app config\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"ps -a\" ]]; then\n"
        "  if [[ \"$*\" == *\"label=com.docker.compose.project=runtime-project\"* ]]; then\n"
        "    printf '/srv/runtime-owner\\n'\n"
        "  fi\n"
        "  if [[ \"$*\" == *\"label=com.docker.compose.project=file-project\"* ]]; then\n"
        "    printf '/srv/file-owner\\n'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "COMPOSE_PROJECT_NAME": "runtime-project",
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["./scripts/deploy.sh", "check"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "runtime-project" in result.stderr
    assert "/srv/runtime-owner" in result.stderr
    assert "file-project" not in result.stderr
    assert "/srv/file-owner" not in result.stderr


def test_deploy_check_requires_docker_cli_for_project_conflict_check(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_PROJECT_NAME=standalone-project\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker-compose",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == \"config --quiet\" ]]; then exit 0; fi\n"
        "if [[ \"$*\" == \"--profile app config --quiet\" ]]; then exit 0; fi\n"
        "exit 1\n",
    )
    env = _clean_root_env()
    env.update({"ENV_FILE": str(env_file), "PATH": f"{fake_bin}:/bin:/usr/bin"})

    result = subprocess.run(
        ["./scripts/deploy.sh", "check"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "docker is required for COMPOSE_PROJECT_NAME conflict check" in result.stderr


def test_local_mode_rejects_running_compose_full_app(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$*\" == \"compose --profile app ps api\"* ]]; then printf 'running\\n'; exit 0; fi\n"
        "if [[ \"$*\" == \"compose --profile app ps worker\"* ]]; then exit 0; fi\n"
        "exit 0\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RUN_DIR": str(tmp_path / "run"),
            "LOG_DIR": str(tmp_path / "logs"),
        }
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            "source scripts/dev/services.sh >/dev/null; assert_no_compose_full_app_running_for_local",
        ],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "compose-full app services are running: api" in result.stderr
    assert "./scripts/deploy.sh down compose-full" in result.stderr


def test_local_mode_rejects_compose_full_app_from_same_repo_different_project(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"ps\" && \"$*\" == *\"com.docker.compose.service=worker\"* ]]; then printf 'other-project-worker-1\\n'; exit 0; fi\n"
        "if [[ \"$*\" == \"compose --profile app ps api\"* ]]; then exit 0; fi\n"
        "if [[ \"$*\" == \"compose --profile app ps worker\"* ]]; then exit 0; fi\n"
        "exit 0\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RUN_DIR": str(tmp_path / "run"),
            "LOG_DIR": str(tmp_path / "logs"),
            "COMPOSE_PROJECT_NAME": "current-project",
        }
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            "source scripts/dev/services.sh >/dev/null; assert_no_compose_full_app_running_for_local",
        ],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "compose-full app services are running: worker" in result.stderr


def test_deploy_compose_full_status_allows_clean_local_mode(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$*\" == \"compose --profile app ps\" ]]; then printf 'compose ps clean\\n'; exit 0; fi\n"
        "exit 0\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RUN_DIR": str(tmp_path / "run"),
            "LOG_DIR": str(tmp_path / "logs"),
        }
    )

    result = subprocess.run(
        ["bash", "-c", "./scripts/deploy.sh status compose-full"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "== Compose Full ==" in result.stdout
    assert "compose ps clean" in result.stdout
    assert "Mode Guard" not in result.stdout


def test_deploy_compose_full_up_allows_clean_local_mode(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"ps -a\" ]]; then exit 0; fi\n"
        "if [[ \"$*\" == \"compose --profile app up -d --build api worker\" ]]; then printf 'compose up clean\\n'; exit 0; fi\n"
        "exit 0\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RUN_DIR": str(tmp_path / "run"),
            "LOG_DIR": str(tmp_path / "logs"),
        }
    )

    result = subprocess.run(
        ["bash", "-c", "./scripts/deploy.sh up compose-full"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "== Compose Full ==" in result.stdout
    assert "compose up clean" in result.stdout


def test_deploy_compose_full_up_rejects_project_name_conflict(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_PROJECT_NAME=shared-project\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == \"ps -a\" ]]; then\n"
        "  if [[ \"$*\" == *\"label=com.docker.compose.project=shared-project\"* ]]; then\n"
        "    printf '/srv/other-service\\n'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$*\" == \"compose --profile app up -d --build api worker\" ]]; then printf 'unexpected up\\n'; exit 0; fi\n"
        "exit 1\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RUN_DIR": str(tmp_path / "run"),
            "LOG_DIR": str(tmp_path / "logs"),
        }
    )

    result = subprocess.run(
        ["bash", "-c", "./scripts/deploy.sh up compose-full"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "COMPOSE_PROJECT_NAME conflict" in result.stderr
    assert "/srv/other-service" in result.stderr
    assert "unexpected up" not in result.stdout
    assert "== Compose Full ==" not in result.stdout


def test_deploy_up_requires_env_file(tmp_path):
    env_file = tmp_path / ".env.missing"
    env = _clean_root_env()
    env["ENV_FILE"] = str(env_file)

    result = subprocess.run(
        ["bash", "-c", "./scripts/deploy.sh up compose-deps"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert f"{env_file} not found" in result.stderr
    assert "== Compose Deps ==" not in result.stdout


def test_compose_full_rejects_local_worker_residual_log_writer(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    worker_log = logs_dir / "worker.log"
    worker_log.write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "lsof",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *\"worker.log\"* ]]; then\n"
        "  printf 'COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\\n'\n"
        "  printf 'python3.1 12345 admin 1w REG 1,18 1 1 %s\\n' \"$4\"\n"
        "fi\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RUN_DIR": str(tmp_path / "run"),
            "LOG_DIR": str(logs_dir),
        }
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            "source scripts/lib/modes.sh >/dev/null; assert_no_local_app_running_for_compose_full",
        ],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "local app processes are running: worker pid=12345" in result.stderr
    assert "./scripts/dev.sh stop" in result.stderr


def test_compose_full_residual_detection_requires_lsof_when_logs_exist(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "worker.log").write_text("", encoding="utf-8")
    env = _clean_root_env()
    env.update(
        {
            "PATH": "/bin:/usr/bin",
            "RUN_DIR": str(tmp_path / "run"),
            "LOG_DIR": str(logs_dir),
        }
    )

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            "source scripts/lib/modes.sh >/dev/null; assert_no_local_app_running_for_compose_full",
        ],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "lsof is required for local process residual detection" in result.stderr


def test_status_guard_warns_when_compose_full_app_is_running(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "docker",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1 $2\" == \"compose version\" ]]; then exit 0; fi\n"
        "if [[ \"$*\" == \"compose --profile app ps api\"* ]]; then exit 0; fi\n"
        "if [[ \"$*\" == \"compose --profile app ps worker\"* ]]; then printf 'running\\n'; exit 0; fi\n"
        "exit 0\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RUN_DIR": str(tmp_path / "run"),
            "LOG_DIR": str(tmp_path / "logs"),
        }
    )

    result = subprocess.run(
        ["bash", "-c", "source scripts/dev/services.sh >/dev/null; warn_if_compose_full_app_running"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "WARN" in result.stdout
    assert "compose-full" in result.stdout
    assert "worker" in result.stdout


def test_status_guard_warns_when_local_worker_residual_is_running(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "worker.log").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "lsof",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *\"worker.log\"* ]]; then\n"
        "  printf 'COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\\n'\n"
        "  printf 'python3.1 23456 admin 1w REG 1,18 1 1 %s\\n' \"$4\"\n"
        "fi\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RUN_DIR": str(tmp_path / "run"),
            "LOG_DIR": str(logs_dir),
        }
    )

    result = subprocess.run(
        ["bash", "-c", "source scripts/lib/modes.sh >/dev/null; warn_if_local_app_running"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "WARN" in result.stdout
    assert "local" in result.stdout
    assert "worker pid=23456" in result.stdout


def test_dev_status_service_reports_residual_worker_process(tmp_path):
    logs_dir = tmp_path / "logs"
    run_dir = tmp_path / "run"
    logs_dir.mkdir()
    run_dir.mkdir()
    (logs_dir / "worker.log").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin,
        "lsof",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *\"worker.log\"* ]]; then\n"
        "  printf 'COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\\n'\n"
        "  printf 'python3.1 34567 admin 1w REG 1,18 1 1 %s\\n' \"$4\"\n"
        "fi\n",
    )
    env = _clean_root_env()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RUN_DIR": str(run_dir),
            "LOG_DIR": str(logs_dir),
        }
    )

    result = subprocess.run(
        ["bash", "-c", "source scripts/dev/services.sh >/dev/null; status_service worker"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "worker" in result.stdout
    assert "residual" in result.stdout
    assert "pid=34567" in result.stdout


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
    assert "oss-upload-image" in result.stdout
    assert "poster-title-image" in result.stdout


def test_k8s_cli_help_is_available_without_db():
    result = subprocess.run(
        ["./scripts/k8s.sh", "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "K8s Pod 内手动运维入口" in result.stdout
    assert "check postgres" in result.stdout
    assert "check redis" in result.stdout
    assert "check oss --confirm" in result.stdout
    assert "PUT / GET / HEAD" in result.stdout
    assert "PUT / GET / HEAD / DELETE" not in result.stdout
    assert "current" in result.stdout
    assert "heads" in result.stdout
    assert "migrate --confirm" in result.stdout


def test_k8s_check_oss_prints_url_ref_without_delete_requirement():
    script = (ROOT_DIR / "scripts" / "k8s.sh").read_text(encoding="utf-8")
    oss_check = script.split("run_check_oss() {", 1)[1].split("\n}\n\nrun_check()", 1)[0]

    assert "oss_url_ref_from_output_object" in oss_check
    assert "OSS_TEST_PUBLIC_URL" in oss_check
    assert "OSS_TEST_INTERNAL_URL" in oss_check
    assert "OSS_TEST_CONTENT_TYPE" in oss_check
    assert "OSS_TEST_SHA256" in oss_check
    assert ".delete_object(" not in oss_check
    assert "delete_checked=false" in oss_check


def test_k8s_check_oss_requires_confirm_before_remote_write():
    env = os.environ.copy()
    env["KUBERNETES_SERVICE_HOST"] = "127.0.0.1"

    result = subprocess.run(
        ["./scripts/k8s.sh", "check", "oss"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "check oss requires --confirm" in result.stderr
    assert "deletes" not in result.stderr
    assert "OSS_ACCESS_KEY" not in result.stdout
    assert "OSS_ACCESS_KEY" not in result.stderr


def test_tools_cli_help_is_available_without_env_file():
    result = subprocess.run(
        ["./scripts/tools.sh", "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "本地开发辅助工具入口" in result.stdout
    assert "secret" in result.stdout
    assert "无默认持久副作用" in result.stdout


def test_shell_entrypoints_require_command_without_help():
    for script in (
        "./scripts/dev.sh",
        "./scripts/deploy.sh",
        "./scripts/verify.sh",
        "./scripts/k8s.sh",
        "./scripts/jobs.sh",
        "./scripts/real-flow.sh",
        "./scripts/tools.sh",
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
    specs = {item["job_type"]: item for item in payload["job_types"]}
    assert specs["poster_title_image"]["visibility"] == "public"
    assert specs["poster_title_image"]["role"] == "root"
    assert specs["job_test_collect"]["visibility"] == "demo"
    assert specs["job_test_collect"]["role"] == "leaf"
    assert payload["applied_filters"] == {
        "all": False,
        "visibility": None,
        "role": None,
        "default_human_catalog": False,
    }
    assert result.stderr == ""


def test_jobs_types_human_output_defaults_to_root_catalog():
    result = subprocess.run(
        ["./scripts/jobs.sh", "types"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "poster_title_image" in result.stdout
    assert "job_test_workflow" in result.stdout
    assert "job_test_collect" not in result.stdout
    assert "job_test_echo" not in result.stdout
    assert "visibility" in result.stdout
    assert "role" in result.stdout
    assert "use --all for the full registry" in result.stdout
    assert result.stderr == ""


def test_jobs_types_json_filters_by_visibility_and_role():
    result = subprocess.run(
        ["./scripts/jobs.sh", "types", "--json", "--visibility", "demo", "--role", "leaf"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert [item["job_type"] for item in payload["job_types"]] == ["job_test_collect"]
    assert payload["applied_filters"] == {
        "all": False,
        "visibility": "demo",
        "role": "leaf",
        "default_human_catalog": False,
    }
    assert result.stderr == ""


def test_jobs_types_rejects_invalid_filter_value():
    result = subprocess.run(
        ["./scripts/jobs.sh", "types", "--json", "--role", "worker"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Invalid value for --role" in result.stderr


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


def _summary_payload(
    *,
    total: int = 2,
    queued: int = 0,
    running_active: int = 0,
    failed: int = 0,
    dispatch_due: int = 0,
    dispatch_dead_letter: int = 0,
    callbacks_due: int = 0,
    callbacks_dead_letter: int = 0,
) -> dict:
    return {
        "jobs": {
            "total": total,
            "queued": queued,
            "running": running_active,
            "running_active": running_active,
            "running_inactive": 0,
            "succeeded": max(total - queued - running_active - failed, 0),
            "failed": failed,
            "active_jobs": queued + running_active,
            "oldest_created_at": None,
            "newest_created_at": None,
        },
        "by_job_type": [
            {
                "job_type": "job_test_echo",
                "total": total,
                "queued": queued,
                "running": running_active,
                "active_jobs": queued + running_active,
                "succeeded": max(total - queued - running_active - failed, 0),
                "failed": failed,
            }
        ]
        if total
        else [],
        "attempts": {"total": total, "pending": queued, "running": running_active, "succeeded": 0, "failed": failed},
        "dispatch": {
            "total": total,
            "pending": dispatch_due,
            "leased": 0,
            "published": max(total - dispatch_due - dispatch_dead_letter, 0),
            "retrying": 0,
            "dead_letter": dispatch_dead_letter,
            "due": dispatch_due,
        },
        "callbacks": {
            "total": total,
            "pending": callbacks_due,
            "leased": 0,
            "delivering": 0,
            "delivered": max(total - callbacks_due - callbacks_dead_letter, 0),
            "failed": 0,
            "dead_letter": callbacks_dead_letter,
            "due": callbacks_due,
        },
    }


def _capacity_payload(
    *,
    active_jobs: int = 0,
    queued: int = 0,
    running_active: int = 0,
    accepted_jobs: int = 0,
    terminal_jobs: int = 0,
    lifecycle_p95_seconds: float | None = None,
    active_ratio: float | None = None,
    active_jobs_needed_upper_bound: float | None = None,
) -> dict:
    return {
        "scope": {"current": "global", "window": {"since": "20m", "seconds": 1200.0}},
        "max_active_jobs": 1000,
        "current": {"active_jobs": active_jobs, "queued": queued, "running_active": running_active},
        "window": {
            "accepted_jobs": accepted_jobs,
            "terminal_jobs": terminal_jobs,
            "lifecycle_p95_seconds": lifecycle_p95_seconds,
            "window_seconds": 1200.0,
            "observed_span_seconds": 60.0,
            "effective_window_seconds": 60.0,
            "accepted_submit_rps": accepted_jobs / 60 if accepted_jobs else 0,
        },
        "estimated": {
            "active_ratio": active_ratio,
            "headroom": 1000 - active_jobs,
            "active_jobs_needed_upper_bound": active_jobs_needed_upper_bound,
        },
    }


def _pressure_input(
    *,
    summary_payload: dict | None = None,
    capacity_payload: dict | None = None,
    latency: list[dict] | None = None,
    stuck: list[dict] | None = None,
    failure_groups: list[dict] | None = None,
    active_samples: list[dict] | None = None,
    failed_samples: list[dict] | None = None,
) -> dict:
    return {
        "summary": _summary_payload(total=0) if summary_payload is None else summary_payload,
        "capacity": _capacity_payload() if capacity_payload is None else capacity_payload,
        "latency": [] if latency is None else latency,
        "stuck": [] if stuck is None else stuck,
        "failure_groups": [] if failure_groups is None else failure_groups,
        "active_samples": [] if active_samples is None else active_samples,
        "failed_samples": [] if failed_samples is None else failed_samples,
    }


def test_jobs_summary_default_is_human_readable(monkeypatch):
    monkeypatch.setattr("scripts.jobs.cli._with_connection", lambda action: _summary_payload(queued=1, running_active=1))

    result = RUNNER.invoke(jobs_cli_app, ["summary", "--since", "10m"])

    assert result.exit_code == 0
    assert "== Job Summary ==" in result.stdout
    assert "== Attempts ==" in result.stdout
    assert "running" in result.stdout
    assert "succeeded" in result.stdout
    assert "== Dispatch ==" in result.stdout
    assert "published" in result.stdout
    assert "== Callbacks ==" in result.stdout
    assert "delivering" in result.stdout
    assert "delivered" in result.stdout
    assert "job_test_echo" in result.stdout
    assert '"scope"' not in result.stdout


def test_jobs_summary_json_uses_stable_scope(monkeypatch):
    def fake_with_connection(action):
        return _summary_payload(total=0)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["summary", "--since", "10m", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["scope"]["since"] == "10m"
    assert payload["jobs"]["active_jobs"] == 0
    assert set(payload) >= {"scope", "jobs", "by_job_type", "attempts", "dispatch", "callbacks"}


def test_jobs_inspect_include_children_human_output(monkeypatch):
    calls: list[str] = []

    def fake_with_connection(action):
        monkeypatch.setattr(queries, "get_job", lambda _conn, _job_id: {"id": "root-job-1", "status": "running"})
        monkeypatch.setattr(queries, "attempts", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "callbacks", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "timeline", lambda _conn, _job_id, *, limit: [])

        def fake_child_jobs(_conn, _root_job_id):
            calls.append("child_jobs")
            return [
                {
                    "workflow_node_key": "item.es",
                    "job_id": "child-job-1",
                    "status": "running",
                    "job_type": "poster_title_image_generate_item",
                    "progress_percent": 50,
                    "progress_stage": "calling_model",
                    "attempt_status": "running",
                    "dispatch_status": "published",
                }
            ]

        monkeypatch.setattr(queries, "child_jobs", fake_child_jobs)
        return action(None)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["inspect", "root-job-1", "--include-children"])

    assert result.exit_code == 0
    assert calls == ["child_jobs"]
    assert "== Job Inspect ==" in result.stdout
    assert "== Workflow Children ==" in result.stdout
    assert "item.es" in result.stdout
    assert "poster_title_image_generate_item" in result.stdout


def test_jobs_inspect_json_includes_children_only_when_requested(monkeypatch):
    def fake_with_connection(action):
        monkeypatch.setattr(queries, "get_job", lambda _conn, _job_id: {"id": "root-job-1", "status": "running"})
        monkeypatch.setattr(queries, "attempts", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "callbacks", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "timeline", lambda _conn, _job_id, *, limit: [])
        monkeypatch.setattr(
            queries,
            "child_jobs",
            lambda _conn, _root_job_id: [{"workflow_node_key": "probe.0", "job_id": "child-job-1"}],
        )
        return action(None)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    without_children = RUNNER.invoke(jobs_cli_app, ["inspect", "root-job-1", "--json"])
    with_children = RUNNER.invoke(jobs_cli_app, ["inspect", "root-job-1", "--include-children", "--json"])

    assert without_children.exit_code == 0
    assert "children" not in json.loads(without_children.stdout)
    assert with_children.exit_code == 0
    payload = json.loads(with_children.stdout)
    assert payload["children"] == [{"workflow_node_key": "probe.0", "job_id": "child-job-1"}]


def test_jobs_inspect_json_includes_single_job_diagnosis(monkeypatch):
    attempt_id = "attempt-1"

    def fake_with_connection(action):
        monkeypatch.setattr(
            queries,
            "get_job",
            lambda _conn, _job_id: {
                "id": "root-job-1",
                "status": "running",
                "active_attempt_id": attempt_id,
            },
        )
        monkeypatch.setattr(
            queries,
            "attempts",
            lambda _conn, _job_id: [
                {
                    "id": attempt_id,
                    "status": "pending",
                    "dispatch_status": "dead_letter",
                    "dispatch_last_error": {"code": "TASKIQ_PUBLISH_FAILED"},
                }
            ],
        )
        monkeypatch.setattr(
            queries,
            "callbacks",
            lambda _conn, _job_id: [
                {
                    "id": "callback-1",
                    "status": "dead_letter",
                    "last_error": {"code": "CALLBACK_HTTP_ERROR"},
                    "dead_lettered_at": datetime(2026, 6, 29, 7, 0, tzinfo=timezone.utc),
                }
            ],
        )
        monkeypatch.setattr(
            queries,
            "timeline",
            lambda _conn, _job_id, *, limit: [{"event_type": "dispatch.published"}],
        )
        return action(None)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["inspect", "root-job-1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["diagnosis"]["status"] == "critical"
    signals = {item["signal"] for item in payload["diagnosis"]["findings"]}
    assert "dispatch_dead_letter" in signals
    assert "callback_dead_letter" in signals
    assert "./scripts/jobs.sh stuck --older-than 1m --limit 20" in payload["diagnosis"]["next_checks"]


def test_jobs_diagnose_command_outputs_human_and_json(monkeypatch):
    def fake_with_connection(action):
        monkeypatch.setattr(
            queries,
            "get_job",
            lambda _conn, _job_id: {
                "id": "root-job-1",
                "status": "queued",
                "active_attempt_id": "attempt-1",
            },
        )
        monkeypatch.setattr(
            queries,
            "attempts",
            lambda _conn, _job_id: [
                {
                    "id": "attempt-1",
                    "status": "pending",
                    "dispatch_status": "published",
                    "published_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
                }
            ],
        )
        monkeypatch.setattr(queries, "callbacks", lambda _conn, _job_id: [])
        monkeypatch.setattr(
            queries,
            "timeline",
            lambda _conn, _job_id, *, limit: [{"event_type": "dispatch.published"}],
        )
        monkeypatch.setattr(queries, "child_jobs", lambda _conn, _root_job_id: [])
        return action(None)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    human = RUNNER.invoke(jobs_cli_app, ["diagnose", "root-job-1"])
    json_result = RUNNER.invoke(jobs_cli_app, ["diagnose", "root-job-1", "--json"])

    assert human.exit_code == 0
    assert "== Job Diagnosis ==" in human.stdout
    assert "published_dispatch_not_claimed" in human.stdout
    assert '"diagnosis"' not in human.stdout
    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["job_id"] == "root-job-1"
    assert payload["diagnosis"]["status"] == "warning"
    assert payload["diagnosis"]["findings"][0]["signal"] == "published_dispatch_not_claimed"
    assert payload["diagnosis"]["findings"][0]["evidence"]["stale"] is True


def test_jobs_diagnose_keeps_fresh_published_attempt_as_info(monkeypatch):
    def fake_with_connection(action):
        monkeypatch.setattr(
            queries,
            "get_job",
            lambda _conn, _job_id: {
                "id": "root-job-1",
                "status": "queued",
                "active_attempt_id": "attempt-1",
            },
        )
        monkeypatch.setattr(
            queries,
            "attempts",
            lambda _conn, _job_id: [
                {
                    "id": "attempt-1",
                    "status": "pending",
                    "dispatch_status": "published",
                    "published_at": datetime(2999, 1, 1, tzinfo=timezone.utc),
                }
            ],
        )
        monkeypatch.setattr(queries, "callbacks", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "timeline", lambda _conn, _job_id, *, limit: [])
        return action(None)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["diagnose", "root-job-1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["diagnosis"]["status"] == "info"
    finding = payload["diagnosis"]["findings"][0]
    assert finding["signal"] == "published_dispatch_not_claimed"
    assert finding["evidence"]["stale"] is False


def test_jobs_diagnose_keeps_fresh_dispatch_and_callback_due_as_info(monkeypatch):
    def fake_with_connection(action):
        monkeypatch.setattr(
            queries,
            "get_job",
            lambda _conn, _job_id: {
                "id": "root-job-1",
                "status": "queued",
                "active_attempt_id": "attempt-1",
            },
        )
        monkeypatch.setattr(
            queries,
            "attempts",
            lambda _conn, _job_id: [
                {
                    "id": "attempt-1",
                    "status": "pending",
                    "dispatch_status": "pending",
                    "next_attempt_at": None,
                    "created_at": datetime(2999, 1, 1, tzinfo=timezone.utc),
                }
            ],
        )
        monkeypatch.setattr(
            queries,
            "callbacks",
            lambda _conn, _job_id: [
                {
                    "id": "callback-1",
                    "status": "pending",
                    "next_attempt_at": None,
                    "created_at": datetime(2999, 1, 1, tzinfo=timezone.utc),
                }
            ],
        )
        monkeypatch.setattr(queries, "timeline", lambda _conn, _job_id, *, limit: [])
        return action(None)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["diagnose", "root-job-1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["diagnosis"]["status"] == "info"
    findings = {item["signal"]: item for item in payload["diagnosis"]["findings"]}
    assert findings["dispatch_due"]["severity"] == "info"
    assert findings["dispatch_due"]["evidence"]["stale"] is False
    assert findings["callback_due"]["severity"] == "info"
    assert findings["callback_due"]["evidence"]["stale"] is False


def test_jobs_diagnose_keeps_succeeded_job_failed_attempt_history_as_info(monkeypatch):
    def fake_with_connection(action):
        monkeypatch.setattr(
            queries,
            "get_job",
            lambda _conn, _job_id: {
                "id": "root-job-1",
                "status": "succeeded",
                "active_attempt_id": "attempt-2",
            },
        )
        monkeypatch.setattr(
            queries,
            "attempts",
            lambda _conn, _job_id: [
                {
                    "id": "attempt-1",
                    "status": "failed",
                    "error_kind": "retryable",
                    "failure_phase": "execute",
                    "retry_decision": "retry",
                }
            ],
        )
        monkeypatch.setattr(queries, "callbacks", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "timeline", lambda _conn, _job_id, *, limit: [])
        return action(None)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["diagnose", "root-job-1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["diagnosis"]["status"] == "info"
    finding = payload["diagnosis"]["findings"][0]
    assert finding["signal"] == "attempt_failed"
    assert finding["severity"] == "info"


def test_jobs_diagnose_includes_children_only_when_requested(monkeypatch):
    child_job_calls = []

    def fake_with_connection(action):
        monkeypatch.setattr(
            queries,
            "get_job",
            lambda _conn, _job_id: {
                "id": "root-job-1",
                "status": "running",
                "active_attempt_id": None,
            },
        )
        monkeypatch.setattr(queries, "attempts", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "callbacks", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "timeline", lambda _conn, _job_id, *, limit: [])

        def fake_child_jobs(_conn, _root_job_id):
            child_job_calls.append("child_jobs")
            return [{"job_id": "child-job-1", "status": "running"}]

        monkeypatch.setattr(queries, "child_jobs", fake_child_jobs)
        return action(None)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    without_children = RUNNER.invoke(jobs_cli_app, ["diagnose", "root-job-1", "--json"])
    with_children = RUNNER.invoke(jobs_cli_app, ["diagnose", "root-job-1", "--include-children", "--json"])

    assert without_children.exit_code == 0
    assert with_children.exit_code == 0
    assert child_job_calls == ["child_jobs"]
    without_payload = json.loads(without_children.stdout)
    with_payload = json.loads(with_children.stdout)
    assert without_payload["diagnosis"]["findings"][0]["signal"] == "job_waiting_children_unchecked"
    assert with_payload["diagnosis"]["findings"][0]["signal"] == "job_waiting_children"


def test_jobs_inspect_human_summarizes_workflow_plan(monkeypatch):
    long_prompt = "Analyze title image. " * 40

    def fake_with_connection(action):
        monkeypatch.setattr(
            queries,
            "get_job",
            lambda _conn, _job_id: {
                "id": "root-job-1",
                "status": "running",
                "runtime_ref": {
                    "name": "runtime",
                    "type": "json",
                    "payload": {
                        "job_type": "poster_title_image",
                        "workflow_plan": {
                            "kind": "dag_lite",
                            "workflow_type": "poster_title_image",
                            "workflow_version": 1,
                            "failure_policy": "fail_fast",
                            "node_count": 1,
                            "max_nodes": 101,
                            "nodes": [
                                {
                                    "key": "probe.0",
                                    "job_type": "poster_title_image_style_probe",
                                    "depends_on": [],
                                    "required": True,
                                    "weight": 1,
                                    "job_params": {"style_prompt": long_prompt},
                                }
                            ],
                        },
                    },
                },
                "canonical_result": {
                    "job_type": "poster_title_image",
                    "workflow": {
                        "workflow_type": "poster_title_image",
                        "workflow_version": 1,
                        "outcome": "success",
                        "failure_policy": "fail_fast",
                        "node_count": 1,
                        "succeeded": 1,
                        "failed": 0,
                        "nodes": [{"result": {"style_desc": long_prompt}}],
                    },
                },
            },
        )
        monkeypatch.setattr(queries, "attempts", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "callbacks", lambda _conn, _job_id: [])
        monkeypatch.setattr(queries, "timeline", lambda _conn, _job_id, *, limit: [])
        return action(None)

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    human = RUNNER.invoke(jobs_cli_app, ["inspect", "root-job-1"])
    json_result = RUNNER.invoke(jobs_cli_app, ["inspect", "root-job-1", "--json"])

    assert human.exit_code == 0
    assert "style_prompt" not in human.stdout
    assert "style_desc" not in human.stdout
    assert "== Workflow Plan ==" in human.stdout
    assert "nodes=1" in human.stdout
    assert "probe.0" in human.stdout
    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["job"]["runtime_ref"]["payload"]["workflow_plan"]["nodes"][0]["job_params"]["style_prompt"] == long_prompt
    assert payload["job"]["canonical_result"]["workflow"]["nodes"][0]["result"]["style_desc"] == long_prompt


def test_jobs_show_default_is_human_readable(monkeypatch):
    monkeypatch.setattr(
        "scripts.jobs.cli._with_connection",
        lambda action: {
            "id": "root-job-1",
            "status": "succeeded",
            "job_type": "poster_title_image",
            "caller_id": "default",
            "progress_percent": 100,
            "progress_stage": "completed",
            "callback_status": "not_configured",
            "job_params": {
                "items": [
                    {
                        "item_id": "es",
                        "language": "es",
                        "title_text": "Cuando el amor se alejo",
                    }
                ]
            },
            "result": {"batch_summary": {"total": 1, "succeeded": 1, "failed": 0, "running": 0, "pending": 0}},
        },
    )

    human = RUNNER.invoke(jobs_cli_app, ["show", "root-job-1"])
    json_result = RUNNER.invoke(jobs_cli_app, ["show", "root-job-1", "--json"])

    assert human.exit_code == 0
    assert "== Job ==" in human.stdout
    assert "job_id" in human.stdout
    assert "== Job Items ==" in human.stdout
    assert "== Result Summary ==" in human.stdout
    assert '"payload_summary"' not in human.stdout
    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout)["job"]["job_params"]["items"][0]["title_text"] == "Cuando el amor se alejo"


def test_jobs_capacity_default_is_human_readable(monkeypatch):
    monkeypatch.setattr(
        "scripts.jobs.cli._with_connection",
        lambda action: {
            "current": {"active_jobs": 12, "queued": 10, "running_active": 2},
            "window": {
                "accepted_jobs": 20,
                "terminal_jobs": 18,
                "lifecycle_p95_seconds": 15.0,
                "accepted_submit_rps": 0.2,
                "observed_span_seconds": 100.0,
                "effective_window_seconds": 100.0,
            },
            "estimated": {"active_jobs_needed_upper_bound": 5.0},
        },
    )

    result = RUNNER.invoke(jobs_cli_app, ["capacity", "--since", "10m", "--max-active-jobs", "50"])

    assert result.exit_code == 0
    assert "== Job Capacity ==" in result.stdout
    assert "== Current ==" in result.stdout
    assert "== Window ==" in result.stdout
    assert "== Estimated ==" in result.stdout
    assert '"current"' not in result.stdout


def test_jobs_pressure_default_is_human_readable(monkeypatch):
    monkeypatch.setattr(
        "scripts.jobs.cli._with_connection",
        lambda action: _pressure_input(
            summary_payload=_summary_payload(total=10),
            capacity_payload={
                "current": {"active_jobs": 2, "queued": 1, "running_active": 1},
                "window": {
                    "accepted_jobs": 10,
                    "terminal_jobs": 10,
                    "lifecycle_p95_seconds": 10.0,
                    "accepted_submit_rps": 0.5,
                    "observed_span_seconds": 20.0,
                    "effective_window_seconds": 20.0,
                },
                "estimated": {"active_jobs_needed_upper_bound": 5.0},
            },
        ),
    )

    result = RUNNER.invoke(jobs_cli_app, ["pressure", "--since", "20m", "--max-active-jobs", "50"])

    assert result.exit_code == 0
    assert "== Job Pressure Diagnosis ==" in result.stdout
    assert "== Capacity ==" in result.stdout
    assert "== Current ==" in result.stdout
    assert "== Window ==" in result.stdout
    assert '"capacity"' not in result.stdout


def test_jobs_doctor_default_reports_empty_window_next_checks(monkeypatch):
    monkeypatch.setattr("scripts.jobs.cli._with_connection", lambda action: _summary_payload(total=0))

    result = RUNNER.invoke(jobs_cli_app, ["doctor", "--since", "10m"])

    assert result.exit_code == 0
    assert "no jobs found" in result.stdout
    assert "扩大 --since 窗口" in result.stdout
    assert "./scripts/jobs.sh list --since 10m" in result.stdout
    assert "./scripts/jobs.sh show <job_id>" in result.stdout
    assert "./scripts/dev.sh status" in result.stdout


def test_jobs_doctor_json_treats_empty_window_as_ok(monkeypatch):
    monkeypatch.setattr("scripts.jobs.cli._with_connection", lambda action: _summary_payload(total=0))

    result = RUNNER.invoke(jobs_cli_app, ["doctor", "--since", "10m", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    findings = {item["metric"]: item for item in payload["findings"]}
    assert findings["jobs.total"]["status"] == "info"


def test_jobs_doctor_default_prints_filter_scope(monkeypatch):
    monkeypatch.setattr("scripts.jobs.cli._with_connection", lambda action: _summary_payload(total=0))

    result = RUNNER.invoke(
        jobs_cli_app,
        ["doctor", "--since", "10m", "--job-type", "job_test_echo", "--caller-id", "default"],
    )

    assert result.exit_code == 0
    assert "job_type=job_test_echo" in result.stdout
    assert "caller_id=default" in result.stdout
    assert "./scripts/jobs.sh list --since 10m --job-type job_test_echo --caller-id default --limit 20" in result.stdout


def test_jobs_doctor_json_reports_abnormal_metrics(monkeypatch):
    monkeypatch.setattr(
        "scripts.jobs.cli._with_connection",
        lambda action: _summary_payload(
            total=5,
            queued=2,
            running_active=1,
            failed=1,
            dispatch_due=2,
            dispatch_dead_letter=1,
            callbacks_due=1,
            callbacks_dead_letter=1,
        ),
    )

    result = RUNNER.invoke(jobs_cli_app, ["doctor", "--since", "10m", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) >= {"scope", "summary", "status", "findings", "next_checks"}
    assert payload["scope"]["since"] == "10m"
    assert payload["summary"]["jobs"]["failed"] == 1
    assert payload["status"] == "critical"
    findings = {item["metric"]: item for item in payload["findings"]}
    assert findings["dispatch.dead_letter"]["status"] == "critical"
    assert findings["callbacks.dead_letter"]["status"] == "critical"
    assert findings["jobs.failed"]["status"] == "warning"
    assert findings["jobs.queued"]["value"] == 2
    assert findings["jobs.running_active"]["value"] == 1
    assert "./scripts/jobs.sh stuck --older-than 10m" in payload["next_checks"]


def test_jobs_pressure_payload_detects_db_connection_pressure():
    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        payload=_pressure_input(
            summary_payload=_summary_payload(total=10, failed=1),
            capacity_payload=_capacity_payload(active_jobs=0, accepted_jobs=10, terminal_jobs=10),
            latency=[
                {
                    "group_key": "all",
                    "total": 10,
                    "terminal": 10,
                    "succeeded": 9,
                    "failed": 1,
                    "success_rate": 0.9,
                }
            ],
            failure_groups=[
                {
                    "count": 1,
                    "error_code": "MODEL_CALL_FAILED",
                    "error_kind": "worker_error",
                    "failure_phase": "execute",
                    "detail_type": "TooManyConnectionsError",
                    "detail_message": "sorry, too many clients already",
                }
            ],
        ),
    )

    assert payload["status"] == "critical"
    signals = {item["signal"] for item in payload["bottlenecks"]}
    assert "job_failures" in signals
    assert "db_connection_pressure" in signals


def test_jobs_pressure_payload_does_not_treat_generic_connection_failure_as_db_pressure():
    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        payload=_pressure_input(
            summary_payload=_summary_payload(total=10, failed=1),
            capacity_payload=_capacity_payload(active_jobs=0, accepted_jobs=10, terminal_jobs=10),
            failure_groups=[
                {
                    "count": 1,
                    "error_code": "CALLBACK_FAILED",
                    "error_kind": "callback_error",
                    "failure_phase": "callback",
                    "detail_type": "ConnectionError",
                    "detail_message": "Connection refused by callback endpoint",
                }
            ],
        ),
    )

    signals = {item["signal"] for item in payload["bottlenecks"]}
    assert "job_failures" in signals
    assert "db_connection_pressure" not in signals


def test_jobs_pressure_payload_detects_http_5xx_from_locust():
    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        locust={
            "post_jobs": {"request_count": 100, "failure_count": 2},
            "failure_status_counts": {"500": 2},
            "failures": [],
            "exceptions": [],
        },
        payload=_pressure_input(summary_payload=_summary_payload(total=100), capacity_payload=_capacity_payload()),
    )

    assert payload["status"] == "critical"
    signals = {item["signal"] for item in payload["bottlenecks"]}
    assert "http_5xx" in signals


def test_jobs_pressure_payload_detects_locust_failures_without_job_records():
    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        locust={
            "post_jobs": {"request_count": 10, "failure_count": 10},
            "failure_status_counts": {},
            "failures": [{"Error": "CatchResponseError('Expecting value: line 1 column 1 (char 0)')"}],
            "exceptions": [],
        },
        payload=_pressure_input(summary_payload=_summary_payload(total=0), capacity_payload=_capacity_payload()),
    )

    assert payload["status"] == "critical"
    signals = {item["signal"] for item in payload["bottlenecks"]}
    assert "http_failures_no_job_records" in signals


def test_jobs_pressure_payload_detects_locust_failure_db_mismatch():
    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        locust={
            "post_jobs": {"request_count": 800, "failure_count": 700},
            "failure_status_counts": {},
            "failures": [{"Error": "CatchResponseError('Expecting value: line 1 column 1 (char 0)')"}],
            "exceptions": [],
        },
        payload=_pressure_input(
            summary_payload=_summary_payload(total=100),
            capacity_payload=_capacity_payload(accepted_jobs=100, terminal_jobs=100),
        ),
    )

    assert payload["status"] == "critical"
    signals = {item["signal"] for item in payload["bottlenecks"]}
    assert "http_failures_db_mismatch" in signals


def test_jobs_pressure_payload_detects_missing_locust_csv_prefix(tmp_path):
    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        locust=_locust_payload(str(tmp_path / "missing-run")),
        payload=_pressure_input(
            summary_payload=_summary_payload(total=5),
            capacity_payload=_capacity_payload(active_jobs=0, accepted_jobs=5, terminal_jobs=5, active_ratio=0),
            latency=[{"group_key": "all", "total": 5, "terminal": 5, "succeeded": 5, "failed": 0, "success_rate": 1.0}],
        ),
    )

    assert payload["status"] == "critical"
    signals = {item["signal"] for item in payload["bottlenecks"]}
    assert "locust_csv_missing" in signals


def test_jobs_pressure_payload_detects_api_log_connection_pressure(tmp_path):
    log = tmp_path / "api.log"
    log.write_text("asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already\n", encoding="utf-8")

    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        api_log=_api_log_payload(str(log), tail_lines=100),
        payload=_pressure_input(summary_payload=_summary_payload(total=100), capacity_payload=_capacity_payload()),
    )

    assert payload["status"] == "critical"
    signals = {item["signal"] for item in payload["bottlenecks"]}
    assert "api_log_db_connection_pressure" in signals


def test_jobs_pressure_payload_detects_worker_broker_stuck():
    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        payload=_pressure_input(
            summary_payload=_summary_payload(total=20, queued=2),
            capacity_payload=_capacity_payload(active_jobs=2, queued=2, accepted_jobs=20, terminal_jobs=18),
            stuck=[
                {
                    "issue": "published_dispatch_not_claimed",
                    "job_id": "job-1",
                    "job_status": "queued",
                    "job_type": "job_test_echo",
                }
            ],
        ),
    )

    assert payload["status"] == "critical"
    bottleneck = next(item for item in payload["bottlenecks"] if item["signal"] == "published_dispatch_not_claimed")
    assert bottleneck["area"] == "worker_broker"
    assert "worker/broker" in bottleneck["message"]
    assert payload["stuck"]["sample_count"] == 1
    assert payload["stuck"]["truncated"] is False


def test_jobs_pressure_payload_classifies_callback_stuck_area():
    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        payload=_pressure_input(
            summary_payload=_summary_payload(total=20),
            capacity_payload=_capacity_payload(active_jobs=0, accepted_jobs=20, terminal_jobs=20),
            stuck=[{"issue": "callback_lease_expired", "job_id": "job-1", "job_status": "succeeded"}],
        ),
    )

    bottleneck = next(item for item in payload["bottlenecks"] if item["signal"] == "callback_lease_expired")
    assert bottleneck["area"] == "callback"


def test_jobs_pressure_payload_reports_ok_when_no_bottleneck():
    payload = _pressure_payload(
        since="20m",
        older_than="1m",
        job_type=None,
        caller_id="default",
        max_active_jobs=1000,
        queue_wait_warning_seconds=30,
        run_warning_seconds=60,
        payload=_pressure_input(
            summary_payload=_summary_payload(total=5),
            capacity_payload=_capacity_payload(active_jobs=0, accepted_jobs=5, terminal_jobs=5, active_ratio=0),
            latency=[
                {
                    "group_key": "all",
                    "total": 5,
                    "terminal": 5,
                    "succeeded": 5,
                    "failed": 0,
                    "success_rate": 1.0,
                    "queue_wait_p95_seconds": 1.0,
                    "run_p95_seconds": 2.0,
                }
            ],
        ),
    )

    assert payload["status"] == "ok"
    assert payload["bottlenecks"][0]["signal"] == "no_obvious_bottleneck"


def test_jobs_pressure_json_uses_aggregated_queries(monkeypatch):
    def fake_with_connection(action):
        return _pressure_input(
            summary_payload=_summary_payload(total=2, queued=1),
            capacity_payload=_capacity_payload(active_jobs=1, queued=1, accepted_jobs=2, terminal_jobs=1, active_ratio=0.001),
            latency=[{"group_key": "all", "total": 2, "terminal": 1, "success_rate": 1.0}],
        )

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["pressure", "--since", "20m", "--caller-id", "default", "--max-active-jobs", "1000", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["scope"]["since"] == "20m"
    assert payload["scope"]["caller_id"] == "default"
    assert set(payload) >= {"status", "bottlenecks", "summary", "capacity", "latency", "stuck", "failure_groups", "samples"}


def test_jobs_pressure_reads_locust_csv_prefix(tmp_path):
    prefix = tmp_path / "run"
    (tmp_path / "run_stats.csv").write_text(
        "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%\n"
        "POST,POST /jobs,10,2,100,120,10,300,1,5.0,1.0,100,120,130,140,150,200,220,250,300,300,300\n",
        encoding="utf-8",
    )
    (tmp_path / "run_failures.csv").write_text(
        "Method,Name,Error,Occurrences,First Seen,Last Seen\n"
        "POST,POST /jobs,\"HTTP 500: internal error\",2,now,now\n",
        encoding="utf-8",
    )
    (tmp_path / "run_exceptions.csv").write_text("Count,Message,Traceback,Nodes\n", encoding="utf-8")

    payload = _locust_payload(str(prefix))

    assert payload["available"] is True
    assert payload["post_jobs"]["request_count"] == 10
    assert payload["post_jobs"]["failure_count"] == 2
    assert payload["post_jobs"]["p95_ms"] == 200
    assert payload["failure_status_counts"] == {"500": 2}


def test_jobs_pressure_reports_missing_locust_csv_prefix(tmp_path):
    payload = _locust_payload(str(tmp_path / "missing"))

    assert payload["available"] is False
    assert payload["files"]["stats"]["available"] is False


def test_jobs_api_log_payload_filters_to_since_window(tmp_path):
    log = tmp_path / "api.log"
    log.write_text(
        "2026-06-26 13:00:00,000 old request\n"
        "asyncpg.exceptions.TooManyConnectionsError: old\n"
        "2026-06-26 13:10:00,000 new request\n"
        "asyncpg.exceptions.TooManyConnectionsError: new\n",
        encoding="utf-8",
    )

    payload = _api_log_payload(str(log), tail_lines=100, since_at=datetime(2026, 6, 26, 5, 5, tzinfo=timezone.utc))

    assert payload["matches"]["too_many_connections"] == 1
    assert payload["samples"]["too_many_connections"] == ["asyncpg.exceptions.TooManyConnectionsError: new"]


def test_jobs_stuck_json_includes_scope_filters(monkeypatch):
    captured: dict = {}

    def fake_stuck(conn, **kwargs):
        captured.update(kwargs)
        return [
            {
                "issue": "running_attempt_lease_expired",
                "job_id": "job-1",
                "job_status": "running",
                "job_type": "job_test_echo",
            }
        ]

    monkeypatch.setattr("scripts.jobs.cli._with_connection", lambda action: action(None))
    monkeypatch.setattr(queries, "stuck", fake_stuck)

    result = RUNNER.invoke(
        jobs_cli_app,
        ["stuck", "--older-than", "5m", "--since", "30m", "--caller-id", "default", "--job-type", "job_test_echo", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["scope"] == {
        "older_than": "5m",
        "since": "30m",
        "job_type": "job_test_echo",
        "caller_id": "default",
    }
    assert payload["items"][0]["issue"] == "running_attempt_lease_expired"
    assert captured["caller_id"] == "default"
    assert captured["job_type"] == "job_test_echo"
    assert captured["since"] is not None


def test_jobs_drain_strict_succeeds_when_scope_is_empty(monkeypatch):
    def fake_with_connection(action):
        return {
            "current": {"active_jobs": 0, "queued": 0, "running": 0, "running_active": 0, "running_inactive": 0},
            "window": {
                "total": 4,
                "active_jobs": 0,
                "queued": 0,
                "running": 0,
                "running_active": 0,
                "running_inactive": 0,
                "succeeded": 4,
                "failed": 0,
            },
            "stuck": {"total": 0, "sample": [], "truncated": False},
        }

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["drain", "--since", "30m", "--caller-id", "default", "--strict", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "drained"
    assert payload["scope"]["caller_id"] == "default"


def test_jobs_drain_strict_exits_when_active_remains(monkeypatch):
    def fake_with_connection(action):
        return {
            "current": {"active_jobs": 1, "queued": 1, "running": 0, "running_active": 0, "running_inactive": 0},
            "window": {
                "total": 1,
                "active_jobs": 1,
                "queued": 1,
                "running": 0,
                "running_active": 0,
                "running_inactive": 0,
                "succeeded": 0,
                "failed": 0,
            },
            "stuck": {"total": 0, "sample": [], "truncated": False},
        }

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["drain", "--since", "30m", "--strict", "--json"])

    assert result.exit_code == 4
    payload = json.loads(result.stdout)
    assert payload["status"] == "not_drained"
    assert "active" in payload["message"]


def test_jobs_drain_strict_exits_when_failed_remains(monkeypatch):
    def fake_with_connection(action):
        return {
            "current": {"active_jobs": 0, "queued": 0, "running": 0, "running_active": 0, "running_inactive": 0},
            "window": {
                "total": 1,
                "active_jobs": 0,
                "queued": 0,
                "running": 0,
                "running_active": 0,
                "running_inactive": 0,
                "succeeded": 0,
                "failed": 1,
            },
            "stuck": {"total": 0, "sample": [], "truncated": False},
        }

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["drain", "--since", "30m", "--strict", "--json"])

    assert result.exit_code == 4
    payload = json.loads(result.stdout)
    assert payload["status"] == "not_drained"
    assert "failed Job" in payload["message"]
    assert "./scripts/jobs.sh list --status failed --since 30m --limit 20" in payload["next_checks"]


def test_jobs_drain_strict_exits_when_running_inactive_remains(monkeypatch):
    def fake_with_connection(action):
        return {
            "current": {"active_jobs": 0, "queued": 0, "running": 1, "running_active": 0, "running_inactive": 1},
            "window": {
                "total": 1,
                "active_jobs": 0,
                "queued": 0,
                "running": 1,
                "running_active": 0,
                "running_inactive": 1,
                "succeeded": 0,
                "failed": 0,
            },
            "stuck": {"total": 0, "sample": [], "truncated": False},
        }

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["drain", "--since", "30m", "--strict", "--json"])

    assert result.exit_code == 4
    payload = json.loads(result.stdout)
    assert payload["status"] == "not_drained"
    assert "running_inactive" in payload["message"]


def test_jobs_drain_recommends_unwindowed_active_list_for_old_active(monkeypatch):
    def fake_with_connection(action):
        return {
            "current": {"active_jobs": 1, "queued": 1, "running": 0, "running_active": 0, "running_inactive": 0},
            "window": {
                "total": 0,
                "active_jobs": 0,
                "queued": 0,
                "running": 0,
                "running_active": 0,
                "running_inactive": 0,
                "succeeded": 0,
                "failed": 0,
            },
            "stuck": {"total": 0, "sample": [], "truncated": False},
        }

    monkeypatch.setattr("scripts.jobs.cli._with_connection", fake_with_connection)

    result = RUNNER.invoke(jobs_cli_app, ["drain", "--since", "30m", "--caller-id", "default", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "./scripts/jobs.sh list --status queued,running --caller-id default --limit 20" in payload["next_checks"]
    assert "./scripts/jobs.sh list --status queued,running --since 30m --caller-id default --limit 20" not in payload["next_checks"]


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


def test_jobs_capacity_query_keeps_current_global_when_window_is_filtered(monkeypatch):
    captured_sql: list[str] = []
    captured_params: list[dict] = []

    def fake_fetch_one(conn, sql, params):
        captured_sql.append(sql)
        captured_params.append(params)
        return {}

    monkeypatch.setattr(queries, "_fetch_one", fake_fetch_one)

    queries.capacity(
        None,
        job_type="job_test_echo",
        caller_id="default",
        since="2026-06-26T00:00:00+00:00",
        window_seconds=600,
    )

    current_sql = captured_sql[0]
    window_sql = captured_sql[1]
    assert "j.job_type = %(job_type)s" not in current_sql
    assert "j.caller_id = %(caller_id)s" not in current_sql
    assert captured_params[0] == {}
    assert "j.job_type = %(job_type)s" in window_sql
    assert "j.caller_id = %(caller_id)s" in window_sql


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
    assert "JOIN job_execution_attempts a ON a.id = d.attempt_id" in dispatch_sql
    assert "JOIN job_aggregates j ON j.id = a.job_id" in dispatch_sql
    assert "d.job_id" not in dispatch_sql
    assert "d.task_name = 'jobs.run_attempt'" in dispatch_sql


def test_jobs_stuck_query_accepts_scope_filters(monkeypatch):
    captured_sql: list[str] = []
    captured_params: list[dict] = []

    def fake_fetch_all(conn, sql, params):
        captured_sql.append(sql)
        captured_params.append(params)
        return []

    monkeypatch.setattr(queries, "_fetch_all", fake_fetch_all)

    queries.stuck(
        None,
        older_than=parse_duration("10m"),
        limit=20,
        caller_id="default",
        job_type="job_test_echo",
        since="2026-06-26T00:00:00+00:00",
    )

    sql = captured_sql[0]
    assert sql.count("j.job_type = %(job_type)s") == 5
    assert sql.count("j.caller_id = %(caller_id)s") == 5
    assert sql.count("j.created_at >= %(since)s") == 5
    published_section = sql.split("SELECT 'published_dispatch_not_claimed'", 1)[1].split("UNION ALL", 1)[0]
    assert "d.published_at < %(cutoff)s" in published_section
    assert "d.next_attempt_at <= now()" not in published_section
    assert captured_params[0]["caller_id"] == "default"
    assert captured_params[0]["job_type"] == "job_test_echo"


def test_jobs_timeline_returns_recent_events_in_chronological_display_order(monkeypatch):
    captured_sql: list[str] = []

    def fake_fetch_all(conn, sql, params):
        captured_sql.append(sql)
        return []

    monkeypatch.setattr(queries, "_fetch_all", fake_fetch_all)

    queries.timeline(None, "job-1", limit=10)

    sql = captured_sql[0]
    assert "ORDER BY e.created_at DESC" in sql
    assert "LIMIT %(limit)s" in sql
    assert "ORDER BY created_at ASC" in sql


def test_jobs_child_jobs_query_filters_internal_children(monkeypatch):
    captured_sql: list[str] = []
    captured_params: list[dict] = []

    def fake_fetch_all(conn, sql, params):
        captured_sql.append(sql)
        captured_params.append(params)
        return []

    monkeypatch.setattr(queries, "_fetch_all", fake_fetch_all)

    queries.child_jobs(None, "root-job-1")

    sql = captured_sql[0]
    assert "FROM job_aggregates j" in sql
    assert "j.root_job_id = %(root_job_id)s" in sql
    assert "j.workflow_node_key IS NOT NULL" in sql
    assert "ORDER BY j.created_at ASC" in sql
    assert captured_params[0] == {"root_job_id": "root-job-1"}


def test_jobs_list_and_inspect_map_skipped_callback_status_to_public_semantics(monkeypatch):
    captured_sql: list[str] = []

    def fake_fetch(conn, sql, params):
        captured_sql.append(sql)
        return [] if "LIMIT %(limit)s" in sql else None

    monkeypatch.setattr(queries, "_fetch_all", fake_fetch)
    monkeypatch.setattr(queries, "_fetch_one", fake_fetch)

    queries.list_jobs(
        None,
        statuses=[],
        job_type=None,
        caller_id=None,
        client_request_id=None,
        since=None,
        limit=20,
    )
    queries.get_job(None, "job-1")

    combined_sql = "\n".join(captured_sql)
    assert "WHEN cb.status = 'skipped' AND cb.last_error IS NOT NULL THEN 'failed'" in combined_sql
    assert "WHEN cb.status = 'skipped' THEN 'not_configured'" in combined_sql


def test_jobs_human_payload_summary_truncates_long_strings():
    payload = job_formatters.summarize_job_payload(
        {
            "job_params": {
                "prompt": "x" * 400,
            }
        }
    )

    assert payload["job_params"]["prompt"] == ("x" * 239) + "..."


def test_jobs_db_normalizes_async_database_url_for_psycopg2():
    normalized = normalize_database_url(
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/app",
        db_ssl="false",
    )

    assert normalized == "postgresql://postgres:postgres@127.0.0.1:25432/app?sslmode=disable"


def test_jobs_db_ssl_false_does_not_override_explicit_sslmode():
    normalized = normalize_database_url(
        "postgresql://postgres:postgres@127.0.0.1:25432/app?sslmode=require",
        db_ssl="false",
    )

    assert normalized == "postgresql://postgres:postgres@127.0.0.1:25432/app?sslmode=require"


def test_env_config_check_rejects_env_file_keys_missing_from_manifest(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DEV_API_RELOAD=false\nWATCHFILES_FORCE_POLLING=false\n", encoding="utf-8")

    issues = check_file(env_file)

    assert issues == [
        f"{env_file}:1: unknown config key: DEV_API_RELOAD",
        f"{env_file}:2: unknown config key: WATCHFILES_FORCE_POLLING",
    ]


def test_env_config_check_allows_launcher_keys_inside_env_example(tmp_path):
    env_file = tmp_path / ".env.example"
    env_file.write_text("API_PORT=8100\nWORKER_CONCURRENCY=4\n", encoding="utf-8")

    issues = check_file(env_file)

    assert "API_PORT" in LAUNCHER_ENV_KEYS
    assert "WORKER_CONCURRENCY" in LAUNCHER_ENV_KEYS
    assert issues == []


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
            run_alembic_revision_check() { printf 'alembic-revision-check\\n'; }
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
    assert "alembic-revision-check" in result.stdout


def _release_env_file_content(*, storage_backend: str = "aliyun_oss") -> str:
    lines = [
        "APP_ENV=local",
        "DATABASE_URL=postgresql+asyncpg://postgres:postgres@db.example.com:5432/app",
        "DB_SSL=true",
        "SERVICE_API_KEY=release-service-token-32",
        "DISABLE_HTTP_AUTH_HEADER=false",
        "DISABLE_CALLER_ID_HEADER=false",
        "REDIS_URL=redis://redis.example.com:6379/0",
        "TASKIQ_BROKER_KIND=redis_stream",
        "CALLBACK_SIGNING_SECRET=release-callback-secret-32-bytes",
        "ALLOW_INSECURE_CALLBACKS=false",
        f"STORAGE_BACKEND={storage_backend}",
        "DEFAULT_MODEL_ID=gpt-5.5",
    ]
    if storage_backend == "aliyun_oss":
        lines.extend(
            [
                "OSS_BUCKET=bucket",
                "OSS_REGION=cn-test",
                "OSS_ACCESS_KEY_ID=access-key-id",
                "OSS_ACCESS_KEY_SECRET=access-key-secret",
                "OSS_PROJECT_ROOT=project/root",
            ]
        )
    return "\n".join(lines) + "\n"


def test_env_config_check_validates_selected_env_file_with_app_env_override(tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text(_release_env_file_content(), encoding="utf-8")

    result = subprocess.run(
        ["./scripts/verify.sh", "env-config", "--env-file", str(env_file), "--app-env", "test"],
        cwd=ROOT_DIR,
        env=_clean_application_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "== Env Config ==" in result.stdout
    assert "OK        env-files  checked=1" in result.stdout
    assert "OK        app-config app_env=test release=true" in result.stdout


def test_env_config_check_fails_release_unsafe_config(tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text(_release_env_file_content(storage_backend="local"), encoding="utf-8")

    result = subprocess.run(
        ["./scripts/verify.sh", "env-config", "--env-file", str(env_file), "--app-env", "test"],
        cwd=ROOT_DIR,
        env=_clean_application_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "app config invalid" in result.stderr
    assert "STORAGE_BACKEND=local" in result.stderr


def test_env_config_check_env_file_validation_ignores_root_dotenv(tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "\n".join(
            [
                "APP_ENV=test",
                "DATABASE_URL=postgresql+asyncpg://postgres:postgres@db.example.com:5432/app",
                "DB_SSL=true",
                "DISABLE_HTTP_AUTH_HEADER=false",
                "DISABLE_CALLER_ID_HEADER=false",
                "REDIS_URL=redis://redis.example.com:6379/0",
                "TASKIQ_BROKER_KIND=redis_stream",
                "ALLOW_INSECURE_CALLBACKS=false",
                "STORAGE_BACKEND=aliyun_oss",
                "OSS_BUCKET=bucket",
                "OSS_REGION=cn-test",
                "OSS_ACCESS_KEY_ID=access-key-id",
                "OSS_ACCESS_KEY_SECRET=access-key-secret",
                "OSS_PROJECT_ROOT=project/root",
                "DEFAULT_MODEL_ID=gpt-5.5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["./scripts/verify.sh", "env-config", "--env-file", str(env_file), "--app-env", "test"],
        cwd=ROOT_DIR,
        env=_clean_application_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "security.service_api_key" in result.stderr
    assert "CALLBACK_SIGNING_SECRET" in result.stderr


def test_env_config_check_with_app_env_reuses_env_key_manifest(tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text(_release_env_file_content() + "BAD_KEY=value\n", encoding="utf-8")

    result = subprocess.run(
        ["./scripts/verify.sh", "env-config", "--env-file", str(env_file), "--app-env", "test"],
        cwd=ROOT_DIR,
        env=_clean_application_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert f"{env_file}:" in result.stderr
    assert "unknown config key: BAD_KEY" in result.stderr


def test_env_config_check_app_env_requires_explicit_env_file():
    result = subprocess.run(
        ["./scripts/verify.sh", "env-config", "--app-env", "test"],
        cwd=ROOT_DIR,
        env=_clean_application_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--app-env requires --env-file" in result.stderr


def test_env_config_check_explicit_env_file_must_exist(tmp_path):
    env_file = tmp_path / ".env.missing"

    result = subprocess.run(
        ["./scripts/verify.sh", "env-config", "--env-file", str(env_file), "--app-env", "test"],
        cwd=ROOT_DIR,
        env=_clean_application_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert f"ENV_FILE not found: {env_file}" in result.stderr


def test_env_config_default_scan_includes_env_variants():
    relative_paths = {
        path.relative_to(ROOT_DIR).as_posix()
        for path in default_env_files()
        if path.is_relative_to(ROOT_DIR)
    }

    assert ".env.example" in relative_paths
    assert "scripts/.env.example" not in relative_paths
    assert not any(path.startswith("scripts/.env") for path in relative_paths)


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


def test_verify_sh_documents_oss_config_command():
    result = subprocess.run(
        ["./scripts/verify.sh", "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    verify_sh = (ROOT_DIR / "scripts/verify.sh").read_text(encoding="utf-8")
    tasks_sh = (ROOT_DIR / "scripts/verify/tasks.sh").read_text(encoding="utf-8")

    assert "oss-config" in result.stdout
    assert "oss-config)" in verify_sh
    assert "run_oss_config_check" in verify_sh
    assert "run_oss_config_check()" in tasks_sh


def test_verify_sh_documents_image_inspect_command():
    result = subprocess.run(
        ["./scripts/verify.sh", "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    verify_sh = (ROOT_DIR / "scripts/verify.sh").read_text(encoding="utf-8")
    tasks_sh = (ROOT_DIR / "scripts/verify/tasks.sh").read_text(encoding="utf-8")

    assert "image-inspect" in result.stdout
    assert "image-inspect)" in verify_sh
    assert "run_image_inspect" in verify_sh
    assert "run_image_inspect()" in tasks_sh


def test_workflow_modes_smoke_validates_successful_root_result():
    case = _workflow_mode_case("group")
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
    case = _workflow_mode_case("group")
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
    case = _workflow_mode_case("starmap")
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
