import asyncio
from dataclasses import dataclass

import litellm

from app.core.config import settings
from app.core.model_registry import get_enabled_model

if settings.ai_provider.openai_base_url:
    litellm.api_base = settings.ai_provider.openai_base_url


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
            temperature=model.temperature,
            timeout=settings.ai_provider.model_call_timeout_seconds,
            api_key=settings.ai_provider.openai_api_key_value or None,
            num_retries=model.num_retries,
            drop_params=model.drop_params,
        ),
        timeout=settings.ai_provider.model_call_timeout_seconds,
    )
    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    return TextGenerationResult(text=text, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
