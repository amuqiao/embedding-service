from __future__ import annotations

from app.ai.adapters.base import TextGenerationRequest, TextGenerationResult
from app.ai.adapters.litellm_client import LiteLLMTextGenerationRequest, generate_text


class LiteLLMAdapter:
    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await generate_text(
            LiteLLMTextGenerationRequest(
                litellm_model=request.adapter_model,
                messages=request.messages,
                temperature=request.temperature,
                timeout_seconds=request.timeout_seconds,
                api_key=request.api_key,
                api_base=request.api_base,
                num_retries=request.num_retries,
                drop_params=request.drop_params,
            )
        )
