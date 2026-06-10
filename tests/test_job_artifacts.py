import uuid

import pytest

from app.core.exceptions import AppError
from app.infrastructure.ai_gateway import TextGenerationResult
from app.models.job import AIJob, AIJobWorkItem
from app.services.executor import run_ai_job
from app.services.job_workflow import merge_work_items


def _prompt_payload() -> dict:
    return {
        "blocks": [
            {"key": "system", "role": "system", "content": "系统提示"},
            {"key": "user", "role": "user", "content": "用户提示"},
            {"key": "work_note", "role": "user", "content": ""},
        ]
    }


def _artifact(result, key: str):
    return next(artifact for artifact in result.artifacts if artifact.key == key)


def _artifact_keys(result) -> list[str]:
    return [artifact.key for artifact in result.artifacts]


def test_step1_returns_work_note_artifact(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        lambda model_id, messages: TextGenerationResult(
            text=(
                "===本地化正文开始===\n本地化正文\n===本地化正文结束===\n"
                "===工作注释开始===\n称谓已本地化\n===工作注释结束==="
            )
        ),
    )

    result = run_ai_job("novel_localization.step1_localize", "gpt-4.1", _prompt_payload(), "原文")
    work_note = _artifact(result, "work_note")

    assert work_note.type == "work_note"
    assert work_note.apply_mode == "replace"
    assert work_note.content == "称谓已本地化"


def test_step2_returns_work_note_suggestion_artifact(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        lambda model_id, messages: TextGenerationResult(
            text="【校验结论】不通过\n【问题说明】称呼不一致\n【建议工作注释】请统一角色称呼。"
        ),
    )

    result = run_ai_job("novel_localization.step2_review", "gpt-4.1", _prompt_payload(), "本地化稿")
    work_note = _artifact(result, "work_note")

    assert result.signals["passed"] is False
    assert work_note.type == "work_note"
    assert work_note.apply_mode == "append"
    assert work_note.content == "请统一角色称呼。"


def test_step2_passed_does_not_return_empty_work_note(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        lambda model_id, messages: TextGenerationResult(text="【校验结论】通过"),
    )

    result = run_ai_job("novel_localization.step2_review", "gpt-4.1", _prompt_payload(), "本地化稿")

    assert result.signals["passed"] is True
    assert _artifact_keys(result) == ["review_summary"]


def test_step1_rejects_missing_work_note_marker(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        lambda model_id, messages: TextGenerationResult(
            text="===本地化正文开始===\n本地化正文\n===本地化正文结束==="
        ),
    )

    with pytest.raises(AppError) as exc_info:
        run_ai_job("novel_localization.step1_localize", "gpt-4.1", _prompt_payload(), "原文")

    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"


def test_step1_rejects_english_translation_output(monkeypatch):
    english_text = (
        "This is a fully translated English draft with American names and American settings. "
        "It reads like a final English story instead of a Chinese localized manuscript. "
        "The output should not be accepted in step one because English belongs to translation."
    )
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        lambda model_id, messages: TextGenerationResult(
            text=(
                f"===本地化正文开始===\n{english_text}\n===本地化正文结束===\n"
                "===工作注释开始===\n误输出英文\n===工作注释结束==="
            )
        ),
    )

    with pytest.raises(AppError) as exc_info:
        run_ai_job("novel_localization.step1_localize", "gpt-4.1", _prompt_payload(), "原文")

    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"
    assert "中文本地化稿" in exc_info.value.message


def test_step2_rejects_missing_suggested_work_note(monkeypatch):
    monkeypatch.setattr(
        "app.services.executor.generate_text",
        lambda model_id, messages: TextGenerationResult(text="【校验结论】不通过\n【问题说明】称呼不一致"),
    )

    with pytest.raises(AppError) as exc_info:
        run_ai_job("novel_localization.step2_review", "gpt-4.1", _prompt_payload(), "本地化稿")

    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"


def test_p5_step1_merge_uses_work_note_artifact():
    job = AIJob(
        job_type="novel_localization.step1_localize",
        model_id="gpt-4.1",
        execution_mode="p5",
        input_payload={},
        output_payload={},
        callback_payload={},
        prompt_payload={},
    )
    job_id = uuid.uuid4()
    items = [
        AIJobWorkItem(
            job_id=job_id,
            name="chunk-1",
            kind="chunk",
            chunk_index=1,
            result_payload={
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
            result_payload={
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


def test_p5_step2_merge_uses_work_note_artifact():
    job = AIJob(
        job_type="novel_localization.step2_review",
        model_id="gpt-4.1",
        execution_mode="p5",
        input_payload={},
        output_payload={},
        callback_payload={},
        prompt_payload={},
    )
    job_id = uuid.uuid4()
    items = [
        AIJobWorkItem(
            job_id=job_id,
            name="chunk-0",
            kind="chunk",
            chunk_index=0,
            result_payload={
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
    assert work_note.apply_mode == "append"
    assert work_note.content == "分块 0:\n请统一称呼。"


def test_p5_step2_passed_does_not_return_empty_work_note():
    job = AIJob(
        job_type="novel_localization.step2_review",
        model_id="gpt-4.1",
        execution_mode="p5",
        input_payload={},
        output_payload={},
        callback_payload={},
        prompt_payload={},
    )
    item = AIJobWorkItem(
        job_id=uuid.uuid4(),
        name="chunk-0",
        kind="chunk",
        chunk_index=0,
        result_payload={
            "artifacts": [
                {"key": "review_summary", "type": "text", "label": "校验结果", "content": "已满足"}
            ],
            "signals": {"passed": True},
        },
    )

    result = merge_work_items(job, [item])

    assert result.signals["passed"] is True
    assert _artifact_keys(result) == ["review_summary"]
