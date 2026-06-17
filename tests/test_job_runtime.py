import json
import uuid

from app.models.job import AIJob, AIJobWorkItem
from app.services.job_runtime import job_params_from_job, prompt_payload_from_job, work_item_payload


def test_runtime_helpers_read_payload_from_refs(monkeypatch):
    objects = {
        "runtime/job_params.json": {"value": {"hello": "world"}},
        "runtime/prompt.json": {"blocks": [{"key": "user", "role": "user", "content": "prompt"}]},
        "runtime/work-item.json": {"text": "chunk text"},
    }

    def fake_read_text(*, bucket, key, region):
        assert bucket == "bucket"
        assert region == "region"
        return json.dumps(objects[key], ensure_ascii=False)

    monkeypatch.setattr("app.services.job_runtime.storage.read_text", fake_read_text)

    job = AIJob(
        id=uuid.uuid4(),
        job_type="generic.echo",
        model_id=None,
        input_payload={"job_params": {"legacy": True}},
        output_payload={},
        callback_payload={},
        prompt_payload={"blocks": []},
        input_ref={"oss_bucket": "bucket", "oss_key": "runtime/job_params.json", "oss_region": "region"},
        prompt_ref={"oss_bucket": "bucket", "oss_key": "runtime/prompt.json", "oss_region": "region"},
    )
    item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job.id,
        name="chunk",
        kind="chunk",
        input_payload={"text": "legacy text"},
        input_ref={"oss_bucket": "bucket", "oss_key": "runtime/work-item.json", "oss_region": "region"},
    )

    assert job_params_from_job(job) == {"value": {"hello": "world"}}
    assert prompt_payload_from_job(job) == {"blocks": [{"key": "user", "role": "user", "content": "prompt"}]}
    assert work_item_payload(item) == {"text": "chunk text"}


def test_runtime_helpers_fallback_to_legacy_payloads():
    job = AIJob(
        id=uuid.uuid4(),
        job_type="generic.echo",
        model_id=None,
        input_payload={"job_params": {"legacy": True}},
        output_payload={},
        callback_payload={},
        prompt_payload={"blocks": [{"key": "user", "role": "user", "content": "legacy"}]},
    )
    item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job.id,
        name="chunk",
        kind="chunk",
        input_payload={"text": "legacy text"},
    )

    assert job_params_from_job(job) == {"legacy": True}
    assert prompt_payload_from_job(job) == {"blocks": [{"key": "user", "role": "user", "content": "legacy"}]}
    assert work_item_payload(item) == {"text": "legacy text"}
