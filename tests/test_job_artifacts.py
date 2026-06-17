import asyncio
import json
import uuid

import pytest

from app.core.exceptions import AppError
from app.integrations.ai_gateway import TextGenerationResult
from app.models.job import AIJob, AIJobWorkItem
from app.schemas.jobs import JobResult
from app.services.executor import run_ai_job
from app.services.job_workflow import finalize_job, merge_work_items
from app.services.jobs import _persist_large_artifacts, _persist_work_item_artifacts


def _prompt_payload() -> dict:
    return {
        "blocks": [
            {"key": "user", "role": "user", "content": "用户提示"},
            {"key": "work_note", "role": "user", "content": ""},
        ]
    }


def _artifact(result, key: str):
    return next(artifact for artifact in result.artifacts if artifact.key == key)


def _artifact_keys(result) -> list[str]:
    return [artifact.key for artifact in result.artifacts]


def _mock_generate(text: str):
    async def _generate(model_id, messages):
        return TextGenerationResult(text=text)
    return _generate


class _FakeDB:
    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def test_step1_returns_work_note_artifact(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        _mock_generate(
            "===本地化正文开始===\n本地化正文\n===本地化正文结束===\n"
            "===工作注释开始===\n称谓已本地化\n===工作注释结束==="
        ),
    )

    result = asyncio.run(run_ai_job("novel_localization.step1_localize", "gpt-4.1", _prompt_payload(), "原文"))
    work_note = _artifact(result, "work_note")

    assert work_note.type == "work_note"
    assert work_note.apply_mode == "replace"
    assert work_note.content == "称谓已本地化"


def test_step2_returns_work_note_suggestion_artifact(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        _mock_generate("【校验结论】不通过\n【问题说明】称呼不一致\n【建议工作注释】请统一角色称呼。"),
    )

    result = asyncio.run(run_ai_job("novel_localization.step2_review", "gpt-4.1", _prompt_payload(), "本地化稿"))
    work_note = _artifact(result, "work_note")

    assert result.signals["passed"] is False
    assert work_note.type == "work_note"
    assert work_note.apply_mode == "replace"
    assert work_note.content == "请统一角色称呼。"


def test_step2_passed_does_not_return_empty_work_note(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        _mock_generate("【校验结论】通过"),
    )

    result = asyncio.run(run_ai_job("novel_localization.step2_review", "gpt-4.1", _prompt_payload(), "本地化稿"))

    assert result.signals["passed"] is True
    assert _artifact_keys(result) == ["review_summary"]


def test_step1_rejects_missing_work_note_marker(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        _mock_generate("===本地化正文开始===\n本地化正文\n===本地化正文结束==="),
    )

    with pytest.raises(AppError) as exc_info:
        asyncio.run(run_ai_job("novel_localization.step1_localize", "gpt-4.1", _prompt_payload(), "原文"))

    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"


def test_step1_rejects_english_translation_output(monkeypatch):
    english_text = (
        "This is a fully translated English draft with American names and American settings. "
        "It reads like a final English story instead of a Chinese localized manuscript. "
        "The output should not be accepted in step one because English belongs to translation."
    )
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        _mock_generate(
            f"===本地化正文开始===\n{english_text}\n===本地化正文结束===\n"
            "===工作注释开始===\n误输出英文\n===工作注释结束==="
        ),
    )

    with pytest.raises(AppError) as exc_info:
        asyncio.run(run_ai_job("novel_localization.step1_localize", "gpt-4.1", _prompt_payload(), "原文"))

    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"
    assert "中文本地化稿" in exc_info.value.message


def test_step2_rejects_missing_suggested_work_note(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        _mock_generate("【校验结论】不通过\n【问题说明】称呼不一致"),
    )

    with pytest.raises(AppError) as exc_info:
        asyncio.run(run_ai_job("novel_localization.step2_review", "gpt-4.1", _prompt_payload(), "本地化稿"))

    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"


