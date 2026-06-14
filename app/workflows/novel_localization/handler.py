"""Novel localization workflow handlers.

Registers three job types:
- novel_localization.step1_localize
- novel_localization.step2_review
- novel_localization.step3_translate
"""
from __future__ import annotations

import logging
import re
from typing import Any, TYPE_CHECKING

from app.core.workflow_registry import WorkflowHandler, register

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.job import AIJob, AIJobWorkItem
    from app.schemas.jobs import JobResult

logger = logging.getLogger(__name__)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _extract_between(text: str, start_marker: str, end_marker: str) -> str | None:
    pattern = re.escape(start_marker) + r"\s*(.*?)\s*" + re.escape(end_marker)
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else None


def _looks_like_english_translation(text: str) -> bool:
    cjk_count = len(re.findall(r"[一-鿿]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    if latin_count < 200:
        return False
    return cjk_count < 20 or latin_count > cjk_count * 3


def _artifact_content(result_payload: dict[str, Any], key: str) -> str:
    for artifact in result_payload.get("artifacts") or []:
        if artifact.get("key") == key:
            return str(artifact.get("content") or "")
    return ""


def _merge_texts(items: list[AIJobWorkItem], artifact_key: str) -> str:
    parts = [
        _artifact_content(item.result_payload or {}, artifact_key).strip()
        for item in sorted(items, key=lambda item: item.chunk_index)
    ]
    return "\n\n".join(part for part in parts if part)


# ── Parse helpers ─────────────────────────────────────────────────────────────

def _model_output_invalid(message: str):
    from app.core.exceptions import AppError
    return AppError("MODEL_OUTPUT_INVALID", message, status_code=502)


def _parse_step1_output(text: str) -> tuple[str, str]:
    notes = _extract_between(text, "===工作注释开始===", "===工作注释结束===")
    localized = _extract_between(text, "===本地化正文开始===", "===本地化正文结束===")
    if notes is None:
        logger.error("step1_localize 输出缺少工作注释标记 output_len=%d", len(text))
        raise _model_output_invalid("step1_localize 模型输出缺少工作注释标记")
    if not localized:
        logger.error("step1_localize 输出缺少本地化正文标记 output_len=%d", len(text))
        raise _model_output_invalid("step1_localize 模型输出缺少本地化正文标记或正文为空")
    if _looks_like_english_translation(localized):
        raise _model_output_invalid("step1_localize 本地化正文疑似英文译文；step1 必须输出中文本地化稿")
    return localized, notes


def _parse_step2_output(text: str) -> tuple[bool, str, str]:
    conclusion_match = re.search(r"【校验结论】\s*(通过|不通过)\s*(?=\n|$)", text)
    if not conclusion_match:
        raise _model_output_invalid("step2_review 模型输出缺少明确的【校验结论】通过/不通过")
    passed = conclusion_match.group(1) == "通过"
    if not passed:
        problem_match = re.search(r"【问题说明】\s*(.*?)(?=【|$)", text, re.DOTALL)
        if not problem_match or not problem_match.group(1).strip():
            raise _model_output_invalid("step2_review 校验不通过时缺少【问题说明】")
        review_summary = problem_match.group(1).strip()
        suggestion_match = re.search(r"【建议工作注释】\s*(.*?)(?=【|$)", text, re.DOTALL)
        if not suggestion_match or not suggestion_match.group(1).strip():
            raise _model_output_invalid("step2_review 校验不通过时缺少【建议工作注释】")
        suggested_work_note = suggestion_match.group(1).strip()
    else:
        review_summary = "已满足"
        suggested_work_note = ""
    return passed, review_summary, suggested_work_note


def _parse_step3_output(text: str) -> str:
    translated = text.strip()
    if not translated:
        raise _model_output_invalid("step3_translate 模型输出为空")
    return translated


# ── Merge helpers ─────────────────────────────────────────────────────────────

def _merge_review(items: list[AIJobWorkItem]) -> JobResult:
    from app.schemas.jobs import JobResult
    summaries: list[str] = []
    suggestions: list[str] = []
    passed = True
    for item in sorted(items, key=lambda item: item.chunk_index):
        payload = item.result_payload or {}
        signals = payload.get("signals") or {}
        if signals.get("passed") is False:
            passed = False
        summary = _artifact_content(payload, "review_summary").strip()
        suggestion = _artifact_content(payload, "work_note").strip()
        if summary:
            summaries.append(f"分块 {item.chunk_index}:\n{summary}")
        if suggestion:
            suggestions.append(f"分块 {item.chunk_index}:\n{suggestion}")
    summary_text = "已满足" if passed else "\n\n".join(summaries)
    suggestion_text = "" if passed else "\n\n".join(suggestions)
    artifacts: list[dict] = [
        {"key": "review_summary", "type": "text", "label": "校验结果", "content": summary_text}
    ]
    if not passed:
        artifacts.append({
            "key": "work_note", "type": "work_note", "label": "建议工作注释",
            "apply_mode": "replace", "content": suggestion_text,
        })
    return JobResult(artifacts=artifacts, signals={"passed": passed})


# ── Step1 handler ─────────────────────────────────────────────────────────────

class Step1LocalizeHandler(WorkflowHandler):
    job_type = "novel_localization.step1_localize"
    canvas_pattern = "memory_fanout"
    chunking_enabled = True
    max_single_chars = 20000
    chunk_size = 3000
    large_artifact_keys = frozenset({"localized_text"})

    def parse_output(self, text: str) -> JobResult:
        from app.schemas.jobs import JobResult
        localized, notes = _parse_step1_output(text)
        return JobResult(
            artifacts=[
                {"key": "localized_text", "type": "text", "label": "本地化正文", "content": localized},
                {"key": "work_note", "type": "work_note", "label": "工作注释", "apply_mode": "replace", "content": notes},
            ],
            signals={},
        )

    def merge_chunks(self, items: list[AIJobWorkItem]) -> JobResult:
        from app.schemas.jobs import JobResult
        chunk_items = [item for item in items if item.kind == "chunk"]
        localized = _merge_texts(chunk_items, "localized_text")
        notes = _merge_texts(chunk_items, "work_note")
        return JobResult(
            artifacts=[
                {"key": "localized_text", "type": "text", "label": "本地化正文", "content": localized},
                {"key": "work_note", "type": "work_note", "label": "工作注释", "apply_mode": "replace", "content": notes},
            ],
            signals={},
        )

    async def execute_special_item(self, item: AIJobWorkItem, job: AIJob, db: AsyncSession) -> dict[str, Any]:
        if item.kind != "memory":
            raise NotImplementedError(f"Step1LocalizeHandler: unexpected kind={item.kind!r}")
        from app.integrations.ai_gateway import generate_text
        from app.services.job_context import project_memory_from_generation

        system = next(
            (block["content"] for block in job.prompt_payload.get("blocks", []) if block.get("key") == "system"),
            "你是一位小说本地化编辑。",
        )
        chunks = (item.input_payload or {}).get("chunks") or []
        chunk_text = "\n\n".join(
            f"【分块 {chunk.get('chunk_index')}】\n{chunk.get('text', '')}"
            for chunk in chunks
        )
        mapping_prompt = (
            "请为以下小说生成供后续分块本地化、校验、翻译使用的项目记忆。"
            "只输出 JSON，不要输出 Markdown。JSON 字段必须包含："
            "characters、places、glossary、style_guide、cultural_rules、continuity_notes、chunk_summaries。"
            "characters/places/glossary/cultural_rules/continuity_notes/chunk_summaries 使用数组，style_guide 使用字符串。"
            "chunk_summaries 应按分块顺序记录每块摘要。\n\n"
            "===原文开始===\n"
            f"{chunk_text}\n"
            "===原文结束==="
        )
        mapping_result = await generate_text(
            job.model_id,
            [{"role": "system", "content": system}, {"role": "user", "content": mapping_prompt}],
        )
        return {"project_memory": project_memory_from_generation(mapping_result)}


# ── Step2 handler ─────────────────────────────────────────────────────────────

class Step2ReviewHandler(WorkflowHandler):
    job_type = "novel_localization.step2_review"
    canvas_pattern = "plain_chord"
    chunking_enabled = True
    max_single_chars = 20000
    chunk_size = 3000

    def parse_output(self, text: str) -> JobResult:
        from app.schemas.jobs import JobResult
        passed, review_summary, suggested_work_note = _parse_step2_output(text)
        artifacts: list[dict] = [
            {"key": "review_summary", "type": "text", "label": "校验结果", "content": review_summary}
        ]
        if not passed:
            artifacts.append({
                "key": "work_note", "type": "work_note", "label": "建议工作注释",
                "apply_mode": "replace", "content": suggested_work_note,
            })
        return JobResult(artifacts=artifacts, signals={"passed": passed})

    def merge_chunks(self, items: list[AIJobWorkItem]) -> JobResult:
        return _merge_review(items)


# ── Step3 handler ─────────────────────────────────────────────────────────────

class Step3TranslateHandler(WorkflowHandler):
    job_type = "novel_localization.step3_translate"
    canvas_pattern = "scan_chord"
    chunking_enabled = True
    max_single_chars = 20000
    chunk_size = 3000
    large_artifact_keys = frozenset({"translated_text"})

    def parse_output(self, text: str) -> JobResult:
        from app.schemas.jobs import JobResult
        translated_text = _parse_step3_output(text)
        return JobResult(
            artifacts=[{"key": "translated_text", "type": "text", "label": "英文终稿", "content": translated_text}],
            signals={},
        )

    def merge_chunks(self, items: list[AIJobWorkItem]) -> JobResult:
        from app.schemas.jobs import JobResult
        # scan item's result takes priority if available
        scan_content = ""
        for item in items:
            if item.kind == "scan" and item.result_payload:
                scan_content = _artifact_content(item.result_payload, "translated_text")
                break
        chunk_items = [item for item in items if item.kind == "chunk"]
        translated = scan_content.strip() or _merge_texts(chunk_items, "translated_text")
        return JobResult(
            artifacts=[{"key": "translated_text", "type": "text", "label": "英文终稿", "content": translated}],
            signals={"merge_scan_applied": bool(scan_content.strip())},
        )

    async def execute_special_item(self, item: AIJobWorkItem, job: AIJob, db: AsyncSession) -> dict[str, Any]:
        if item.kind != "scan":
            raise NotImplementedError(f"Step3TranslateHandler: unexpected kind={item.kind!r}")
        from app.integrations.ai_gateway import generate_text
        from app.repositories.job_repo import JobRepo
        from app.services.job_context import format_project_memory, project_memory_from_job

        all_items = await JobRepo.list_work_items(db, job.id)
        chunk_items = [i for i in all_items if i.kind == "chunk"]
        translated = _merge_texts(chunk_items, "translated_text")
        memory = project_memory_from_job(job)
        system = next(
            (block["content"] for block in job.prompt_payload.get("blocks", []) if block.get("key") == "system"),
            "你是一位小说英文终稿编辑。",
        )
        scan_prompt = (
            f"{format_project_memory(memory)}\n\n"
            "请对以下英文译稿做最终合并扫描，只修复明显的人名/地名/术语不一致、漏译、重复段落和分块衔接问题。"
            "不要重写风格，不要添加解释，只输出修订后的英文终稿。\n\n"
            "===英文译稿开始===\n"
            f"{translated}\n"
            "===英文译稿结束==="
        )
        scan_result = await generate_text(
            job.model_id,
            [{"role": "system", "content": system}, {"role": "user", "content": scan_prompt}],
        )
        return {
            "artifacts": [
                {"key": "translated_text", "type": "text", "label": "英文终稿", "content": scan_result.text.strip()}
            ],
            "signals": {"merge_scan_applied": True},
        }


# ── Registration ──────────────────────────────────────────────────────────────

def register_all() -> None:
    register(Step1LocalizeHandler())
    register(Step2ReviewHandler())
    register(Step3TranslateHandler())
