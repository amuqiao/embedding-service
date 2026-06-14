import uuid
from typing import Any

from celery import chain, chord, group
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.job import AIJob, AIJobWorkItem
from app.repositories.job_repo import JobRepo
from app.schemas.jobs import JobResult
from app.integrations.ai_gateway import generate_text
from app.services.job_context import (
    append_context_to_prompt,
    build_chunk_context,
    format_project_memory,
    project_memory_from_generation,
    project_memory_from_job,
)
from app.services.executor import run_ai_job
from app.services.job_planner import JobPlan, build_job_plan
from app.services.jobs import _load_input_text, _persist_large_artifacts, get_job_or_404


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


def _first_result_value(items: list[AIJobWorkItem], kind: str, key: str) -> Any:
    for item in sorted(items, key=lambda item: item.chunk_index):
        if item.kind == kind and item.result_payload:
            return item.result_payload.get(key)
    return None


def _merge_review(items: list[AIJobWorkItem]) -> JobResult:
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
    artifacts = [
        {
            "key": "review_summary",
            "type": "text",
            "label": "校验结果",
            "content": summary_text,
        }
    ]
    if not passed:
        artifacts.append(
            {
                "key": "work_note",
                "type": "work_note",
                "label": "建议工作注释",
                "apply_mode": "replace",
                "content": suggestion_text,
            }
        )
    return JobResult(
        artifacts=artifacts,
        signals={"passed": passed},
    )


def merge_work_items(job: AIJob, items: list[AIJobWorkItem]) -> JobResult:
    if job.execution_mode == "single":
        whole = next(item for item in items if item.kind == "whole")
        return JobResult.model_validate(whole.result_payload)

    chunk_items = [item for item in items if item.kind == "chunk"]
    if job.job_type == "novel_localization.step1_localize":
        localized = _merge_texts(chunk_items, "localized_text")
        notes = _merge_texts(chunk_items, "work_note")
        return JobResult(
            artifacts=[
                {"key": "localized_text", "type": "text", "label": "本地化正文", "content": localized},
                {
                    "key": "work_note",
                    "type": "work_note",
                    "label": "工作注释",
                    "apply_mode": "replace",
                    "content": notes,
                },
            ],
            signals={},
        )

    if job.job_type == "novel_localization.step2_review":
        return _merge_review(chunk_items)

    if job.job_type == "novel_localization.step3_translate":
        scanned = _artifact_content(_first_scan_payload(items), "translated_text")
        translated = scanned.strip() or _merge_texts(chunk_items, "translated_text")
        return JobResult(
            artifacts=[
                {"key": "translated_text", "type": "text", "label": "英文终稿", "content": translated},
            ],
            signals={"merge_scan_applied": bool(scanned.strip())},
        )

    raise KeyError(job.job_type)


def _first_scan_payload(items: list[AIJobWorkItem]) -> dict[str, Any]:
    for item in items:
        if item.kind == "scan" and item.result_payload:
            return item.result_payload
    return {}


async def create_work_items(db: AsyncSession, job: AIJob, plan: JobPlan) -> dict[str, uuid.UUID]:
    item_ids: dict[str, uuid.UUID] = {}
    for item in plan.work_items:
        created = await JobRepo.create_work_item(
            db,
            job_id=job.id,
            name=item.name,
            kind=item.kind,
            chunk_index=item.chunk_index,
            input_payload=item.input_payload,
        )
        item_ids[f"{item.kind}:{item.chunk_index}"] = created.id
    return item_ids


async def plan_job(db: AsyncSession, job_id: uuid.UUID) -> tuple[AIJob, JobPlan, dict[str, uuid.UUID]]:
    job = await get_job_or_404(db, job_id)
    if job.celery_task_id:
        await JobRepo.mark_running(db, job_id, celery_task_id=job.celery_task_id, progress_text="正在规划执行策略")
    input_text = _load_input_text(job)
    plan = build_job_plan(job.job_type, input_text)
    item_ids = await create_work_items(db, job, plan)
    await JobRepo.set_execution_plan(
        db,
        job_id,
        execution_mode=plan.execution_mode,
        execution_plan=plan.model_dump(),
    )
    await JobRepo.update_progress(
        db,
        job_id,
        progress_percent=10,
        progress_text=f"已生成 {plan.execution_mode} 执行计划",
    )
    await db.commit()
    await db.refresh(job)
    return job, plan, item_ids


