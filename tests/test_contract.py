import yaml
import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.core.config import settings
from app.core.prompt_templates import get_template
from app.core.security import require_service_auth
from app.main import app
from app.schemas.jobs import CreateJobRequest, JobResult, NovelLocalizationJobParams
from app.services.executor import _prompt_messages
from app.services.jobs import _validate_create_request


def _valid_payload() -> dict:
    template = get_template("novel_localization.step1_localize")
    assert template is not None
    return {
        "job_type": "novel_localization.step1_localize",
        "job_params": {
            "model_id": "gpt-4.1",
            "source": {
                "oss": {
                    "oss_key": "jobs/test/input.txt",
                    "oss_url": "https://example.com/jobs/test/input.txt",
                    "content_type": "text/plain; charset=utf-8",
                },
            },
            "prompt": {
                "blocks": [
                    {"key": block.key, "role": block.role, "content": block.default_content}
                    for block in template.prompt_blocks
                ]
            },
        },
        "callback": {"url": "https://example.com/callback"},
        "metadata": {"caller_task_id": "task-1"},
        "options": {"priority": "normal", "timeout_seconds": 300},
    }


def test_create_job_request_accepts_valid_payload():
    payload = CreateJobRequest.model_validate(_valid_payload())
    assert payload.job_type == "novel_localization.step1_localize"
    assert payload.job_params["source"]["oss"]["oss_key"] == "jobs/test/input.txt"
    assert payload.metadata == {"caller_task_id": "task-1"}


def test_novel_localization_job_params_accept_valid_payload():
    params = NovelLocalizationJobParams.model_validate(_valid_payload()["job_params"])
    assert params.source.oss.oss_key == "jobs/test/input.txt"
    assert params.source.oss.content_type == "text/plain; charset=utf-8"


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
                {"key": "user", "role": "user", "content": "用户配置提示"},
                {"key": "work_note", "role": "user", "content": "工作注释"},
            ]
        },
        "待处理正文",
        "novel_localization.step1_localize",
    )

    user_message = messages[0]["content"]
    assert "用户配置提示" in user_message
    assert "AI 能力层输出格式契约" in user_message
    assert "===本地化正文开始===" in user_message
    assert "===待处理文本开始===" in user_message
    assert messages[1]["content"].startswith("【已有工作注释 / 上一轮约束】")


def test_runtime_prompt_skips_empty_work_note_input():
    messages = _prompt_messages(
        {
            "blocks": [
                {"key": "user", "role": "user", "content": "用户配置提示"},
                {"key": "work_note", "role": "user", "content": ""},
            ]
        },
        "待处理正文",
        "novel_localization.step1_localize",
    )

    assert [message["role"] for message in messages] == ["user"]
    assert all(not message["content"].startswith("【已有工作注释 / 上一轮约束】") for message in messages)


def test_create_job_request_rejects_non_text_content_type():
    params = _valid_payload()["job_params"]
    params["source"]["oss"]["content_type"] = "application/json"
    try:
        NovelLocalizationJobParams.model_validate(params)
    except Exception as exc:
        assert "content_type" in str(exc)
    else:
        raise AssertionError("non-text content_type should be rejected")


def test_create_job_request_rejects_legacy_job_contract_fields():
    payload = _valid_payload()
    payload["model_id"] = "gpt-4.1"
    payload["source"] = {"inline": {"text": "hello"}}
    payload["prompt"] = {"blocks": []}
    try:
        CreateJobRequest.model_validate(payload)
    except Exception as exc:
        assert "model_id" in str(exc) and "source" in str(exc) and "prompt" in str(exc)
    else:
        raise AssertionError("legacy job contract fields should be rejected")


def test_create_job_request_rejects_execution_mode():
    payload = _valid_payload()
    payload["execution_mode"] = "sync"
    try:
        CreateJobRequest.model_validate(payload)
    except Exception as exc:
        assert "execution_mode" in str(exc)
    else:
        raise AssertionError("execution_mode should be rejected")


def test_create_job_validation_allows_non_model_runtime(monkeypatch):
    class GenericHandler:
        def normalize_job_params(self, job_params):
            return job_params

        def runtime_job_fields(self, job_params):
            return {}

        def validate_extra(self, extra):
            pass

    payload = CreateJobRequest.model_validate(
        {
            "job_type": "generic.no_model",
            "job_params": {"input": {"value": 1}},
        }
    )
    monkeypatch.setattr("app.core.workflow_registry.get", lambda job_type: GenericHandler())
    monkeypatch.setattr("app.services.jobs.get_template", lambda job_type: None)
    monkeypatch.setattr(
        "app.services.jobs.get_enabled_model",
        lambda model_id: (_ for _ in ()).throw(AssertionError("model registry should not be called")),
    )

    _handler, job_params, runtime_fields = _validate_create_request(payload)

    assert job_params == {"input": {"value": 1}}
    assert runtime_fields == {}


@pytest.mark.asyncio
async def test_service_auth_uses_caller_id_header(monkeypatch):
    monkeypatch.setattr("app.core.security.settings.SERVICE_API_KEY", "test-token")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")

    caller_id = await require_service_auth(credentials=credentials, caller_id="caller-1")

    assert caller_id == "caller-1"


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

    from app.core.config import settings
    prompt_templates = schema["paths"][f"{settings.SERVICE_API_PREFIX}/prompt-templates"]["get"]
    assert {"HTTPBearer": []} in prompt_templates["security"]


def test_health_endpoints():
    from fastapi.testclient import TestClient

    client = TestClient(app)
    health = client.get("/health")
    healthz = client.get("/healthz")

    # /health 是 liveness probe，始终返回 200
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    # /healthz 是 readiness probe，检查 DB/Redis；测试环境无真实依赖，返回状态可为 200 或 503
    assert healthz.status_code in (200, 503)
    assert "status" in healthz.json()
