import logging
import uuid

from app.core.exceptions import AppError
from app.services.ai_gateway_facade import generate_text_with_ledger
from app.core.prompt_templates import get_output_contract, get_system_prompt
from app.schemas.jobs import JobResult

logger = logging.getLogger(__name__)


def _append_output_contract(content: str, job_type: str) -> str:
    contract = get_output_contract(job_type)
    if not contract:
        return content
    return f"{content}\n\n{contract}"


def _prompt_messages(prompt_payload: dict, input_text: str, job_type: str) -> list[dict[str, str]]:
    system_prompt = get_system_prompt(job_type)
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for block in prompt_payload["blocks"]:
        if block["key"] == "system":
            continue
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


_REFUSAL_PREFIXES = (
    "i'm sorry", "i am sorry", "i cannot", "i can't",
    "i'm unable", "i am unable", "i apologize", "sorry, i",
)


def _is_model_refusal(text: str) -> bool:
    if len(text) > 400:
        return False
    lower = text.lower().lstrip()
    return any(lower.startswith(prefix) for prefix in _REFUSAL_PREFIXES)


def _model_output_invalid(message: str) -> AppError:
    return AppError("MODEL_OUTPUT_INVALID", message)


async def run_ai_job(
    *,
    job_type: str,
    model_id: str,
    prompt_payload: dict,
    input_text: str,
    caller_id: str,
    job_id: uuid.UUID,
    ai_scope_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID,
    request_id: str | None,
) -> JobResult:
    from app.jobs.factory import get_job_executor

    executor = get_job_executor(job_type)
    result = await generate_text_with_ledger(
        caller_id=caller_id,
        scope_type="job",
        scope_id=str(ai_scope_id or job_id),
        operation=f"{job_type}.execute",
        step_name="calling_model",
        request_id=request_id,
        job_id=job_id,
        attempt_id=attempt_id,
        job_type=job_type,
        model_id=model_id,
        messages=_prompt_messages(prompt_payload, input_text, job_type),
    )
    text = result.text.strip()
    if _is_model_refusal(text):
        raise _model_output_invalid(f"{job_type} 模型拒绝执行请求")
    return executor.parse_output(text)
