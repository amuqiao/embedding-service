import json
import os
import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHON_BIN", None)
    env.update(
        {
            "APP_CONFIG_SKIP_DEFAULT_ENV_FILE": "true",
            "APP_ENV": "local",
            "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/fastapi_best_ai_architecture",
            "DB_SSL": "false",
            "SERVICE_API_KEY": "test-token",
            "CALLBACK_SIGNING_SECRET": "test-callback-secret",
            "OPENAI_API_KEY": "test-openai-key",
            "DASHSCOPE_API_KEY": "test-dashscope-key",
        }
    )
    return env


def test_ai_providers_help_describes_script_boundary():
    result = subprocess.run(
        ["./scripts/ai-providers.sh", "--help"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "provider" in result.stdout
    assert "不下载本地模型资产" in result.stdout
    assert "默认不访问远端 provider" in result.stdout


def test_ai_providers_check_json_redacts_provider_secrets():
    result = subprocess.run(
        ["./scripts/ai-providers.sh", "check", "--json"],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert result.stderr == ""
    assert data["status"] == "ok"
    assert data["catalog"]["default_model_ids"]["text_generation"] == "gpt-5.5"
    assert {provider["provider"] for provider in data["providers"]} == {"openai", "dashscope"}
    assert "test-openai-key" not in result.stdout
    assert "test-dashscope-key" not in result.stdout


def test_ai_providers_models_can_filter_by_job_type():
    result = subprocess.run(
        [
            "./scripts/ai-providers.sh",
            "models",
            "--job-type",
            "poster_title_image",
            "--capability",
            "image_generation",
            "--json",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert result.stderr == ""
    assert data["job_type"] == "poster_title_image"
    assert data["job_default_model_id"] == "gpt-image-2"
    assert [model["id"] for model in data["models"]] == ["gpt-image-2"]


def test_ai_providers_resolve_uses_job_model_slot():
    result = subprocess.run(
        [
            "./scripts/ai-providers.sh",
            "resolve",
            "--job-type",
            "poster_title_image",
            "--slot",
            "generation",
            "--capability",
            "image_generation",
            "--json",
        ],
        cwd=ROOT_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert result.stderr == ""
    assert data["model_id"] == "gpt-image-2"
    assert data["provider"] == "openai"
    assert data["adapter"] == "openai_images"
    assert data["source_policy"] == "job:poster_title_image:generation"


def test_ai_providers_resolve_rejects_disabled_job_type():
    env = _env()
    env["ENABLED_JOB_TYPES"] = "tagged_text_translation"
    result = subprocess.run(
        [
            "./scripts/ai-providers.sh",
            "resolve",
            "--job-type",
            "poster_title_image",
            "--slot",
            "generation",
            "--capability",
            "image_generation",
            "--json",
        ],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert result.stdout == ""
    assert "INVALID_JOB_TYPE" in result.stderr