def test_chunked_step1_merge_uses_work_note_artifact():
    job = AIJob(
        job_type="novel_localization.step1_localize",
        execution_plan={"execution_mode": "chunked"},
    )
    job_id = uuid.uuid4()
    items = [
        AIJobWorkItem(
            job_id=job_id,
            name="chunk-1",
            kind="chunk",
            chunk_index=1,
            result={
                "artifacts": [
                    {"key": "localized_text", "type": "text", "label": "本地化正文", "content": "第二段"},
                    {
                        "key": "work_note",
                        "type": "work_note",
                        "label": "工作注释",
                        "apply_mode": "replace",
                        "content": "第二段注释",
                    },
                ],
                "signals": {},
            },
        ),
        AIJobWorkItem(
            job_id=job_id,
            name="chunk-0",
            kind="chunk",
            chunk_index=0,
            result={
                "artifacts": [
                    {"key": "localized_text", "type": "text", "label": "本地化正文", "content": "第一段"},
                    {
                        "key": "work_note",
                        "type": "work_note",
                        "label": "工作注释",
                        "apply_mode": "replace",
                        "content": "第一段注释",
                    },
                ],
                "signals": {},
            },
        ),
    ]

    result = merge_work_items(job, items)
    work_note = _artifact(result, "work_note")

    assert _artifact_keys(result) == ["localized_text", "work_note"]
    assert result.signals == {}
    assert work_note.apply_mode == "replace"
    assert work_note.content == "第一段注释\n\n第二段注释"


