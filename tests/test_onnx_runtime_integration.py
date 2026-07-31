import builtins
from pathlib import Path
import tomllib

import pytest

from app.integrations.onnx_runtime import (
    CPU_EXECUTION_PROVIDER,
    CUDA_EXECUTION_PROVIDER,
    OnnxRuntimeIntegrationError,
    create_inference_session,
    import_onnxruntime,
    resolve_execution_providers,
)

ROOT_DIR = Path(__file__).resolve().parents[1]


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


def test_audio_stem_cpu_runtime_dependencies_are_optional_project_dependencies():
    project = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert "onnxruntime==1.27.0" not in project["dependencies"]
    assert "soundfile==0.14.0" not in project["dependencies"]
    assert "tritonclient[http]==2.70.0" not in project["dependencies"]
    assert project["optional-dependencies"]["audio-separation"] == [
        "onnxruntime==1.27.0",
        "soundfile==0.14.0",
        "tritonclient[http]==2.70.0",
    ]


def test_audio_stem_cpu_runtime_dependencies_are_extra_marked_in_lockfile():
    lock = tomllib.loads((ROOT_DIR / "uv.lock").read_text(encoding="utf-8"))
    package = next(item for item in lock["package"] if item["name"] == "fastapi-best-ai-architecture-v2")
    markers_by_name: dict[str, set[str]] = {}
    for item in package["metadata"]["requires-dist"]:
        marker = item.get("marker")
        if marker:
            markers_by_name.setdefault(item["name"], set()).add(marker)

    assert markers_by_name["onnxruntime"] == {"extra == 'audio-separation'"}
    assert "extra == 'audio-separation'" in markers_by_name["soundfile"]
    assert "extra == 'audio-separation'" in markers_by_name["tritonclient"]


def test_import_onnxruntime_missing_dependency_points_to_audio_separation_extra(monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name == "onnxruntime":
            raise ModuleNotFoundError("No module named 'onnxruntime'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(OnnxRuntimeIntegrationError, match="uv sync --extra audio-separation"):
        import_onnxruntime()


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

    assert runtime.requested_providers == (CUDA_EXECUTION_PROVIDER,)
    assert runtime.actual_providers == (CUDA_EXECUTION_PROVIDER,)
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
