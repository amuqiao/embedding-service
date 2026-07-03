import json
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from scripts.load import cli


RUNNER = CliRunner()


def _env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "API_HOST=127.0.0.1",
                "API_PORT=18200",
                "DISABLE_HTTP_AUTH_HEADER=true",
                "DISABLE_CALLER_ID_HEADER=true",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_scenarios_lists_registered_contract():
    result = RUNNER.invoke(cli.app, ["scenarios", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    keys = {item["key"] for item in payload["scenarios"]}
    assert {"job-flow", "job-submit", "job-query", "workflow-flow", "api-health"} <= keys


def test_run_rejects_non_demo_job_without_confirmation(tmp_path):
    env_file = _env_file(tmp_path)

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "job-flow",
            "--env-file",
            str(env_file),
            "--job-type",
            "poster_title_image",
            "--job-params-json",
            "{}",
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "pass --allow-real-job" in result.stderr


def test_run_dry_run_writes_manifest_without_token(tmp_path):
    env_file = _env_file(tmp_path)
    output_dir = tmp_path / "load"

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "job-flow",
            "--env-file",
            str(env_file),
            "--run-id",
            "run-1",
            "--output-dir",
            str(output_dir),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    manifest = json.loads((output_dir / "run-1" / "manifest.json").read_text(encoding="utf-8"))
    assert payload["status"] == "dry_run"
    assert manifest["scenario_key"] == "job-flow"
    assert manifest["job_type"] == "job_test_echo"
    assert "LOAD_INTERNAL_AUTH_TOKEN" not in json.dumps(manifest)
    assert manifest["paths"]["csv_prefix"].endswith("/run-1/locust")


def test_query_scenario_requires_job_ids(tmp_path):
    env_file = _env_file(tmp_path)

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "job-query",
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "requires --query-job-ids" in result.stderr


def test_unknown_scenario_returns_stable_error(tmp_path):
    env_file = _env_file(tmp_path)

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "no-such-scenario",
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "ERROR: unknown load scenario" in result.stderr
    assert "Traceback" not in result.stderr


def test_ui_query_accepts_job_ids(tmp_path):
    env_file = _env_file(tmp_path)
    output_dir = tmp_path / "load"

    result = RUNNER.invoke(
        cli.app,
        [
            "ui",
            "job-query",
            "--env-file",
            str(env_file),
            "--query-job-ids",
            "00000000-0000-0000-0000-000000000000",
            "--run-id",
            "ui-query",
            "--output-dir",
            str(output_dir),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    manifest = json.loads((output_dir / "ui-query" / "manifest.json").read_text(encoding="utf-8"))
    assert "--autostart" in manifest["command"]
    assert manifest["scenario_key"] == "job-query"
    assert manifest["query_job_ids_source"] == "inline"


def test_api_url_rejects_secret_bearing_parts(tmp_path):
    env_file = _env_file(tmp_path)

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "api-health",
            "--env-file",
            str(env_file),
            "--api-url",
            "http://user:pass@127.0.0.1:18200?token=secret",
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "must not contain userinfo, query, or fragment" in result.stderr


def test_locust_query_job_ids_must_be_uuid(tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("../secret\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("LOAD_INTERNAL_QUERY_JOB_IDS", None)
    env["LOAD_INTERNAL_QUERY_JOB_IDS_FILE"] = str(ids_file)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "from scripts.load import locustfile",
                    "try:",
                    "    locustfile.load_query_job_ids()",
                    "except locustfile.LoadConfigError as exc:",
                    "    print(str(exc))",
                    "else:",
                    "    raise SystemExit('expected LoadConfigError')",
                ]
            ),
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "must be UUID" in result.stdout


def test_locust_failure_message_does_not_include_body():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "from scripts.load import locustfile",
                    "class Response:",
                    "    status_code = 500",
                    "    text = 'secret-body'",
                    "    headers = {'x-request-id': 'req-1'}",
                    "    def json(self):",
                    "        return {'code': '900500', 'msg': 'secret-body'}",
                    "print(locustfile.failure_message(Response()))",
                ]
            ),
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HTTP 500 code=900500 request_id=req-1"
    assert "secret-body" not in result.stdout
    assert "secret-body" not in result.stderr


def test_pressure_uses_manifest_context(tmp_path, monkeypatch):
    output_dir = tmp_path / "load"
    run_dir = output_dir / "run-2"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-2",
                "scenario_key": "job-submit",
                "job_type": "job_test_echo",
                "caller_id": "load-cli",
                "paths": {"csv_prefix": str(run_dir / "locust")},
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_run_jobs_command(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli, "_run_jobs_command", fake_run_jobs_command)

    result = RUNNER.invoke(
        cli.app,
        [
            "pressure",
            "--run-id",
            "run-2",
            "--output-dir",
            str(output_dir),
            "--since",
            "10m",
        ],
    )

    assert result.exit_code == 0
    assert captured["args"] == [
        "pressure",
        "--since",
        "10m",
        "--older-than",
        "1m",
        "--locust-prefix",
        str(run_dir / "locust"),
        "--job-type",
        "job_test_echo",
        "--caller-id",
        "load-cli",
    ]
