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


def _prepared_locust_env(tmp_path: Path, *, profile: str, **overrides):
    env_file = _env_file(tmp_path)
    values = {
        "case_key": None,
        "profile_ref": profile,
        "api_url": None,
        "env_file": str(env_file),
        "allow_remote_api": False,
        "service_api_key": None,
        "caller_id": "load-cli",
        "allow_real_job": False,
        "job_type": None,
        "job_params_json": None,
        "job_params_json_file": None,
        "query_job_ids": None,
        "query_job_ids_file": None,
        "users": None,
        "spawn_rate": None,
        "run_time": None,
        "run_id": "profile-env",
        "output_dir": str(tmp_path / "load"),
        "echo_sleep_seconds": None,
        "echo_repeat": None,
        "workflow_mode": None,
        "workflow_sleep_seconds": None,
        "wait_min_seconds": None,
        "wait_max_seconds": None,
        "poll_interval_seconds": None,
        "flow_timeout_seconds": None,
        "web": False,
        "web_host": "127.0.0.1",
        "web_port": 8089,
    }
    values.update(overrides)
    return cli._prepare_run(**values)


def test_cases_lists_registered_contract():
    result = RUNNER.invoke(cli.app, ["cases", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    cases = {item["key"]: item for item in payload["cases"]}
    keys = set(cases)
    assert {"job-flow", "job-submit", "job-query", "workflow-flow", "api-health"} <= keys
    required_keys = {
        "key",
        "title",
        "question",
        "kind",
        "target",
        "default_job_type",
        "default_http_method",
        "default_http_path",
        "writes_jobs",
        "requires_job_ids",
        "billable_risk",
        "defaults",
        "post_checks",
    }
    for case in payload["cases"]:
        assert required_keys <= set(case)
        assert {
            "time",
            "users",
            "spawn_rate",
            "flow_timeout_seconds",
            "poll_interval_seconds",
            "wait_min_seconds",
            "wait_max_seconds",
        } <= set(case["defaults"])
    assert cases["job-flow"]["kind"] == "job_flow"
    assert cases["job-flow"]["default_job_type"] == "example_sleep"
    assert cases["job-flow"]["defaults"]["time"] == "60s"
    assert cases["job-flow"]["defaults"]["users"] == 4
    assert cases["job-flow"]["post_checks"] == ["drain", "pressure"]
    assert cases["job-query"]["requires_job_ids"] is True
    assert cases["api-health"]["default_http_path"] == "/health"


def test_list_alias_lists_cases():
    result = RUNNER.invoke(cli.app, ["list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "cases" in payload


def test_profiles_lists_builtin_profiles():
    result = RUNNER.invoke(cli.app, ["profiles", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    profiles = {item["key"]: item for item in payload["profiles"]}
    keys = set(profiles)
    assert keys == {
        "example-sleep",
        "example-workflow-single",
        "example-workflow-chain",
        "example-workflow-group",
        "example-workflow-chord",
        "example-workflow-chord-slow",
        "example-workflow-chord-child-fail",
        "example-workflow-chord-join-fail",
        "example-workflow-chord-timeout",
        "example-workflow-map",
        "example-workflow-starmap",
        "example-workflow-chunks",
    }
    required_keys = {"key", "title", "job_type", "case", "job_params_present", "defaults"}
    for profile in payload["profiles"]:
        assert required_keys <= set(profile)
        assert {
            "users",
            "spawn_rate",
            "time",
            "poll_interval_seconds",
            "flow_timeout_seconds",
            "wait_min_seconds",
            "wait_max_seconds",
        } <= set(profile["defaults"])
        assert "job_params" not in profile
    assert profiles["example-sleep"]["job_type"] == "example_sleep"
    assert profiles["example-sleep"]["case"] == "job-flow"
    assert profiles["example-sleep"]["job_params_present"] is True
    assert profiles["example-sleep"]["defaults"]["time"] == "60s"
    assert profiles["example-sleep"]["defaults"]["flow_timeout_seconds"] == 45.0
    assert profiles["example-workflow-group"]["job_type"] == "example_workflow"
    assert profiles["example-workflow-group"]["case"] == "workflow-flow"
    assert profiles["example-workflow-group"]["job_params_present"] is True


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


def test_run_rejects_billable_demo_job_without_confirmation(tmp_path):
    env_file = _env_file(tmp_path)

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "job-flow",
            "--env-file",
            str(env_file),
            "--job-type",
            "job_real_llm_echo",
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
    assert payload["run_id"] == "run-1"
    assert payload == manifest
    assert manifest["status"] == "dry_run"
    assert manifest["case_key"] == "job-flow"
    assert manifest["case"]["kind"] == "job_flow"
    assert manifest["case"]["target"] == "job"
    assert manifest["case"]["writes_jobs"] is True
    assert manifest["case"]["requires_job_ids"] is False
    assert manifest["case"]["billable_risk"] is False
    assert manifest["profile"] is None
    assert manifest["job_type"] == "example_sleep"
    assert manifest["job_params_source"] == "example_sleep_defaults"
    assert manifest["allow_real_job"] is False
    assert manifest["users"] == 4
    assert manifest["spawn_rate"] == 1.0
    assert manifest["run_time"] == "60s"
    assert "LOAD_INTERNAL_AUTH_TOKEN" not in json.dumps(manifest)
    assert manifest["paths"]["manifest"].endswith("/run-1/manifest.json")
    assert manifest["paths"]["csv_prefix"].endswith("/run-1/locust")
    assert manifest["paths"]["html_report"].endswith("/run-1/report.html")
    assert f"--output-dir {output_dir}" in manifest["next_checks"][0]


def test_run_rejects_unsafe_run_id_before_writing_manifest(tmp_path):
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
            "../escape",
            "--output-dir",
            str(output_dir),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "run_id must match" in result.stderr
    assert not (tmp_path / "escape").exists()


def test_query_case_requires_job_ids(tmp_path):
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


def test_unknown_case_returns_stable_error(tmp_path):
    env_file = _env_file(tmp_path)

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "no-such-case",
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "ERROR: unknown load case" in result.stderr
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
    assert manifest["case_key"] == "job-query"
    assert manifest["query_job_ids_source"] == "inline"


def test_run_builtin_profile_uses_profile_defaults(tmp_path):
    env_file = _env_file(tmp_path)
    output_dir = tmp_path / "load"

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "--profile",
            "example-workflow-group",
            "--env-file",
            str(env_file),
            "--run-id",
            "profile-workflow",
            "--output-dir",
            str(output_dir),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["case_key"] == "workflow-flow"
    assert payload["job_type"] == "example_workflow"
    assert payload["profile"]["key"] == "example-workflow-group"
    assert payload["job_params_source"] == "profile"
    assert payload["profile"]["job_params_present"] is True
    assert "job_params" not in payload["profile"]
    assert payload["flow_timeout_seconds"] == 90.0


def test_builtin_workflow_profile_supplies_effective_locust_job_params(tmp_path):
    payload, _command, locust_env = _prepared_locust_env(tmp_path, profile="example-workflow-group")

    job_params = json.loads(locust_env["LOAD_INTERNAL_JOB_PARAMS_JSON"])
    assert job_params == {"mode": "group", "label": "load-group", "sleep_seconds": 15.0}
    assert locust_env["LOAD_INTERNAL_JOB_PARAMS_SOURCE"] == "profile"
    assert locust_env["LOAD_INTERNAL_RUN_ID"] == payload["run_id"]
    assert locust_env["LOAD_INTERNAL_PROFILE_KEY"] == "example-workflow-group"


def test_builtin_workflow_profile_allows_explicit_example_overrides(tmp_path):
    _payload, _command, locust_env = _prepared_locust_env(
        tmp_path,
        profile="example-workflow-group",
        workflow_mode="chain",
        workflow_sleep_seconds=3.0,
    )

    assert json.loads(locust_env["LOAD_INTERNAL_JOB_PARAMS_JSON"]) == {
        "mode": "chain",
        "label": "load-group",
        "sleep_seconds": 3.0,
    }


def test_run_file_profile_supplies_real_job_params(tmp_path):
    env_file = _env_file(tmp_path)
    output_dir = tmp_path / "load"
    profile = tmp_path / "poster.json"
    profile.write_text(
        json.dumps(
            {
                "key": "poster",
                "job_type": "poster_title_image",
                "case": "job-flow",
                "job_params": {"items": []},
                "defaults": {"users": 2, "spawn_rate": 1.0, "time": "30s", "flow_timeout_seconds": 120},
            }
        ),
        encoding="utf-8",
    )

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "--profile",
            str(profile),
            "--env-file",
            str(env_file),
            "--run-id",
            "poster-profile",
            "--output-dir",
            str(output_dir),
            "--allow-real-job",
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["allow_real_job"] is True
    assert payload["job_type"] == "poster_title_image"
    assert payload["job_params_source"] == "profile"
    assert payload["case"]["billable_risk"] is True
    assert payload["profile"]["key"] == "poster"
    assert payload["profile"]["job_type"] == "poster_title_image"
    assert payload["profile"]["job_params_present"] is True
    assert payload["profile"]["defaults"]["time"] == "30s"
    assert "job_params" not in payload["profile"]
    assert payload["users"] == 2
    assert payload["flow_timeout_seconds"] == 120.0


def test_run_file_profile_requires_real_job_confirmation(tmp_path):
    env_file = _env_file(tmp_path)
    profile = tmp_path / "poster.json"
    profile.write_text(
        json.dumps(
            {
                "key": "poster",
                "job_type": "poster_title_image",
                "case": "job-flow",
                "job_params": {"items": []},
            }
        ),
        encoding="utf-8",
    )

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "--profile",
            str(profile),
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "pass --allow-real-job" in result.stderr


def test_file_profile_requires_explicit_case(tmp_path):
    env_file = _env_file(tmp_path)
    profile = tmp_path / "poster.json"
    profile.write_text(
        json.dumps({"key": "poster", "job_type": "poster_title_image", "job_params": {}}),
        encoding="utf-8",
    )

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "--profile",
            str(profile),
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "profile case is required" in result.stderr


def test_file_profile_rejects_unknown_keys(tmp_path):
    env_file = _env_file(tmp_path)
    profile = tmp_path / "poster.json"
    profile.write_text(
        json.dumps(
            {
                "key": "poster",
                "job_type": "poster_title_image",
                "case": "job-flow",
                "allow_real_job": True,
                "job_params": {},
            }
        ),
        encoding="utf-8",
    )

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "--profile",
            str(profile),
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "unknown keys: allow_real_job" in result.stderr


def test_init_writes_profile_template(tmp_path):
    output = tmp_path / "profile.json"

    result = RUNNER.invoke(
        cli.app,
        [
            "init",
            "poster",
            "--job-type",
            "poster_title_image",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["key"] == "poster"
    assert payload["job_type"] == "poster_title_image"
    assert payload["case"] == "job-flow"


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


def test_locust_job_metadata_includes_run_context():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import json",
                    "from scripts.load import locustfile",
                    "print(json.dumps(locustfile.build_job_metadata(",
                    "    case_key='workflow-flow',",
                    "    run_id='run-1',",
                    "    profile_key='example-workflow-chord',",
                    "    sequence=3,",
                    "), sort_keys=True))",
                ]
            ),
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "source": "scripts/load.sh",
        "run_id": "run-1",
        "case_key": "workflow-flow",
        "sequence": 3,
        "profile": "example-workflow-chord",
    }


def test_pressure_uses_manifest_context(tmp_path, monkeypatch):
    output_dir = tmp_path / "load"
    run_dir = output_dir / "run-2"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-2",
                "case_key": "job-submit",
                "job_type": "example_sleep",
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
        "example_sleep",
        "--caller-id",
        "load-cli",
        "--run-id",
        "run-2",
    ]

def test_report_uses_case_key(tmp_path):
    output_dir = tmp_path / "load"
    run_dir = output_dir / "run-3"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "run_id": "run-3",
                "case_key": "job-flow",
                "job_type": "example_sleep",
                "caller_id": "load-cli",
                "paths": {
                    "manifest": str(run_dir / "manifest.json"),
                    "csv_prefix": str(run_dir / "locust"),
                    "html_report": str(run_dir / "report.html"),
                },
            }
        ),
        encoding="utf-8",
    )

    result = RUNNER.invoke(
        cli.app,
        [
            "report",
            "--run-id",
            "run-3",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert "case=job-flow" in result.output


def test_drain_uses_manifest_context(tmp_path, monkeypatch):
    output_dir = tmp_path / "load"
    run_dir = output_dir / "old-run"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "case_key": "job-submit",
                "job_type": "example_sleep",
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
            "drain",
            "--run-id",
            "old-run",
            "--output-dir",
            str(output_dir),
            "--strict",
        ],
    )

    assert result.exit_code == 0
    assert captured["args"] == [
        "drain",
        "--since",
        "30m",
        "--older-than",
        "10m",
        "--job-type",
        "example_sleep",
        "--caller-id",
        "load-cli",
        "--run-id",
        "old-run",
        "--strict",
    ]
