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


def run_ai_job(job_type: str, model_id: str, prompt_payload: dict, input_text: str) -> JobResult:
    result = generate_text(model_id, _prompt_messages(prompt_payload, input_text, job_type))
    text = result.text.strip()

    if job_type == "novel_localization.step1_localize":
        return JobResult(
            artifacts=[
                {
                    "key": "localized_text",
                    "type": "text",
                    "label": "本地化正文",
                    "content": text,
                },
                {
                    "key": "notes",
                    "type": "text",
                    "label": "工作注释",
                    "content": "mock" if model_id == "mock-novel-localizer" else "",
                },
            ],
            signals={},
        )

    if job_type == "novel_localization.step2_review":
        passed = "不通过" not in text and "failed" not in text.lower()
        summary = "已满足" if passed else text
        return JobResult(
            artifacts=[
                {
                    "key": "review_summary",
                    "type": "text",
                    "label": "校验结果",
                    "content": summary,
                },
                {
                    "key": "optimization_prompt",
                    "type": "prompt_suggestion",
                    "label": "优化建议 Prompt",
                    "content": "" if passed else "请根据校验意见重新本地化，并保持角色称谓一致。",
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
        return JobResult(
            artifacts=[
                {
                    "key": "translated_text",
                    "type": "text",
                    "label": "英文终稿",
                    "content": text,
                }
            ],
            signals={},
        )

    raise KeyError(job_type)
