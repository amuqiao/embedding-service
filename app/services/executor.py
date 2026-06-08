import re

from app.infrastructure.ai_gateway import generate_text
from app.schemas.jobs import JobResult


def _prompt_messages(prompt_payload: dict, input_text: str, job_type: str) -> list[dict[str, str]]:
    blocks = prompt_payload["blocks"]
    messages: list[dict[str, str]] = []
    for block in blocks:
        content = block["content"]
        if block["key"] == "user":
            content = f"{content}\n\n===待处理文本开始===\n{input_text}\n===待处理文本结束==="
        messages.append({"role": block["role"], "content": content})
    return messages


def _extract_between(text: str, start_marker: str, end_marker: str) -> str:
    pattern = re.escape(start_marker) + r"\s*(.*?)\s*" + re.escape(end_marker)
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _parse_step1_output(text: str) -> tuple[str, str]:
    localized = _extract_between(text, "===本地化正文开始===", "===本地化正文结束===")
    notes = _extract_between(text, "===工作注释开始===", "===工作注释结束===")
    if not localized:
        localized = text.strip()
    return localized, notes


def _parse_step2_output(text: str) -> tuple[bool, str, str]:
    passed = False
    review_summary = ""
    optimization_prompt = ""

    if "【校验结论】" in text:
        conclusion_match = re.search(r"【校验结论】\s*(\S+)", text)
        if conclusion_match:
            conclusion_text = conclusion_match.group(1)
            passed = "通过" in conclusion_text and "不通过" not in conclusion_text
    else:
        passed = "不通过" not in text and "failed" not in text.lower() and any(
            word in text for word in ["已满足", "通过", "符合"]
        )

    if not passed:
        problem_match = re.search(r"【问题说明】\s*(.*?)(?=【|$)", text, re.DOTALL)
        if problem_match:
            review_summary = problem_match.group(1).strip()
        else:
            review_summary = text.strip()

        suggestion_match = re.search(r"【优化建议】\s*(.*?)(?=【|$)", text, re.DOTALL)
        if suggestion_match:
            optimization_prompt = suggestion_match.group(1).strip()
        else:
            optimization_prompt = "请根据校验意见重新本地化。"
    else:
        review_summary = "已满足"

    return passed, review_summary, optimization_prompt


def _parse_step3_output(text: str) -> str:
    return text.strip()


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
                    "key": "notes",
                    "type": "text",
                    "label": "工作注释",
                    "content": notes,
                },
            ],
            signals={},
        )

    if job_type == "novel_localization.step2_review":
        passed, review_summary, optimization_prompt = _parse_step2_output(text)
        return JobResult(
            artifacts=[
                {
                    "key": "review_summary",
                    "type": "text",
                    "label": "校验结果",
                    "content": review_summary,
                },
                {
                    "key": "optimization_prompt",
                    "type": "prompt_suggestion",
                    "label": "优化建议 Prompt",
                    "content": optimization_prompt,
                    "target": {
                        "job_type": "novel_localization.step1_localize",
                        "prompt_block_key": "work_note",
                        "default_mode": "append",
                    },
                },
            ],
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