async def execute_work_item(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    item_id: uuid.UUID,
    celery_task_id: str | None,
) -> dict[str, Any]:
    job = await get_job_or_404(db, job_id)
    item = await JobRepo.get_work_item(db, item_id)
    if not item:
        raise RuntimeError(f"work item not found: {item_id}")
    await JobRepo.mark_work_item_running(db, item_id, celery_task_id=celery_task_id)
    await JobRepo.update_progress(db, job_id, progress_percent=30, progress_text=f"正在执行 {item.kind}")
    await db.commit()

    input_text = (item.input_payload or {}).get("text") or ""
    if item.kind == "memory":
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
        mapping_result = generate_text(job.model_id, [{"role": "system", "content": system}, {"role": "user", "content": mapping_prompt}])
        result_payload = {
            "project_memory": project_memory_from_generation(mapping_result),
        }
    elif item.kind == "scan":
        translated = _merge_texts(
            [existing for existing in await JobRepo.list_work_items(db, job_id) if existing.kind == "chunk"],
            "translated_text",
        )
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
        scan_result = generate_text(job.model_id, [{"role": "system", "content": system}, {"role": "user", "content": scan_prompt}])
        result_payload = {
            "artifacts": [
                {
                    "key": "translated_text",
                    "type": "text",
                    "label": "英文终稿",
                    "content": scan_result.text.strip(),
                }
            ],
            "signals": {"merge_scan_applied": True},
        }
    else:
        prompt_payload = job.prompt_payload
        if item.kind == "chunk":
            memory = project_memory_from_job(job)
            if job.job_type == "novel_localization.step1_localize":
                memory = _first_result_value(await JobRepo.list_work_items(db, job_id), "memory", "project_memory")
            context_text = build_chunk_context(
                memory=memory,
                chunk_index=item.chunk_index,
                chunk_count=(job.execution_plan or {}).get("chunk_count") or 1,
            )
            prompt_payload = append_context_to_prompt(job, context_text)
        result = run_ai_job(job.job_type, job.model_id, prompt_payload, input_text)
        result_payload = result.model_dump()

    await JobRepo.mark_work_item_succeeded(db, item_id, result_payload)
    await db.commit()
    return {"work_item_id": str(item_id), "kind": item.kind, "chunk_index": item.chunk_index}


async def finalize_job(db: AsyncSession, job_id: uuid.UUID) -> dict[str, Any]:
    job = await get_job_or_404(db, job_id)
    items = await JobRepo.list_work_items(db, job_id)
    failed = [item for item in items if item.status == "failed"]
    if failed:
        raise AppError(
            "WORK_ITEM_FAILED",
            "内部执行分片失败",
            status_code=500,
            details={"failed_items": [str(item.id) for item in failed]},
        )

    result = merge_work_items(job, items)
    result_payload = _persist_large_artifacts(job, result)
    for item in items:
        if item.kind == "merge" and item.status == "queued":
            await JobRepo.mark_work_item_succeeded(
                db,
                item.id,
                {
                    "merged": True,
                    "artifact_keys": [artifact.key for artifact in result.artifacts],
                },
            )
    await JobRepo.update_progress(db, job_id, progress_percent=90, progress_text="正在写入最终结果")
    if not job.celery_task_id:
        raise RuntimeError(f"job has no celery_task_id: {job_id}")
    await JobRepo.mark_succeeded(db, job_id, celery_task_id=job.celery_task_id, result_payload=result_payload)
    await db.commit()
    await db.refresh(job)
    from app.tasks.jobs import deliver_callback_for_job
    await deliver_callback_for_job(job_id)
    return {"job_id": str(job_id), "status": "succeeded"}


async def fail_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    item_id: uuid.UUID | None,
    error_payload: dict[str, Any],
) -> None:
    if item_id:
        await JobRepo.mark_work_item_failed(db, item_id, error_payload)
    job = await get_job_or_404(db, job_id)
    await JobRepo.mark_failed(db, job_id, error_payload, celery_task_id=job.celery_task_id)
    await db.commit()
    from app.tasks.jobs import deliver_callback_for_job
    await deliver_callback_for_job(job_id)


def build_canvas(job_id: uuid.UUID, plan: JobPlan, item_ids: dict[str, uuid.UUID]):
    from app.tasks.jobs import execute_work_item_task, fanout_after_mapping_task, finalize_job_task

    job_id_text = str(job_id)
    if plan.execution_mode == "single":
        whole_id = str(item_ids["whole:0"])
        return chain(
            execute_work_item_task.s(job_id_text, whole_id),
            finalize_job_task.s(job_id_text),
        )

    chunk_signatures = [
        execute_work_item_task.s(job_id_text, str(item_ids[f"chunk:{item.chunk_index}"]))
        for item in plan.work_items
        if item.kind == "chunk"
    ]

    if "memory:0" in item_ids:
        chunk_item_ids = [
            str(item_ids[f"chunk:{item.chunk_index}"])
            for item in plan.work_items
            if item.kind == "chunk"
        ]
        return chain(
            execute_work_item_task.s(job_id_text, str(item_ids["memory:0"])),
            fanout_after_mapping_task.s(job_id_text, chunk_item_ids),
        )

    if "scan:{}".format(plan.chunk_count + 2) in item_ids:
        scan_id = str(item_ids[f"scan:{plan.chunk_count + 2}"])
        return chain(
            chord(group(chunk_signatures), execute_work_item_task.si(job_id_text, scan_id)),
            finalize_job_task.s(job_id_text),
        )

    return chord(group(chunk_signatures), finalize_job_task.s(job_id_text))
