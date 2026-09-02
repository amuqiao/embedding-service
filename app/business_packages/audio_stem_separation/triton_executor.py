from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.tools.private.audio_input import AUDIO_DECODE_NORMALIZE_TOOL_REF, OBJECT_STORAGE_READ_TOOL_REF
from app.core.config import settings
from app.core.exceptions import AppError
from app.tools.providers.triton_audio_stem import (
    TritonAudioStemClient,
    TritonAudioStemConfig,
    TritonAudioStemInferenceError,
    TritonAudioStemRuntimeUnavailable,
)
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.business_packages.audio_stem_separation.shared import (
    DEFAULT_TIMEOUT_SECONDS,
    SOURCES,
    chunk_window as _chunk_window,
    load_model_asset as _load_model_asset,
    make_transition_window as _make_transition_window,
    segment_ranges as _segment_ranges,
    wav_bytes as _wav_bytes,
)
from app.business_packages.audio_stem_separation.triton_storage_adapter import AudioStemSeparationTritonStorageAdapter
from app.business_packages.audio_stem_separation.errors import (
    AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
    AUDIO_STEM_INFERENCE_FAILED,
    AUDIO_STEM_INPUT_INVALID,
    AUDIO_STEM_MODEL_ASSET_MISSING,
    AUDIO_STEM_OUTPUT_INVALID,
    AUDIO_STEM_RUNTIME_UNAVAILABLE,
)
from app.models.job import Job
from app.business_packages.audio_stem_separation.schemas import (
    AudioStemSeparationDurationMs,
    AudioStemSeparationInputObject,
    AudioStemSeparationStemOutputs,
)
from app.business_packages.audio_stem_separation.triton_schemas import (
    AudioStemSeparationTritonParams,
    AudioStemSeparationTritonResult,
    AudioStemSeparationTritonRuntimeFields,
)
from app.tools.private.audio_contracts import AudioInputPlanSnapshot
from app.services.job_runtime import runtime_fields_from_job

_RUNNER_CACHE: dict[tuple[str, str, str, float], "HTDemucsTritonRunner"] = {}
_RUNNER_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class TritonSeparationOutput:
    stems: dict[str, np.ndarray]
    segment_count: int
    inference_ms: int


class HTDemucsTritonRunner:
    def __init__(self, *, asset: dict[str, Any], client: TritonAudioStemClient) -> None:
        self.asset = asset
        self.client = client
        self.sample_rate = int(asset["runtime"]["sample_rate"])
        self.channels = int(asset["runtime"]["channels"])
        self.segment_samples = int(asset["runtime"]["segment_samples"])
        self.overlap_ratio = float(asset["runtime"]["overlap_ratio"])
        self.model_version = str(asset["model"]["version"])

    def separate(self, mix: np.ndarray) -> TritonSeparationOutput:
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
                model_name = f"htdemucs_ft_{stem}"
                try:
                    raw = self.client.infer_stems(model_name=model_name, model_input=model_input)
                except TritonAudioStemRuntimeUnavailable as exc:
                    raise AppError(AUDIO_STEM_RUNTIME_UNAVAILABLE, f"{stem} Triton runtime unavailable") from exc
                except TritonAudioStemInferenceError as exc:
                    raise AppError(AUDIO_STEM_INFERENCE_FAILED, f"{stem} Triton inference failed") from exc
                if not isinstance(raw, np.ndarray) or raw.shape != (1, 4, self.channels, self.segment_samples):
                    actual_shape = getattr(raw, "shape", None)
                    raise AppError(
                        AUDIO_STEM_OUTPUT_INVALID,
                        f"{stem} Triton output shape is invalid",
                        details={"stem": stem, "actual_shape": list(actual_shape) if actual_shape is not None else None},
                    )
                if raw.dtype != np.float32:
                    raise AppError(AUDIO_STEM_OUTPUT_INVALID, f"{stem} Triton output dtype is invalid")
                target_row = int(spec["target_row"])
                output[stem][:, start:end] += raw[0, target_row, :, :chunk_len] * chunk_window
            weight[start:end] += chunk_window

        weight = np.maximum(weight, 1e-8)
        for stem in SOURCES:
            output[stem] /= weight
        return TritonSeparationOutput(
            stems=output,
            segment_count=segment_count,
            inference_ms=int((time.monotonic() - started) * 1000),
        )


