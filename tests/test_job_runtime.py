import json
import uuid

import pytest

from app.models.job import AIJob, AIJobWorkItem
from app.services.job_runtime import (
    job_params_from_job,
    payload_hash,
    prompt_payload_from_job,
    work_item_payload,
    write_runtime_json,
)


def test_write_runtime_json_stores_small_runtime_payload_inline(monkeypatch):
    monkeypatch.setattr(
        "app.services.job_runtime.storage.write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runtime payload should not use storage")),
    )
    job = AIJob(id=uuid.uuid4(), job_type="test.echo")
    payload = {"value": {"hello": "world"}}

    ref = write_runtime_json(job, "job_params", payload)

    assert ref["storage"] == "db_inline"
    assert ref["type"] == "json"
    assert ref["name"] == "job_params"
    assert ref["payload"] == payload
    assert ref["content_hash"].startswith("sha256:")
    assert ref["content_size_bytes"] > 0


def test_runtime_helpers_read_payload_from_refs(monkeypatch):
    job_params = {
        "model_id": "gpt-4.1",
        "source": {"inline": {"text": "原文"}},
        "prompt": {"blocks": [{"key": "user", "role": "user", "content": "prompt"}]},
    }
    job_params_hash = payload_hash(job_params)
    objects = {
        "runtime/job_params.json": job_params,
        "runtime/runtime.json": {
            "schema_version": 1,
            "job_type": "test.text",
            "job_params_hash": job_params_hash,
            "runtime_fields": {
                "model_id": "gpt-4.1",
                "prompt_payload": {"blocks": [{"key": "user", "role": "user", "content": "prompt"}]},
            },
            "output_target": {
                "type": "oss_prefix",
                "oss_bucket": "bucket",
                "oss_prefix": "runtime-output/",
                "oss_region": "region",
            },
        },
        "runtime/work-item.json": {"text": "chunk text"},
    }

    def fake_read_text(*, bucket, key, region):
        assert bucket == "bucket"
        assert region == "region"
        return json.dumps(objects[key], ensure_ascii=False)

    monkeypatch.setattr("app.services.job_runtime.storage.read_text", fake_read_text)

    job = AIJob(
        id=uuid.uuid4(),
        job_type="test.text",
        job_params_ref={"oss_bucket": "bucket", "oss_key": "runtime/job_params.json", "oss_region": "region"},
        job_params_hash=job_params_hash,
        runtime_ref={"oss_bucket": "bucket", "oss_key": "runtime/runtime.json", "oss_region": "region"},
    )
    item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job.id,
        name="chunk",
        kind="chunk",
        input_ref={"oss_bucket": "bucket", "oss_key": "runtime/work-item.json", "oss_region": "region"},
    )

    assert job_params_from_job(job)["model_id"] == "gpt-4.1"
    assert prompt_payload_from_job(job) == {"blocks": [{"key": "user", "role": "user", "content": "prompt"}]}
    assert work_item_payload(item) == {"text": "chunk text"}


def test_job_params_hash_mismatch_fails_fast(monkeypatch):
    def fake_read_text(*, bucket, key, region):
        return json.dumps({"value": "tampered"}, ensure_ascii=False)

    monkeypatch.setattr("app.services.job_runtime.storage.read_text", fake_read_text)

    job = AIJob(
        id=uuid.uuid4(),
        job_type="test.echo",
        job_params_ref={"oss_bucket": "bucket", "oss_key": "runtime/job_params.json", "oss_region": "region"},
        job_params_hash=payload_hash({"value": "original"}),
    )

    with pytest.raises(Exception, match="运行时参数 hash 不匹配"):
        job_params_from_job(job)


def test_runtime_helpers_fail_fast_without_refs():
    job = AIJob(
        id=uuid.uuid4(),
        job_type="test.echo",
    )
    item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job.id,
        name="chunk",
        kind="chunk",
    )

    with pytest.raises(Exception, match="运行时引用不存在"):
        job_params_from_job(job)
    with pytest.raises(Exception, match="运行时引用不存在"):
        work_item_payload(item)


def test_runtime_helpers_reject_invalid_inline_payload():
    item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        name="chunk",
        kind="chunk",
        input_ref={"storage": "db_inline", "type": "json", "payload": ["not", "object"]},
    )

    with pytest.raises(Exception, match="内联引用必须包含 JSON object payload"):
        work_item_payload(item)
