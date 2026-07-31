from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ExecutionProviderMode = Literal["auto", "cpu", "cuda"]

CPU_EXECUTION_PROVIDER = "CPUExecutionProvider"
CUDA_EXECUTION_PROVIDER = "CUDAExecutionProvider"


class OnnxRuntimeIntegrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OnnxSessionRuntime:
    session: Any
    requested_providers: tuple[str, ...]
    actual_providers: tuple[str, ...]
    execution_provider: str


def import_onnxruntime() -> Any:
    try:
        import onnxruntime as ort
    except ModuleNotFoundError as exc:
        raise OnnxRuntimeIntegrationError(
            "onnxruntime is not installed; run: uv sync --extra audio-separation, or install onnxruntime-gpu in the GPU deployment image"
        ) from exc
    return ort


def available_execution_providers(ort: Any | None = None) -> tuple[str, ...]:
    runtime = ort or import_onnxruntime()
    return tuple(str(provider) for provider in runtime.get_available_providers())


def resolve_execution_providers(
    mode: ExecutionProviderMode,
    *,
    available_providers: tuple[str, ...],
) -> tuple[str, ...]:
    if mode == "cpu":
        if CPU_EXECUTION_PROVIDER not in available_providers:
            raise OnnxRuntimeIntegrationError(f"{CPU_EXECUTION_PROVIDER} is not available")
        return (CPU_EXECUTION_PROVIDER,)
    if mode == "cuda":
        if CUDA_EXECUTION_PROVIDER not in available_providers:
            raise OnnxRuntimeIntegrationError(f"{CUDA_EXECUTION_PROVIDER} is not available")
        return (CUDA_EXECUTION_PROVIDER,)
    if mode == "auto":
        if CUDA_EXECUTION_PROVIDER in available_providers:
            providers = [CUDA_EXECUTION_PROVIDER]
            if CPU_EXECUTION_PROVIDER in available_providers:
                providers.append(CPU_EXECUTION_PROVIDER)
            return tuple(providers)
        if CPU_EXECUTION_PROVIDER in available_providers:
            return (CPU_EXECUTION_PROVIDER,)
        raise OnnxRuntimeIntegrationError(f"{CPU_EXECUTION_PROVIDER} is not available")
    raise OnnxRuntimeIntegrationError("ONNX execution provider mode must be auto, cpu, or cuda")


def create_inference_session(
    model_path: Path,
    *,
    execution_provider_mode: ExecutionProviderMode,
    ort: Any | None = None,
) -> OnnxSessionRuntime:
    runtime = ort or import_onnxruntime()
    requested_providers = resolve_execution_providers(
        execution_provider_mode,
        available_providers=available_execution_providers(runtime),
    )
    try:
        session = runtime.InferenceSession(str(model_path), providers=list(requested_providers))
    except Exception as exc:
        raise OnnxRuntimeIntegrationError(f"failed to create ONNX Runtime session: {model_path}") from exc
    actual_providers = tuple(str(provider) for provider in session.get_providers())
    execution_provider = actual_providers[0] if actual_providers else requested_providers[0]
    if requested_providers[0] == CUDA_EXECUTION_PROVIDER and execution_provider != CUDA_EXECUTION_PROVIDER:
        raise OnnxRuntimeIntegrationError(
            f"{CUDA_EXECUTION_PROVIDER} was requested but session is using {execution_provider}"
        )
    return OnnxSessionRuntime(
        session=session,
        requested_providers=requested_providers,
        actual_providers=actual_providers,
        execution_provider=execution_provider,
    )
