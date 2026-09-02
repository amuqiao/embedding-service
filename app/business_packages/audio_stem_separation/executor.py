from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np

from app.tools.private.audio_input import AUDIO_DECODE_NORMALIZE_TOOL_REF, OBJECT_STORAGE_READ_TOOL_REF
from app.core.config import settings
from app.core.exceptions import AppError
from app.tools.providers.onnx_runtime import (
    ExecutionProviderMode,
    OnnxRuntimeIntegrationError,
    create_inference_session,
)
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.business_packages.audio_stem_separation.shared import (
    DEFAULT_TIMEOUT_SECONDS,
    MODEL_ASSET_PATH,
    SOURCES,
    chunk_window as _chunk_window,
    load_model_asset as _load_model_asset,
    make_transition_window as _make_transition_window,
    segment_ranges as _segment_ranges,
    wav_bytes as _wav_bytes,
)
from app.business_packages.audio_stem_separation.errors import (
    AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
    AUDIO_STEM_INFERENCE_FAILED,
    AUDIO_STEM_INPUT_INVALID,
    AUDIO_STEM_MODEL_ASSET_MISSING,
    AUDIO_STEM_OUTPUT_INVALID,
    AUDIO_STEM_RUNTIME_UNAVAILABLE,
)
from app.business_packages.audio_stem_separation.storage_adapter import AudioStemSeparationStorageAdapter
from app.models.job import Job
from app.schemas.jobs import (
    AudioInputPlanSnapshot,
    AudioStemSeparationDurationMs,
    AudioStemSeparationInputObject,
    AudioStemSeparationParams,
    AudioStemSeparationResult,
    AudioStemSeparationRuntimeFields,
    AudioStemSeparationStemOutputs,
)
from app.services.job_runtime import runtime_fields_from_job

logger = logging.getLogger(__name__)

_RUNNER_CACHE: dict[tuple[str, str, str], "HTDemucsONNXRunner"] = {}
_RUNNER_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class SeparationOutput:
    stems: dict[str, np.ndarray]
    segment_count: int
    execution_provider: str
    inference_ms: int


def _hash_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _node_shape(node: Any) -> list[Any]:
    shape = getattr(node, "shape", None)
    if shape is None:
        return []
    return list(shape)


def _node_dtype(node: Any) -> str:
    type_name = str(getattr(node, "type", ""))
    if type_name.startswith("tensor(") and type_name.endswith(")"):
        return type_name[len("tensor(") : -1]
    return type_name


def _validate_session_signature(session: Any, *, stem: str, asset: dict[str, Any]) -> None:
    inputs = list(session.get_inputs())
    outputs = list(session.get_outputs())
    expected_input = asset["runtime"]["input"]
    expected_output = asset["runtime"]["output"]
    if len(inputs) != 1 or len(outputs) != 1:
        raise AppError(AUDIO_STEM_MODEL_ASSET_MISSING, f"{stem} ONNX signature is invalid")
    actual_input = inputs[0]
    actual_output = outputs[0]
    if (
        getattr(actual_input, "name", None) != expected_input["name"]
        or _node_dtype(actual_input) != expected_input["dtype"]
        or _node_shape(actual_input) != expected_input["shape"]
    ):
        raise AppError(AUDIO_STEM_MODEL_ASSET_MISSING, f"{stem} ONNX input signature does not match model_asset")
    if (
        getattr(actual_output, "name", None) != expected_output["name"]
        or _node_dtype(actual_output) != expected_output["dtype"]
        or _node_shape(actual_output) != expected_output["shape"]
    ):
        raise AppError(AUDIO_STEM_MODEL_ASSET_MISSING, f"{stem} ONNX output signature does not match model_asset")


