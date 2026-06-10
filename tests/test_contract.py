import yaml

from app.infrastructure.config import settings
from app.infrastructure.prompt_templates import get_template
from app.main import app
from app.schemas.jobs import CreateJobRequest, JobResult
from app.services.executor import _prompt_messages


def _valid_payload() -> dict:
    template = get_template("novel_localization.step1_localize")
    assert template is not None
    return {
        "job_type": "novel_localization.step1_localize",
        "model_id": "gpt-4.1",
        "source": {
            "oss": {
                "oss_key": "jobs/test/input.txt",
                "oss_url": "https://example.com/jobs/test/input.txt",
                "content_type": "text/plain; charset=utf-8",
            },
        },
        "callback": {"url": "https://example.com/callback"},
        "prompt": {
            "blocks": [
                {"key": block.key, "role": block.role, "content": block.default_content}
                for block in template.prompt_blocks
            ]
        },
    }


def test_create_job_request_accepts_valid_payload():
    payload = CreateJobRequest.model_validate(_valid_payload())
    assert payload.job_type == "novel_localization.step1_localize"
    assert payload.source.oss.oss_key == "jobs/test/input.txt"
    assert payload.source.oss.content_type == "text/plain; charset=utf-8"


def test_step1_prompt_requires_chinese_localized_output():
    template = get_template("novel_localization.step1_localize")
    assert template is not None
    user_block = next((b for b in template.prompt_blocks if b.key == "user"), None)
    assert user_block is not None
    content = user_block.default_content

    assert "语言是中文" in content
    assert "小说本地化方法论" in content


def _block_content(job_type: str, key: str) -> str:
    template = get_template(job_type)
    assert template is not None
    return next(block.default_content for block in template.prompt_blocks if block.key == key)


def test_user_prompt_defaults_to_yaml_config():
    config = yaml.safe_load(settings.prompt_config_path.read_text(encoding="utf-8"))
    expected = {
        job_type: job_config["prompt_blocks"]["user"]["content"].strip()
        for job_type, job_config in config["job_types"].items()
    }

    for job_type, content in expected.items():
        assert _block_content(job_type, "user") == content


def test_runtime_prompt_appends_service_output_contract():
    messages = _prompt_messages(
        {
            "blocks": [
                {"key": "system", "role": "system", "content": "系统提示"},
                {"key": "user", "role": "user", "content": "用户配置提示"},
                {"key": "work_note", "role": "user", "content": "工作注释"},
            ]
        },
        "待处理正文",
        "novel_localization.step1_localize",
    )

    user_message = messages[1]["content"]
    assert "用户配置提示" in user_message
    assert "AI 能力层输出格式契约" in user_message
    assert "===本地化正文开始===" in user_message
    assert "===待处理文本开始===" in user_message
    assert messages[2]["content"].startswith("【已有工作注释 / 上一轮约束】")


def test_runtime_prompt_skips_empty_work_note_input():
    messages = _prompt_messages(
        {
            "blocks": [
                {"key": "system", "role": "system", "content": "系统提示"},
                {"key": "user", "role": "user", "content": "用户配置提示"},
                {"key": "work_note", "role": "user", "content": ""},
            ]
        },
        "待处理正文",
        "novel_localization.step1_localize",
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert all(not message["content"].startswith("【已有工作注释 / 上一轮约束】") for message in messages)


def test_create_job_request_rejects_non_text_content_type():
    payload = _valid_payload()
    payload["source"]["oss"]["content_type"] = "application/json"
    try:
        CreateJobRequest.model_validate(payload)
    except Exception as exc:
        assert "content_type" in str(exc)
    else:
        raise AssertionError("non-text content_type should be rejected")


def test_create_job_request_rejects_legacy_input_output():
    payload = _valid_payload()
    payload["input"] = {"type": "text", "content": "hello"}
    payload["output"] = {"type": "oss_prefix", "oss_bucket": "bucket", "oss_prefix": "jobs/test/", "oss_region": "local"}
    try:
        CreateJobRequest.model_validate(payload)
    except Exception as exc:
        assert "input" in str(exc) or "output" in str(exc)
    else:
        raise AssertionError("legacy input/output fields should be rejected")


def test_create_job_request_rejects_execution_mode():
    payload = _valid_payload()
    payload["execution_mode"] = "sync"
    try:
        CreateJobRequest.model_validate(payload)
    except Exception as exc:
        assert "execution_mode" in str(exc)
    else:
        raise AssertionError("execution_mode should be rejected")


def test_job_result_rejects_legacy_artifact_target():
    try:
        JobResult.model_validate(
            {
                "artifacts": [
                    {
                        "key": "work_note",
                        "type": "work_note",
                        "label": "建议工作注释",
                        "apply_mode": "append",
                        "content": "请统一角色称呼。",
                        "target": {
                            "job_type": "novel_localization.step1_localize",
                            "prompt_block_key": "work_note",
                            "default_mode": "append",
                        },
                    }
                ],
                "signals": {"passed": False},
            }
        )
    except Exception as exc:
        assert "target" in str(exc)
    else:
        raise AssertionError("legacy artifact target should be rejected")


def test_openapi_declares_bearer_auth_for_protected_routes():
    schema = app.openapi()

    security_schemes = schema["components"]["securitySchemes"]
    assert security_schemes["HTTPBearer"] == {"type": "http", "scheme": "bearer"}

    prompt_templates = schema["paths"]["/api/v1/novel-localization-ai/prompt-templates"]["get"]
    assert {"HTTPBearer": []} in prompt_templates["security"]


def test_healthz_matches_health():
    from fastapi.testclient import TestClient

    client = TestClient(app)
    health = client.get("/health")
    healthz = client.get("/healthz")

    assert health.status_code == 200
    assert healthz.status_code == 200
    assert healthz.json() == health.json()
