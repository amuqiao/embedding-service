from app.infrastructure.prompt_templates import get_template
from app.schemas.jobs import CreateJobRequest


def _valid_payload() -> dict:
    template = get_template("novel_localization.step1_localize")
    assert template is not None
    return {
        "job_type": "novel_localization.step1_localize",
        "model_id": "mock-novel-localizer",
        "input": {"type": "text", "content": "hello"},
        "output": {
            "type": "oss_prefix",
            "oss_bucket": "bucket",
            "oss_prefix": "jobs/test/",
            "oss_region": "local",
        },
        "callback": {"url": "https://example.com/callback"},
        "prompt": {
            "blocks": [
                {"key": block.key, "role": block.role, "content": block.default_content}
                for block in template.prompt_blocks
            ]
        },
        "metadata": {"external_job_ref": "test"},
    }


def test_create_job_request_accepts_valid_payload():
    payload = CreateJobRequest.model_validate(_valid_payload())
    assert payload.job_type == "novel_localization.step1_localize"


def test_create_job_request_rejects_execution_mode():
    payload = _valid_payload()
    payload["execution_mode"] = "sync"
    try:
        CreateJobRequest.model_validate(payload)
    except Exception as exc:
        assert "execution_mode" in str(exc)
    else:
        raise AssertionError("execution_mode should be rejected")