class HTDemucsONNXRunner:
    def __init__(self, *, model_dir: Path, asset: dict[str, Any], execution_provider_mode: ExecutionProviderMode) -> None:
        self.model_dir = model_dir
        self.asset = asset
        self.sample_rate = int(asset["runtime"]["sample_rate"])
        self.channels = int(asset["runtime"]["channels"])
        self.segment_samples = int(asset["runtime"]["segment_samples"])
        self.overlap_ratio = float(asset["runtime"]["overlap_ratio"])
        self.model_version = str(asset["model"]["version"])
        self.execution_provider_mode = execution_provider_mode
        self.providers: list[str] = []
        self.sessions: dict[str, Any] = {}
        self.execution_provider = execution_provider_mode
        self._load_sessions()

    def _load_sessions(self) -> None:
        experts = self.asset.get("experts")
        if not isinstance(experts, dict):
            raise AppError(AUDIO_STEM_MODEL_ASSET_MISSING, "model_asset experts must be an object")
        for stem in SOURCES:
            spec = experts.get(stem)
            if not isinstance(spec, dict):
                raise AppError(AUDIO_STEM_MODEL_ASSET_MISSING, f"model_asset missing expert: {stem}")
            path = self.model_dir / str(spec["file"])
            if not path.is_file() or path.stat().st_size == 0:
                raise AppError(
                    AUDIO_STEM_MODEL_ASSET_MISSING,
                    f"audio stem ONNX file missing or empty: {path}",
                    details={"stem": stem, "path": str(path)},
                )
            if _hash_file(path) != str(spec["sha256"]):
                raise AppError(
                    AUDIO_STEM_MODEL_ASSET_MISSING,
                    f"audio stem ONNX sha256 mismatch: {path.name}",
                    details={"stem": stem, "path": str(path)},
                )
            try:
                runtime = create_inference_session(path, execution_provider_mode=self.execution_provider_mode)
            except OnnxRuntimeIntegrationError as exc:
                raise AppError(AUDIO_STEM_RUNTIME_UNAVAILABLE, f"failed to load {stem} ONNX session: {exc}") from exc
            _validate_session_signature(runtime.session, stem=stem, asset=self.asset)
            self.sessions[stem] = runtime.session
            if not self.providers:
                self.providers = list(runtime.requested_providers)
                self.execution_provider = runtime.execution_provider

    def separate(self, mix: np.ndarray) -> SeparationOutput:
        if mix.dtype != np.float32:
            raise AppError(AUDIO_STEM_INPUT_INVALID, "audio mix must be float32")
        if mix.ndim != 2 or mix.shape[0] != self.channels:
            raise AppError(AUDIO_STEM_INPUT_INVALID, f"expected stereo audio shape (2, samples), got {mix.shape}")

        total_len = int(mix.shape[1])
        overlap = int(self.segment_samples * self.overlap_ratio)
        stride = self.segment_samples - overlap
        segments = _segment_ranges(total_len=total_len, segment_samples=self.segment_samples, stride=stride)
        segment_count = len(segments)
        window = _make_transition_window(self.segment_samples, self.overlap_ratio)
        output = {stem: np.zeros((self.channels, total_len), dtype=np.float32) for stem in SOURCES}
        weight = np.zeros(total_len, dtype=np.float32)
        started = time.monotonic()

        for start, end in segments:
            chunk = mix[:, start:end]
            if chunk.shape[1] < self.segment_samples:
                chunk = np.pad(chunk, ((0, 0), (0, self.segment_samples - chunk.shape[1])), mode="constant")
            model_input = chunk[np.newaxis, ...].astype(np.float32)
            chunk_len = end - start
            chunk_window = _chunk_window(
                window,
                chunk_len=chunk_len,
                overlap=overlap,
                is_first=start == 0,
                is_last=end == total_len,
            )
            for stem in SOURCES:
                spec = self.asset["experts"][stem]
                try:
                    raw = self.sessions[stem].run(["stems"], {"mix": model_input})[0]
                except Exception as exc:
                    raise AppError(AUDIO_STEM_INFERENCE_FAILED, f"{stem} ONNX inference failed") from exc
                if not isinstance(raw, np.ndarray) or raw.shape != (1, 4, self.channels, self.segment_samples):
                    actual_shape = getattr(raw, "shape", None)
                    raise AppError(
                        AUDIO_STEM_OUTPUT_INVALID,
                        f"{stem} ONNX output shape is invalid",
                        details={"stem": stem, "actual_shape": list(actual_shape) if actual_shape is not None else None},
                    )
                if raw.dtype != np.float32:
                    raise AppError(AUDIO_STEM_OUTPUT_INVALID, f"{stem} ONNX output dtype is invalid")
                target_row = int(spec["target_row"])
                output[stem][:, start:end] += raw[0, target_row, :, :chunk_len] * chunk_window
            weight[start:end] += chunk_window

        weight = np.maximum(weight, 1e-8)
        for stem in SOURCES:
            output[stem] /= weight
        return SeparationOutput(
            stems=output,
            segment_count=segment_count,
            execution_provider=self.execution_provider,
            inference_ms=int((time.monotonic() - started) * 1000),
        )


def _runner() -> HTDemucsONNXRunner:
    model_dir = settings.job.audio_stem_separation.htdemucs_model_dir
    execution_provider_mode = cast(ExecutionProviderMode, settings.job.audio_stem_separation.execution_provider)
    cache_key = (str(model_dir), str(MODEL_ASSET_PATH), execution_provider_mode)
    with _RUNNER_CACHE_LOCK:
        runner = _RUNNER_CACHE.get(cache_key)
        if runner is None:
            runner = HTDemucsONNXRunner(
                model_dir=model_dir,
                asset=_load_model_asset(),
                execution_provider_mode=execution_provider_mode,
            )
            _RUNNER_CACHE[cache_key] = runner
        return runner


