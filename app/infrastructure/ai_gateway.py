import asyncio
import os
from dataclasses import dataclass

import litellm

from app.infrastructure.config import settings
from app.infrastructure.model_registry import get_enabled_model

if settings.OPENAI_BASE_URL:
    litellm.api_base = settings.OPENAI_BASE_URL
if settings.OPENAI_API_KEY:
    os.environ.setdefault("OPENAI_API_KEY", settings.OPENAI_API_KEY)


@dataclass
class TextGenerationResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


async def generate_text(model_id: str, messages: list[dict[str, str]]) -> TextGenerationResult:
    model = get_enabled_model(model_id)
    if not model:
        raise KeyError(model_id)
    # asyncio.wait_for 是唯一可靠的总时长截断（httpx read_timeout 对 chunked LLM 响应无效）；
    # litellm.acompletion 同时传入相同 timeout 以控制泄漏线程的退出上限。
    response = await asyncio.wait_for(
        litellm.acompletion(
            model=model.litellm_model,
            messages=messages,
            temperature=0.7,
            timeout=settings.MODEL_CALL_TIMEOUT_SECONDS,
            num_retries=0,
            drop_params=True,
        ),
        timeout=settings.MODEL_CALL_TIMEOUT_SECONDS,
    )
    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    return TextGenerationResult(text=text, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
