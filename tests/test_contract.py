from app.main import app
from app.infrastructure.prompt_templates import get_template
from app.schemas.jobs import CreateJobRequest


def _valid_payload() -> dict:
    template = get_template("novel_localization.step1_localize")
    assert template is not None
    return {
        "job_type": "novel_localization.step1_localize",
        "model_id": "mock-novel-localizer",
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
