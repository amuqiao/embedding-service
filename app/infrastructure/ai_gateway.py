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


def generate_text(model_id: str, messages: list[dict[str, str]]) -> TextGenerationResult:
    model = get_enabled_model(model_id)
    if not model:
        raise KeyError(model_id)
    if model.provider == "mock":
        joined = "\n".join(m["content"] for m in messages)
        sample = joined[-1200:]
        return TextGenerationResult(text=f"{sample}\n\n[mock-result]")

    response = litellm.completion(
        model=model.litellm_model,
        messages=messages,
        temperature=0.7,
        num_retries=0,
        drop_params=True,
    )
    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    return TextGenerationResult(text=text, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