def test_persist_large_artifacts_uses_runtime_output_target(monkeypatch):
    written: dict = {}
    job_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    job_params_hash = "sha256:" + "0" * 64
    job = AIJob(
        id=job_id,
        job_type="novel_localization.step1_localize",
        job_params_hash=job_params_hash,
        runtime_ref={"oss_bucket": "runtime-bucket", "oss_key": "runtime/runtime.json", "oss_region": "runtime-region"},
    )

    def fake_write_text(*, bucket, key, region, content):
        written.update({"bucket": bucket, "key": key, "region": region, "content": content})
        return {"oss_bucket": bucket, "oss_key": key, "oss_region": region, "content_hash": "sha256:" + "0" * 64}

    def fake_read_text(*, bucket, key, region):
        assert (bucket, key, region) == ("runtime-bucket", "runtime/runtime.json", "runtime-region")
        return json.dumps(
            {
                "schema_version": 1,
                "job_type": "novel_localization.step1_localize",
                "job_params_hash": job_params_hash,
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "ai-jobs/frozen-job/",
                    "oss_region": "region",
                },
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.jobs.storage.write_text", fake_write_text)
    monkeypatch.setattr("app.services.job_runtime.storage.read_text", fake_read_text)

    result = _persist_large_artifacts(
        job,
        JobResult(
            artifacts=[
                {"key": "localized_text", "type": "text", "label": "本地化正文", "content": "正文"}
            ],
            signals={},
        ),
    )

    assert written == {
        "bucket": "bucket",
        "key": "ai-jobs/frozen-job/results/g1/localized_text.txt",
        "region": "region",
        "content": "正文",
    }
    assert result["artifacts"][0]["storage"] == "oss_object"
    assert "content" not in result["artifacts"][0]


def test_persist_work_item_large_artifacts_uses_work_item_scope(monkeypatch):
    written: dict = {}
    job_params_hash = "sha256:" + "0" * 64
    job = AIJob(
        id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        job_type="novel_localization.step1_localize",
        execution_generation=2,
        job_params_hash=job_params_hash,
        runtime_ref={"oss_bucket": "runtime-bucket", "oss_key": "runtime/runtime.json", "oss_region": "runtime-region"},
    )

    def fake_write_text(*, bucket, key, region, content):
        written.update({"bucket": bucket, "key": key, "region": region, "content": content})
        return {"oss_bucket": bucket, "oss_key": key, "oss_region": region, "content_hash": "sha256:" + "0" * 64}

    def fake_read_text(*, bucket, key, region):
        assert (bucket, key, region) == ("runtime-bucket", "runtime/runtime.json", "runtime-region")
        return json.dumps(
            {
                "schema_version": 1,
                "job_type": "novel_localization.step1_localize",
                "job_params_hash": job_params_hash,
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "ai-jobs/frozen-job/",
                    "oss_region": "region",
                },
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.jobs.storage.write_text", fake_write_text)
    monkeypatch.setattr("app.services.job_runtime.storage.read_text", fake_read_text)

    result = _persist_work_item_artifacts(
        job,
        kind="chunk",
        chunk_index=1,
        result={
            "artifacts": [
                {"key": "localized_text", "type": "text", "label": "本地化正文", "content": "分块正文"},
                {"key": "work_note", "type": "work_note", "label": "工作注释", "content": "注释"},
            ],
            "signals": {},
        },
    )

    assert written == {
        "bucket": "bucket",
        "key": "ai-jobs/frozen-job/work-items/g2/chunk-1/localized_text.txt",
        "region": "region",
        "content": "分块正文",
    }
    assert result["artifacts"][0]["storage"] == "oss_object"
    assert "content" not in result["artifacts"][0]
    assert result["artifacts"][1]["content"] == "注释"


def test_single_mode_large_artifact_uses_final_job_scope(monkeypatch):
    written: list[dict] = []
    job_params_hash = "sha256:" + "0" * 64
    job = AIJob(
        id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        job_type="novel_localization.step1_localize",
        execution_plan={"execution_mode": "single"},
        job_params_hash=job_params_hash,
        runtime_ref={"oss_bucket": "runtime-bucket", "oss_key": "runtime/runtime.json", "oss_region": "runtime-region"},
    )
    item = AIJobWorkItem(
        job_id=job.id,
        name="whole",
        kind="whole",
        chunk_index=0,
        result={
            "artifacts": [
                {"key": "localized_text", "type": "text", "label": "本地化正文", "content": "完整正文"}
            ],
            "signals": {},
        },
    )

    def fake_write_text(*, bucket, key, region, content):
        written.append({"bucket": bucket, "key": key, "region": region, "content": content})
        return {"oss_bucket": bucket, "oss_key": key, "oss_region": region, "content_hash": "sha256:" + "0" * 64}

    def fake_read_text(*, bucket, key, region):
        assert (bucket, key, region) == ("runtime-bucket", "runtime/runtime.json", "runtime-region")
        return json.dumps(
            {
                "schema_version": 1,
                "job_type": "novel_localization.step1_localize",
                "job_params_hash": job_params_hash,
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "ai-jobs/final-job/",
                    "oss_region": "region",
                },
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.jobs.storage.write_text", fake_write_text)
    monkeypatch.setattr("app.services.job_runtime.storage.read_text", fake_read_text)

    merged = merge_work_items(job, [item])
    result = _persist_large_artifacts(job, merged)

    assert written == [
        {
            "bucket": "bucket",
            "key": "ai-jobs/final-job/results/g1/localized_text.txt",
            "region": "region",
            "content": "完整正文",
        }
    ]
    assert result["artifacts"][0]["oss_key"] == "ai-jobs/final-job/results/g1/localized_text.txt"
    assert "content" not in result["artifacts"][0]


@pytest.mark.asyncio
async def test_finalize_single_mode_rewrites_work_item_to_final_artifact_ref(monkeypatch):
    written: list[dict] = []
    job_params_hash = "sha256:" + "0" * 64
    job_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
    job = AIJob(
        id=job_id,
        job_type="novel_localization.step1_localize",
        status="running",
        execution_plan={"execution_mode": "single"},
        celery_task_id="root-task",
        job_params_hash=job_params_hash,
        runtime_ref={"oss_bucket": "runtime-bucket", "oss_key": "runtime/runtime.json", "oss_region": "runtime-region"},
    )
    whole = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        name="whole",
        kind="whole",
        chunk_index=0,
        status="succeeded",
        result={
            "artifacts": [
                {"key": "localized_text", "type": "text", "label": "本地化正文", "content": "完整正文"}
            ],
            "signals": {},
        },
    )
    captured: dict = {}

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_list_work_items(_db, _job_id, **_kwargs):
        return [whole]

    async def fake_mark_work_item_succeeded(_db, _item_id, result):
        captured["work_item_result"] = result
        whole.result = result

    async def fake_update_progress(*_args, **_kwargs):
        pass

    async def fake_mark_succeeded(_db, _job_id, *, celery_task_id, result, canonical_result, canonical_result_ref=None):
        captured["job_result"] = result
        captured["canonical_result"] = canonical_result
        return True

    async def fake_deliver_callback(_job_id):
        return False

    def fake_write_text(*, bucket, key, region, content):
        written.append({"bucket": bucket, "key": key, "region": region, "content": content})
        return {"oss_bucket": bucket, "oss_key": key, "oss_region": region, "content_hash": "sha256:" + "0" * 64}

    def fake_read_text(*, bucket, key, region):
        return json.dumps(
            {
                "schema_version": 1,
                "job_type": "novel_localization.step1_localize",
                "job_params_hash": job_params_hash,
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "ai-jobs/final-job/",
                    "oss_region": "region",
                },
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.list_work_items", fake_list_work_items)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_work_item_succeeded", fake_mark_work_item_succeeded)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback)
    monkeypatch.setattr("app.services.jobs.storage.write_text", fake_write_text)
    monkeypatch.setattr("app.services.job_runtime.storage.read_text", fake_read_text)

    finalized = await finalize_job(_FakeDB(), job_id)

    assert finalized == {"job_id": str(job_id), "status": "succeeded"}
    assert written[0]["key"] == "ai-jobs/final-job/results/g1/localized_text.txt"
    artifact = captured["work_item_result"]["artifacts"][0]
    assert artifact["storage"] == "oss_object"
    assert artifact["oss_key"] == "ai-jobs/final-job/results/g1/localized_text.txt"
    assert "content" not in artifact
    assert captured["job_result"] == captured["canonical_result"] == captured["work_item_result"]


def test_chunked_step1_merge_reads_externalized_chunk_artifact(monkeypatch):
    job = AIJob(
        job_type="novel_localization.step1_localize",
        execution_plan={"execution_mode": "chunked"},
    )
    job_id = uuid.uuid4()
    stored = {
        "ai-jobs/job/work-items/chunk-0/localized_text.txt": "第一段",
        "ai-jobs/job/work-items/chunk-1/localized_text.txt": "第二段",
    }

    def fake_read_text(*, bucket, key, region):
        assert bucket == "bucket"
        assert region == "region"
        return stored[key]

    monkeypatch.setattr("app.workflows.novel_localization.handler.storage.read_text", fake_read_text)

    items = [
        AIJobWorkItem(
            job_id=job_id,
            name="chunk-0",
            kind="chunk",
            chunk_index=0,
            result={
                "artifacts": [
                    {
                        "key": "localized_text",
                        "type": "text",
                        "label": "本地化正文",
                        "storage": "oss_object",
                        "oss_bucket": "bucket",
                        "oss_key": "ai-jobs/job/work-items/chunk-0/localized_text.txt",
                        "oss_region": "region",
                    },
                    {"key": "work_note", "type": "work_note", "label": "工作注释", "content": "第一段注释"},
                ],
                "signals": {},
            },
        ),
        AIJobWorkItem(
            job_id=job_id,
            name="chunk-1",
            kind="chunk",
            chunk_index=1,
            result={
                "artifacts": [
                    {
                        "key": "localized_text",
                        "type": "text",
                        "label": "本地化正文",
                        "storage": "oss_object",
                        "oss_bucket": "bucket",
                        "oss_key": "ai-jobs/job/work-items/chunk-1/localized_text.txt",
                        "oss_region": "region",
                    },
                    {"key": "work_note", "type": "work_note", "label": "工作注释", "content": "第二段注释"},
                ],
                "signals": {},
            },
        ),
    ]

    result = merge_work_items(job, items)

    assert _artifact(result, "localized_text").content == "第一段\n\n第二段"
    assert _artifact(result, "work_note").content == "第一段注释\n\n第二段注释"


def test_chunked_step2_merge_uses_work_note_artifact():
    job = AIJob(
        job_type="novel_localization.step2_review",
        execution_plan={"execution_mode": "chunked"},
    )
    job_id = uuid.uuid4()
    items = [
        AIJobWorkItem(
            job_id=job_id,
            name="chunk-0",
            kind="chunk",
            chunk_index=0,
            result={
                "artifacts": [
                    {"key": "review_summary", "type": "text", "label": "校验结果", "content": "称呼不一致"},
                    {
                        "key": "work_note",
                        "type": "work_note",
                        "label": "建议工作注释",
                        "apply_mode": "append",
                        "content": "请统一称呼。",
                    },
                ],
                "signals": {"passed": False},
            },
        )
    ]

    result = merge_work_items(job, items)
    work_note = _artifact(result, "work_note")

    assert result.signals["passed"] is False
    assert work_note.apply_mode == "replace"
    assert work_note.content == "分块 0:\n请统一称呼。"


def test_chunked_step2_passed_does_not_return_empty_work_note():
    job = AIJob(
        job_type="novel_localization.step2_review",
        execution_plan={"execution_mode": "chunked"},
    )
    item = AIJobWorkItem(
        job_id=uuid.uuid4(),
        name="chunk-0",
        kind="chunk",
        chunk_index=0,
        result={
            "artifacts": [
                {"key": "review_summary", "type": "text", "label": "校验结果", "content": "已满足"}
            ],
            "signals": {"passed": True},
        },
    )

    result = merge_work_items(job, [item])

    assert result.signals["passed"] is True
    assert _artifact_keys(result) == ["review_summary"]
