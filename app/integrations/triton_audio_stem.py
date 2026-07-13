from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


class TritonAudioStemIntegrationError(RuntimeError):
    pass


class TritonAudioStemRuntimeUnavailable(TritonAudioStemIntegrationError):
    pass


class TritonAudioStemInferenceError(TritonAudioStemIntegrationError):
    pass


@dataclass(frozen=True)
class TritonAudioStemConfig:
    url: str
    token: str
    model_version: str
    request_timeout_seconds: float


class TritonAudioStemClient:
    def __init__(
        self,
        config: TritonAudioStemConfig,
        *,
        http_client: Any | None = None,
        httpclient_module: Any | None = None,
    ) -> None:
        if not config.url.strip():
            raise TritonAudioStemRuntimeUnavailable("AUDIO_STEM_TRITON_URL must be configured")
        if not config.model_version.strip():
            raise TritonAudioStemRuntimeUnavailable("AUDIO_STEM_TRITON_MODEL_VERSION must be configured")
        self.config = config
        self._httpclient_module = httpclient_module or _import_triton_httpclient()
        self._client = http_client
        if self._client is None:
            try:
                self._client = self._httpclient_module.InferenceServerClient(
                    url=self.config.url,
                    connection_timeout=self.config.request_timeout_seconds,
                    network_timeout=self.config.request_timeout_seconds,
                )
            except Exception as exc:
                raise TritonAudioStemRuntimeUnavailable("failed to create Triton HTTP client") from exc

    def infer_stems(self, *, model_name: str, model_input: np.ndarray) -> np.ndarray:
        if model_input.dtype != np.float32:
            raise TritonAudioStemInferenceError("triton audio stem input must be float32")
        if model_input.ndim != 3:
            raise TritonAudioStemInferenceError("triton audio stem input must have shape [1, 2, samples]")

        infer_input = self._httpclient_module.InferInput("mix", model_input.shape, "FP32")
        infer_input.set_data_from_numpy(model_input)
        outputs = [self._httpclient_module.InferRequestedOutput("stems")]
        headers = {}
        if self.config.token:
            headers["Authorization"] = self.config.token

        try:
            result = self._client.infer(
                model_name=model_name,
                model_version=self.config.model_version,
                inputs=[infer_input],
                outputs=outputs,
                headers=headers or None,
                timeout=int(self.config.request_timeout_seconds * 1_000_000),
            )
            stems = result.as_numpy("stems")
        except Exception as exc:
            raise TritonAudioStemInferenceError(f"Triton inference failed for model {model_name}") from exc
        if not isinstance(stems, np.ndarray):
            raise TritonAudioStemInferenceError(f"Triton model {model_name} did not return stems output")
        return stems


def _import_triton_httpclient() -> Any:
    try:
        import tritonclient.http as httpclient
    except ModuleNotFoundError as exc:
        raise TritonAudioStemRuntimeUnavailable(
            "tritonclient is not installed; install tritonclient[http] in the worker image"
        ) from exc
    return httpclient
