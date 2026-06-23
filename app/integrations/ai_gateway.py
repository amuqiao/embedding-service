import asyncio
from dataclasses import dataclass
from typing import Any

import litellm


@dataclass
class TextGenerationResult:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class TextGenerationRequest:
    litellm_model: str
    messages: list[dict[str, str]]
    temperature: float
    timeout_seconds: int
    api_key: str | None
    api_base: str | None
    num_retries: int
    drop_params: bool


def _usage_to_dict(usage: object | None) -> dict[str, Any] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        data = usage.model_dump()
    elif isinstance(usage, dict):
        data = dict(usage)
    else:
        data = {
            key: getattr(usage, key)
            for key in dir(usage)
            if not key.startswith("_") and not callable(getattr(usage, key))
        }
    return data if isinstance(data, dict) else None


def _usage_int(usage: object | None, key: str) -> int | None:
    if usage is None:
        return None
    raw = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    if raw is None:
        return None
    return int(raw or 0)


async def generate_text(request: TextGenerationRequest) -> TextGenerationResult:
    # asyncio.wait_for 是唯一可靠的总时长截断（httpx read_timeout 对 chunked LLM 响应无效）；
    # litellm.acompletion 同时传入相同 timeout 以控制泄漏线程的退出上限。
    response = await asyncio.wait_for(
        litellm.acompletion(
            model=request.litellm_model,
            messages=request.messages,
            temperature=request.temperature,
            timeout=request.timeout_seconds,
            api_key=request.api_key,
            api_base=request.api_base,
            num_retries=request.num_retries,
            drop_params=request.drop_params,
        ),
        timeout=request.timeout_seconds,
    )
    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    usage = getattr(response, "usage", None)
    usage_detail = _usage_to_dict(usage)
    prompt_tokens = _usage_int(usage, "prompt_tokens")
    completion_tokens = _usage_int(usage, "completion_tokens")
    return TextGenerationResult(
        text=text,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usage=usage_detail,
    )
