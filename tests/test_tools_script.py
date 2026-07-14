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
    assert "capability" in result.stdout
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
    assert "Tools" in result.stdout
    assert "object_storage_read:1" in result.stdout
    assert "audio_decode_normalize:1" in result.stdout
    assert "Capabilities" in result.stdout
    assert "media.audio_input:2" in result.stdout
    assert "Job Type Capabilities" in result.stdout
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
    assert data == {
        "capabilities": [
            {
                "allowed_tool_refs": ["audio_decode_normalize:1", "object_storage_read:1"],
                "capability_ref": "media.audio_input:2",
                "error_codes": [
                    "AUDIO_STEM_DURATION_EXCEEDS_LIMIT",
                    "AUDIO_STEM_INPUT_INVALID",
                    "AUDIO_STEM_RUNTIME_UNAVAILABLE",
                    "INPUT_HASH_MISMATCH",
                    "INPUT_TOO_LARGE",
                    "OSS_BUCKET_NOT_CONFIGURED",
                    "OSS_FETCH_FAILED",
                    "OSS_OBJECT_NOT_FOUND",
                    "OSS_REGION_NOT_CONFIGURED",
                ],
                "log_events": [],
                "plan_schema": "AudioInputPlanSnapshot",
                "result_schema": "PreparedAudioInputMetadata",
                "service_entrypoint": "app.capabilities.media.audio_input:prepare_audio_input",
            }
        ],
        "job_capabilities": [
            {
                "allowed_capability_refs": ["media.audio_input:2"],
                "job_type": "audio_stem_separation",
                "role": "root",
                "visibility": "demo",
            },
            {
                "allowed_capability_refs": ["media.audio_input:2"],
                "job_type": "audio_stem_separation_triton",
                "role": "root",
                "visibility": "demo",
            },
        ],
        "tools": [
            {
                "entrypoint": "app.tools.media_audio:decode_normalize_audio",
                "error_codes": [
                    "AUDIO_STEM_DURATION_EXCEEDS_LIMIT",
                    "AUDIO_STEM_INPUT_INVALID",
                    "AUDIO_STEM_RUNTIME_UNAVAILABLE",
                ],
                "kind": "media_transform",
                "log_events": [],
                "request_schema": "AudioDecodeNormalizeRequest",
                "required_settings": [],
                "result_schema": None,
                "startup_validators": [],
                "tool_ref": "audio_decode_normalize:1",
            },
            {
                "entrypoint": "app.tools.object_storage:read_object_bytes",
                "error_codes": [
                    "INPUT_TOO_LARGE",
                    "OSS_BUCKET_NOT_CONFIGURED",
                    "OSS_FETCH_FAILED",
                    "OSS_OBJECT_NOT_FOUND",
                    "OSS_REGION_NOT_CONFIGURED",
                ],
                "kind": "object_storage",
                "log_events": [],
                "request_schema": "CanonicalObjectRefSnapshot",
                "required_settings": ["storage.backend", "job.oss_input_max_bytes"],
                "result_schema": None,
                "startup_validators": [],
                "tool_ref": "object_storage_read:1",
            },
        ],
    }


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