def _storage_adapter() -> AudioStemSeparationStorageAdapter:
    return AudioStemSeparationStorageAdapter.from_settings(settings)


def build_audio_input_plan(
    input_audio: AudioStemSeparationInputObject,
    *,
    max_duration_seconds: float | None,
) -> dict:
    return _storage_adapter().build_audio_input_plan(
        input_audio,
        max_duration_seconds=max_duration_seconds,
    )


def prepare_audio_input(plan: AudioInputPlanSnapshot | dict):
    return _storage_adapter().prepare_audio_input(plan)


def _attachment_content_disposition(job: Job, stem: str) -> str:
    return f'attachment; filename="audio-stem-separation-{job.id}-{stem}.wav"'


@register_job_type
class AudioStemSeparationJob(JobExecutor):
    name = "audio_stem_separation"
    visibility = "demo"
    role = "root"
    params_schema = AudioStemSeparationParams
    runtime_fields_schema_name = "AudioStemSeparationRuntimeFields"
    canonical_result_schema = AudioStemSeparationResult
    public_result_schema = AudioStemSeparationResult
    allow_callback = True
    timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    required_tool_refs = frozenset({OBJECT_STORAGE_READ_TOOL_REF, AUDIO_DECODE_NORMALIZE_TOOL_REF})
    allowed_error_codes = frozenset(
        {
            "INVALID_INPUT",
            "INPUT_HASH_MISMATCH",
            "INPUT_TOO_LARGE",
            "JOB_EXECUTION_FAILED",
            "JOB_RUNTIME_NOT_SUPPORTED",
            "JOB_STATE_TRANSITION_CONFLICT",
            "OSS_BUCKET_NOT_CONFIGURED",
            "OSS_FETCH_FAILED",
            "OSS_OBJECT_NOT_FOUND",
            "OSS_REGION_NOT_CONFIGURED",
            "OSS_WRITE_FAILED",
            AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
            AUDIO_STEM_INFERENCE_FAILED,
            AUDIO_STEM_INPUT_INVALID,
            AUDIO_STEM_MODEL_ASSET_MISSING,
            AUDIO_STEM_OUTPUT_INVALID,
            AUDIO_STEM_RUNTIME_UNAVAILABLE,
        }
    )

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AudioStemSeparationParams.model_validate(job_params)
        build_audio_input_plan(params.input_audio, max_duration_seconds=params.max_duration_seconds)
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AudioStemSeparationParams.model_validate(job_params)
        asset = _load_model_asset()
        runtime = asset["runtime"]
        return AudioStemSeparationRuntimeFields(
            media_input_plan=build_audio_input_plan(
                params.input_audio,
                max_duration_seconds=params.max_duration_seconds,
            ),
            onnx_model_version=str(asset["model"]["version"]),
            execution_provider=settings.job.audio_stem_separation.execution_provider,
            segment_seconds=float(runtime["segment_seconds"]),
            overlap_ratio=float(runtime["overlap_ratio"]),
        ).model_dump(exclude_none=True)

    async def _execute(self, job: Job, db) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._execute_sync, job)

    def _execute_sync(self, job: Job) -> dict[str, Any]:
        total_started = time.monotonic()
        io_started = time.monotonic()
        runtime_fields = AudioStemSeparationRuntimeFields.model_validate(runtime_fields_from_job(job))
        input_audio = prepare_audio_input(runtime_fields.media_input_plan)
        io_ms = int((time.monotonic() - io_started) * 1000)

        runner = _runner()
        separated = runner.separate(input_audio.data)

        stem_objects: dict[str, dict[str, str]] = {}
        write_started = time.monotonic()
        storage_adapter = _storage_adapter()
        for stem in SOURCES:
            data = _wav_bytes(separated.stems[stem], sample_rate=input_audio.sample_rate)
            stem_objects[stem] = storage_adapter.write_stem(
                job=job,
                stem=stem,
                data=data,
                content_disposition=_attachment_content_disposition(job, stem),
            )
        io_ms += int((time.monotonic() - write_started) * 1000)
        result = AudioStemSeparationResult(
            stems=AudioStemSeparationStemOutputs.model_validate(stem_objects),
            source_duration_seconds=input_audio.duration_seconds,
            segment_count=separated.segment_count,
            sample_rate=input_audio.sample_rate,
            channels=2,
            onnx_model_version=runner.model_version,
            execution_provider=separated.execution_provider,
            duration_ms=AudioStemSeparationDurationMs(
                io=io_ms,
                inference=separated.inference_ms,
                total=int((time.monotonic() - total_started) * 1000),
            ),
        )
        return result.model_dump()


def clear_runner_cache_for_tests() -> None:
    with _RUNNER_CACHE_LOCK:
        _RUNNER_CACHE.clear()
