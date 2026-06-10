import re

from app.core.exceptions import AppError
from app.infrastructure.ai_gateway import generate_text
from app.infrastructure.prompt_templates import get_output_contract
from app.schemas.jobs import JobResult


def _append_output_contract(content: str, job_type: str) -> str:
    contract = get_output_contract(job_type)
    if not contract:
        return content
    return f"{content}\n\n{contract}"


def _prompt_messages(prompt_payload: dict, input_text: str, job_type: str) -> list[dict[str, str]]:
    blocks = prompt_payload["blocks"]
    messages: list[dict[str, str]] = []
    for block in blocks:
        content = block["content"].strip()
        if block["key"] == "user":
            content = _append_output_contract(content, job_type)
            content = f"{content}\n\n===待处理文本开始===\n{input_text}\n===待处理文本结束==="
        elif block["key"] == "work_note":
            if not content:
                continue
            content = f"【已有工作注释 / 上一轮约束】\n{content}"
        messages.append({"role": block["role"], "content": content})
    return messages


def _model_output_invalid(message: str) -> AppError:
    return AppError("MODEL_OUTPUT_INVALID", message, status_code=502)


def _looks_like_english_translation(text: str) -> bool:
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    if latin_count < 200:
        return False
    return cjk_count < 20 or latin_count > cjk_count * 3


def _extract_between(text: str, start_marker: str, end_marker: str) -> str | None:
    pattern = re.escape(start_marker) + r"\s*(.*?)\s*" + re.escape(end_marker)
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else None


def _parse_step1_output(text: str) -> tuple[str, str]:
    notes = _extract_between(text, "===工作注释开始===", "===工作注释结束===")
    localized = _extract_between(text, "===本地化正文开始===", "===本地化正文结束===")
    if notes is None:
        raise _model_output_invalid("step1_localize 模型输出缺少工作注释标记")
    if not localized:
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


def run_ai_job(job_type: str, model_id: str, prompt_payload: dict, input_text: str) -> JobResult:
    result = generate_text(model_id, _prompt_messages(prompt_payload, input_text, job_type))
    text = result.text.strip()

    if job_type == "novel_localization.step1_localize":
        localized_text, notes = _parse_step1_output(text)
        return JobResult(
            artifacts=[
                {
                    "key": "localized_text",
                    "type": "text",
                    "label": "本地化正文",
                    "content": localized_text,
                },
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

    if job_type == "novel_localization.step2_review":
        passed, review_summary, suggested_work_note = _parse_step2_output(text)
        artifacts = [
            {
                "key": "review_summary",
                "type": "text",
                "label": "校验结果",
                "content": review_summary,
            }
        ]
        if not passed:
            artifacts.append(
                {
                    "key": "work_note",
                    "type": "work_note",
                    "label": "建议工作注释",
                    "apply_mode": "replace",
                    "content": suggested_work_note,
                }
            )
        return JobResult(
            artifacts=artifacts,
            signals={"passed": passed},
        )

    if job_type == "novel_localization.step3_translate":
        translated_text = _parse_step3_output(text)
        return JobResult(
            artifacts=[
                {
                    "key": "translated_text",
                    "type": "text",
                    "label": "英文终稿",
                    "content": translated_text,
                }
            ],
            signals={},
        )

    raise KeyError(job_type)