def _triton_config() -> TritonAudioStemConfig:
    return TritonAudioStemConfig(
        url=settings.job.audio_stem_triton.url,
        token=settings.job.audio_stem_triton.token_value,
        model_version=settings.job.audio_stem_triton.model_version,
        request_timeout_seconds=settings.job.audio_stem_triton.request_timeout_seconds,
    )


def _runner() -> HTDemucsTritonRunner:
    config = _triton_config()
    cache_key = (config.url, config.token, config.model_version, config.request_timeout_seconds)
    with _RUNNER_CACHE_LOCK:
        runner = _RUNNER_CACHE.get(cache_key)
        if runner is None:
            try:
                client = TritonAudioStemClient(config)
            except TritonAudioStemRuntimeUnavailable as exc:
                raise AppError(AUDIO_STEM_RUNTIME_UNAVAILABLE, f"audio stem Triton runtime unavailable: {exc}") from exc
            runner = HTDemucsTritonRunner(asset=_load_model_asset(), client=client)
            _RUNNER_CACHE[cache_key] = runner
        return runner


def _storage_adapter() -> AudioStemSeparationTritonStorageAdapter:
    return AudioStemSeparationTritonStorageAdapter.from_settings(settings)


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
    return f'attachment; filename="audio-stem-separation-triton-{job.id}-{stem}.wav"'


@register_job_type
class AudioStemSeparationTritonJob(JobExecutor):
    name = "audio_stem_separation_triton"
    visibility = "demo"
    role = "root"
    params_schema = AudioStemSeparationTritonParams
    runtime_fields_schema_name = "AudioStemSeparationTritonRuntimeFields"
    canonical_result_schema = AudioStemSeparationTritonResult
    public_result_schema = AudioStemSeparationTritonResult
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
        params = AudioStemSeparationTritonParams.model_validate(job_params)
        build_audio_input_plan(params.input_audio, max_duration_seconds=params.max_duration_seconds)
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = AudioStemSeparationTritonParams.model_validate(job_params)
        asset = _load_model_asset()
        runtime = asset["runtime"]
        return AudioStemSeparationTritonRuntimeFields(
            media_input_plan=build_audio_input_plan(
                params.input_audio,
                max_duration_seconds=params.max_duration_seconds,
            ),
            onnx_model_version=str(asset["model"]["version"]),
            triton_model_version=settings.job.audio_stem_triton.model_version,
            segment_seconds=float(runtime["segment_seconds"]),
            overlap_ratio=float(runtime["overlap_ratio"]),
        ).model_dump(exclude_none=True)

    async def _execute(self, job: Job, db) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._execute_sync, job)

    def _execute_sync(self, job: Job) -> dict[str, Any]:
        total_started = time.monotonic()
        io_started = time.monotonic()
        runtime_fields = AudioStemSeparationTritonRuntimeFields.model_validate(runtime_fields_from_job(job))
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
        result = AudioStemSeparationTritonResult(
            stems=AudioStemSeparationStemOutputs.model_validate(stem_objects),
            source_duration_seconds=input_audio.duration_seconds,
            segment_count=separated.segment_count,
            sample_rate=input_audio.sample_rate,
            channels=2,
            onnx_model_version=runner.model_version,
            triton_model_version=settings.job.audio_stem_triton.model_version,
            duration_ms=AudioStemSeparationDurationMs(
                io=io_ms,
                inference=separated.inference_ms,
                total=int((time.monotonic() - total_started) * 1000),
            ),
        )
        return result.model_dump()


def clear_triton_runner_cache_for_tests() -> None:
    with _RUNNER_CACHE_LOCK:
        _RUNNER_CACHE.clear()
