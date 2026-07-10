from pathlib import Path

import pytest

from app.integrations.onnx_runtime import (
    CPU_EXECUTION_PROVIDER,
    CUDA_EXECUTION_PROVIDER,
    OnnxRuntimeIntegrationError,
    create_inference_session,
    resolve_execution_providers,
)


class FakeSession:
    override_providers: list[str] | None = None

    def __init__(self, _model_path: str, *, providers: list[str]) -> None:
        self.providers = self.override_providers or providers

    def get_providers(self) -> list[str]:
        return self.providers


class FakeOnnxRuntime:
    InferenceSession = FakeSession

    def __init__(self, providers: list[str]) -> None:
        self._providers = providers

    def get_available_providers(self) -> list[str]:
        return self._providers


def test_resolve_execution_providers_auto_prefers_cuda_when_available():
    assert resolve_execution_providers(
        "auto",
        available_providers=(CUDA_EXECUTION_PROVIDER, CPU_EXECUTION_PROVIDER),
    ) == (CUDA_EXECUTION_PROVIDER, CPU_EXECUTION_PROVIDER)


def test_resolve_execution_providers_auto_uses_cpu_without_cuda():
    assert resolve_execution_providers(
        "auto",
        available_providers=(CPU_EXECUTION_PROVIDER,),
    ) == (CPU_EXECUTION_PROVIDER,)


def test_resolve_execution_providers_cpu_forces_cpu():
    assert resolve_execution_providers(
        "cpu",
        available_providers=(CUDA_EXECUTION_PROVIDER, CPU_EXECUTION_PROVIDER),
    ) == (CPU_EXECUTION_PROVIDER,)


def test_resolve_execution_providers_cuda_requires_cuda():
    with pytest.raises(OnnxRuntimeIntegrationError, match=CUDA_EXECUTION_PROVIDER):
        resolve_execution_providers("cuda", available_providers=(CPU_EXECUTION_PROVIDER,))


def test_create_inference_session_reports_actual_execution_provider():
    runtime = create_inference_session(
        Path("model.onnx"),
        execution_provider_mode="cuda",
        ort=FakeOnnxRuntime([CUDA_EXECUTION_PROVIDER, CPU_EXECUTION_PROVIDER]),
    )

    assert runtime.requested_providers == (CUDA_EXECUTION_PROVIDER, CPU_EXECUTION_PROVIDER)
    assert runtime.actual_providers == (CUDA_EXECUTION_PROVIDER, CPU_EXECUTION_PROVIDER)
    assert runtime.execution_provider == CUDA_EXECUTION_PROVIDER


def test_create_inference_session_rejects_cuda_when_session_falls_back_to_cpu():
    FakeSession.override_providers = [CPU_EXECUTION_PROVIDER]
    try:
        with pytest.raises(OnnxRuntimeIntegrationError, match="was requested"):
            create_inference_session(
                Path("model.onnx"),
                execution_provider_mode="cuda",
                ort=FakeOnnxRuntime([CUDA_EXECUTION_PROVIDER, CPU_EXECUTION_PROVIDER]),
            )
    finally:
        FakeSession.override_providers = None
