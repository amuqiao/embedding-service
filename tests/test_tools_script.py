import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHON_BIN", None)
    return env


def test_tools_secret_help_describes_generated_secret():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--help"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "secrets.token_urlsafe(32)" in result.stdout
    assert "SERVICE_API_KEY" in result.stdout
    assert ".env" in result.stdout


def test_tools_env_url_help_describes_fixed_encoding_rules():
    result = subprocess.run(
        ["./scripts/tools.sh", "env-url", "--help"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "DATABASE_URL" in result.stdout
    assert "REDIS_URL" in result.stdout
    assert "生成时始终执行 URL encode" in result.stdout
    assert "不提供 --no-encode" in result.stdout


def test_tools_registry_help_describes_registered_graph():
    result = subprocess.run(
        ["./scripts/tools.sh", "registry", "--help"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "tool" in result.stdout
    assert "job_type" in result.stdout
    assert "--json" in result.stdout


def test_tools_registry_prints_registered_graph():
    result = subprocess.run(
        ["./scripts/tools.sh", "registry"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stderr == ""
    assert "Operations" in result.stdout
    assert "create_ai_job" in result.stdout
    assert "Job Types" in result.stdout
    assert "example_workflow" in result.stdout
    assert "Workflows" in result.stdout
    assert "poster_title_image" in result.stdout
    assert "Tools" in result.stdout
    assert "object_storage_read:1" in result.stdout
    assert "audio_decode_normalize:1" in result.stdout
    assert "Job Type Tools" in result.stdout
    assert "audio_stem_separation" in result.stdout
    assert "audio_stem_separation_triton" in result.stdout


def test_tools_registry_json_prints_registered_graph():
    result = subprocess.run(
        ["./scripts/tools.sh", "registry", "--json"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert result.stderr == ""
    assert set(data) == {
        "operations",
        "job_types",
        "workflows",
        "tools",
        "job_tools",
    }
    assert set(data["operations"][0]) == {
        "operation_id",
        "channel",
        "method",
        "path",
        "success_status",
        "auth_boundary",
        "request_schema",
        "response_data_schema",
        "error_codes",
        "idempotency_key",
        "side_effects",
        "log_events",
        "metrics",
        "change_policy",
    }
    assert set(data["job_types"][0]) == {
        "job_type",
        "visibility",
        "role",
        "execution_mode",
        "params_schema",
        "runtime_fields_schema",
        "canonical_result_schema",
        "public_result_schema",
        "callback_envelope_schema",
        "allow_callback",
        "result_snapshot_statuses",
        "large_artifact_keys",
        "error_codes",
        "log_events",
        "timeout_seconds",
        "retry_policy",
        "side_effect_policy",
        "required_tool_refs",
        "prompt_specs",
        "prompt_template_required_blocks",
    }
    assert set(data["workflows"][0]) == {
        "workflow_type",
        "root_job_type",
        "workflow_version",
        "failure_policy",
        "max_nodes",
        "runtime_job_type_dependencies",
        "build",
    }
    assert set(data["tools"][0]) == {
        "tool_ref",
        "kind",
        "entrypoint",
        "request_schema",
        "result_schema",
        "required_settings",
        "startup_validators",
        "error_codes",
        "log_events",
    }
    assert set(data["job_tools"][0]) == {
        "job_type",
        "visibility",
        "role",
        "required_tool_refs",
    }
    operations = {item["operation_id"]: item for item in data["operations"]}
    assert operations["create_ai_job"]["method"] == "POST"
    assert operations["create_ai_job"]["path"] == "/jobs"
    assert operations["create_ai_job"]["success_status"] == 200
    assert operations["create_ai_job"]["response_data_schema"] == "JobResponseData"

    job_types = {item["job_type"]: item for item in data["job_types"]}
    assert job_types["example_workflow"]["visibility"] == "demo"
    assert job_types["example_workflow"]["role"] == "root"
    assert job_types["poster_title_image"]["visibility"] == "public"
    assert job_types["poster_title_image"]["role"] == "root"

    workflows = {item["workflow_type"]: item for item in data["workflows"]}
    assert workflows["example_workflow"]["root_job_type"] == "example_workflow"
    assert workflows["example_workflow"]["runtime_job_type_dependencies"] == [
        "example_collect",
        "example_pair",
        "example_sleep",
    ]
    assert workflows["poster_title_image"]["root_job_type"] == "poster_title_image"
    assert workflows["poster_title_image"]["failure_policy"] == "fail_fast"
    assert workflows["poster_title_image"]["runtime_job_type_dependencies"] == [
        "poster_title_image_generate_item",
        "poster_title_image_join",
        "poster_title_image_style_probe",
    ]

    tools = {item["tool_ref"]: item for item in data["tools"]}
    assert tools["audio_decode_normalize:1"]["kind"] == "media_transform"
    assert tools["object_storage_read:1"]["required_settings"] == [
        "storage.backend",
        "job.oss_input_max_bytes",
    ]
    assert tools["object_storage_read:1"]["startup_validators"] == [
        "app.tools.private.object_storage_read:validate_configuration",
    ]
    job_tools = {item["job_type"]: item for item in data["job_tools"]}
    assert job_tools["audio_stem_separation"]["required_tool_refs"] == [
        "audio_decode_normalize:1",
        "object_storage_read:1",
    ]
    assert job_tools["audio_stem_separation_triton"]["required_tool_refs"] == [
        "audio_decode_normalize:1",
        "object_storage_read:1",
    ]


def test_tools_registry_rejects_unknown_argument():
    result = subprocess.run(
        ["./scripts/tools.sh", "registry", "--format", "table"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--format" in result.stderr


def test_tools_secret_generates_urlsafe_token_only_on_stdout():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    token = result.stdout.strip()
    assert result.stderr == ""
    assert TOKEN_RE.fullmatch(token)
    assert len(token) >= 32


def test_tools_env_url_postgres_encodes_components_and_prints_parse_summary():
    result = subprocess.run(
        [
            "./scripts/tools.sh",
            "env-url",
            "postgres",
            "--username",
            "test:user",
            "--host",
            "postgres.fortress",
            "--port",
            "5432",
            "--database",
            "test/cms poster-title",
            "--password-stdin",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        input="abc@123#x/y",
        capture_output=True,
        text=True,
        check=True,
    )

    lines = result.stdout.splitlines()
    assert result.stderr == ""
    assert lines[0] == (
        "DATABASE_URL=postgresql+asyncpg://"
        "test%3Auser:abc%40123%23x%2Fy@postgres.fortress:5432/test%2Fcms%20poster-title"
    )
    assert "# DATABASE_URL_username_decoded=test:user" in lines
    assert "# DATABASE_URL_password_present=true" in lines
    assert "abc@123#x/y" not in result.stdout
    assert "# DATABASE_URL_database_decoded=test/cms poster-title" in lines
    assert "# URL encode rule: encode username/password/path component; do not encode host/port." in lines


def test_tools_env_url_redis_encodes_acl_user_password_and_db():
    result = subprocess.run(
        [
            "./scripts/tools.sh",
            "env-url",
            "redis",
            "--username",
            "acl:user",
            "--host",
            "192.168.0.5",
            "--port",
            "6390",
            "--db",
            "8",
            "--password-stdin",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        input="p@ss/word",
        capture_output=True,
        text=True,
        check=True,
    )

    lines = result.stdout.splitlines()
    assert result.stderr == ""
    assert lines[0] == "REDIS_URL=redis://acl%3Auser:p%40ss%2Fword@192.168.0.5:6390/8"
    assert "# REDIS_URL_username_decoded=acl:user" in lines
    assert "# REDIS_URL_password_present=true" in lines
    assert "p@ss/word" not in result.stdout
    assert "# REDIS_URL_db_decoded=8" in lines


def test_tools_env_url_redis_supports_no_password_url():
    result = subprocess.run(
        [
            "./scripts/tools.sh",
            "env-url",
            "redis",
            "--host",
            "127.0.0.1",
            "--port",
            "26379",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    lines = result.stdout.splitlines()
    assert result.stderr == ""
    assert lines[0] == "REDIS_URL=redis://127.0.0.1:26379/0"
    assert "# REDIS_URL_username_encoded=-" in lines
    assert "# REDIS_URL_password_present=false" in lines
    assert "# REDIS_URL_db_decoded=0" in lines


def test_tools_env_url_redis_acl_username_requires_password():
    result = subprocess.run(
        [
            "./scripts/tools.sh",
            "env-url",
            "redis",
            "--username",
            "acl-user",
            "--host",
            "127.0.0.1",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--username requires --password-stdin or --password" in result.stderr


def test_tools_env_url_rejects_conflicting_password_sources():
    result = subprocess.run(
        [
            "./scripts/tools.sh",
            "env-url",
            "redis",
            "--host",
            "127.0.0.1",
            "--password",
            "one",
            "--password-stdin",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        input="two",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--password cannot be combined with --password-stdin" in result.stderr


def test_tools_env_url_rejects_empty_password_from_stdin():
    result = subprocess.run(
        [
            "./scripts/tools.sh",
            "env-url",
            "postgres",
            "--username",
            "app",
            "--host",
            "postgres.fortress",
            "--database",
            "app",
            "--password-stdin",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        input="",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "password must not be empty" in result.stderr


def test_tools_secret_prefix_prepends_literal_prefix():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--prefix", "prd_"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    token = result.stdout.strip()
    assert result.stderr == ""
    assert token.startswith("prd_")
    assert TOKEN_RE.fullmatch(token)
    assert len(token) >= 36


def test_tools_secret_rejects_non_urlsafe_prefix():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--prefix", "prd/"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "URL-safe" in result.stderr


def test_tools_secret_rejects_missing_prefix_value():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--prefix"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--prefix requires a value" in result.stderr


def test_tools_secret_honors_python_bin_when_path_does_not_have_python3(tmp_path):
    dirname_bin = shutil.which("dirname")
    assert dirname_bin is not None
    (tmp_path / "dirname").symlink_to(dirname_bin)

    env = _env()
    env["PATH"] = str(tmp_path)
    env["PYTHON_BIN"] = sys.executable

    result = subprocess.run(
        ["/bin/bash", "./scripts/tools.sh", "secret"],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    token = result.stdout.strip()
    assert result.stderr == ""
    assert TOKEN_RE.fullmatch(token)


def test_tools_secret_rejects_unknown_argument():
    result = subprocess.run(
        ["./scripts/tools.sh", "secret", "--format", "hex"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "用法：" in result.stderr
