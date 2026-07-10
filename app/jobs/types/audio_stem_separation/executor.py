from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import yaml

from app.core.config import settings
from app.core.exceptions import AppError
from app.integrations.onnx_runtime import (
    ExecutionProviderMode,
    OnnxRuntimeIntegrationError,
    create_inference_session,
)
from app.integrations.object_storage import bare_sha256, sha256_digest
from app.integrations.storage import storage
from app.jobs.adapters.http_url_input import read_http_url_bytes
from app.jobs.adapters.oss_url_ref import canonical_ref_from_oss_url_ref, oss_url_ref_from_output_object
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.jobs.types.audio_stem_separation.errors import (
    AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
    AUDIO_STEM_INFERENCE_FAILED,
    AUDIO_STEM_INPUT_INVALID,
    AUDIO_STEM_MODEL_ASSET_MISSING,
    AUDIO_STEM_OUTPUT_INVALID,
    AUDIO_STEM_RUNTIME_UNAVAILABLE,
)
from app.models.job import Job
from app.schemas.jobs import (
    AudioStemSeparationDurationMs,
    AudioStemSeparationInputObject,
    AudioStemSeparationParams,
    AudioStemSeparationResult,
    AudioStemSeparationRuntimeFields,
    AudioStemSeparationStemOutputs,
)
from app.services.job_runtime import job_params_from_job, output_target_from_job

logger = logging.getLogger(__name__)

MODEL_ASSET_PATH = Path(__file__).with_name("model_asset.yaml")
SOURCES = ("drums", "bass", "other", "vocals")
AUDIO_WAV_CONTENT_TYPE = "audio/wav"
DEFAULT_TIMEOUT_SECONDS = 2400

_RUNNER_CACHE: dict[tuple[str, str, str], "HTDemucsONNXRunner"] = {}
_RUNNER_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class InputAudio:
    data: np.ndarray
    sample_rate: int
    duration_seconds: float


@dataclass(frozen=True)
class SeparationOutput:
    stems: dict[str, np.ndarray]
    segment_count: int
    execution_provider: str
    inference_ms: int


def _load_model_asset() -> dict[str, Any]:
    try:
        with MODEL_ASSET_PATH.open("r", encoding="utf-8") as handle:
            asset = yaml.safe_load(handle)
    except OSError as exc:
        raise AppError(AUDIO_STEM_MODEL_ASSET_MISSING, "audio stem model_asset.yaml is missing") from exc
    if not isinstance(asset, dict):
        raise AppError(AUDIO_STEM_MODEL_ASSET_MISSING, "audio stem model_asset.yaml is invalid")
    return asset


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


def _segment_ranges(*, total_len: int, segment_samples: int, stride: int) -> list[tuple[int, int]]:
    if total_len < 1:
        raise AppError(AUDIO_STEM_INPUT_INVALID, "audio input must not be empty")
    segment_count = 1 + max(0, (total_len - segment_samples + stride - 1) // stride)
    return [
        (index * stride, min(index * stride + segment_samples, total_len))
        for index in range(segment_count)
    ]


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


def _make_transition_window(segment_samples: int, overlap_ratio: float) -> np.ndarray:
    transition = int(segment_samples * overlap_ratio)
    window = np.ones(segment_samples, dtype=np.float32)
    fade = np.linspace(0, 1, transition, dtype=np.float32)
    window[:transition] = fade
    window[-transition:] = fade[::-1]
    return window


def _chunk_window(
    window: np.ndarray,
    *,
    chunk_len: int,
    overlap: int,
    is_first: bool,
    is_last: bool,
) -> np.ndarray:
    chunk_window = window[:chunk_len].copy()
    edge = min(overlap, chunk_len)
    if is_first:
        chunk_window[:edge] = 1.0
    if is_last:
        chunk_window[-edge:] = 1.0
    return chunk_window


def _runner() -> HTDemucsONNXRunner:
    model_dir = settings.job.htdemucs_model_dir
    execution_provider_mode = cast(ExecutionProviderMode, settings.job.audio_stem_separation_execution_provider)
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


def _canonical_input_ref(input_audio: AudioStemSeparationInputObject):
    try:
        return canonical_ref_from_oss_url_ref(
            input_audio.model_dump(),
            allowed_buckets=settings.job.audio_stem_separation_allowed_oss_buckets,
            allowed_regions=settings.job.audio_stem_separation_allowed_oss_regions,
            allowed_content_types={AUDIO_WAV_CONTENT_TYPE},
            public_endpoint=settings.storage.oss_public_endpoint or None,
            public_endpoint_bucket=getattr(settings.storage, "oss_bucket", "") or None,
            public_endpoint_region=getattr(settings.storage, "oss_region", "") or None,
        )
    except AppError as exc:
        raise AppError(
            AUDIO_STEM_INPUT_INVALID,
            "audio stem input_audio is invalid",
            details={"source_reason": exc.code, **(exc.details or {})},
        ) from exc


def _read_input_audio(input_audio: AudioStemSeparationInputObject, *, max_duration_seconds: float | None) -> InputAudio:
    ref = _canonical_input_ref(input_audio)
    payload = input_audio.model_dump()
    data = read_http_url_bytes(str(payload["public_url"]).strip(), max_bytes=settings.job.oss_input_max_bytes)
    if ref.content_hash and sha256_digest(data) != ref.content_hash:
        raise AppError("INPUT_HASH_MISMATCH", "audio stem input sha256 mismatch")
    try:
        import soundfile as sf
    except ModuleNotFoundError as exc:
        raise AppError(
            AUDIO_STEM_INFERENCE_FAILED,
            "soundfile is not installed; run: uv sync --extra audio-separation",
        ) from exc
    try:
        audio, sample_rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:
        raise AppError(AUDIO_STEM_INPUT_INVALID, "audio stem input must be a readable WAV file") from exc
    if sample_rate != 44100:
        raise AppError(
            AUDIO_STEM_INPUT_INVALID,
            "audio stem input sample_rate must be 44100",
            details={"actual": sample_rate, "expected": 44100},
        )
    if audio.ndim != 2 or audio.shape[1] != 2:
        actual_channels = int(audio.shape[1]) if audio.ndim == 2 else None
        raise AppError(
            AUDIO_STEM_INPUT_INVALID,
            "audio stem input must be stereo",
            details={"actual": actual_channels, "expected": 2},
        )
    duration_seconds = float(audio.shape[0] / sample_rate)
    if max_duration_seconds is not None and duration_seconds > max_duration_seconds:
        raise AppError(
            AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
            "audio stem input duration exceeds max_duration_seconds",
            details={"actual": duration_seconds, "max_duration_seconds": max_duration_seconds},
        )
    return InputAudio(data=audio.T.astype(np.float32), sample_rate=int(sample_rate), duration_seconds=duration_seconds)


def _wav_bytes(audio: np.ndarray, *, sample_rate: int) -> bytes:
    try:
        import soundfile as sf
    except ModuleNotFoundError as exc:
        raise AppError(
            AUDIO_STEM_INFERENCE_FAILED,
            "soundfile is not installed; run: uv sync --extra audio-separation",
        ) from exc
    buffer = io.BytesIO()
    try:
        sf.write(buffer, audio.T, sample_rate, format="WAV", subtype="PCM_16")
    except Exception as exc:
        raise AppError(AUDIO_STEM_OUTPUT_INVALID, "failed to encode audio stem WAV") from exc
    return buffer.getvalue()


def _output_key(job: Job, stem: str) -> str:
    output_target = output_target_from_job(job)
    prefix = output_target["oss_prefix"].strip("/")
    key = f"audio-stem-separation/{job.id}/{stem}.wav"
    return f"{prefix}/{key}" if prefix else key


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
        _canonical_input_ref(params.input_audio)
        return params.model_dump(exclude_none=True)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        asset = _load_model_asset()
        runtime = asset["runtime"]
        return AudioStemSeparationRuntimeFields(
            onnx_model_version=str(asset["model"]["version"]),
            execution_provider=settings.job.audio_stem_separation_execution_provider,
            segment_seconds=float(runtime["segment_seconds"]),
            overlap_ratio=float(runtime["overlap_ratio"]),
        ).model_dump()

    async def _execute(self, job: Job, db) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._execute_sync, job)

    def _execute_sync(self, job: Job) -> dict[str, Any]:
        total_started = time.monotonic()
        io_started = time.monotonic()
        params = AudioStemSeparationParams.model_validate(job_params_from_job(job))
        input_audio = _read_input_audio(params.input_audio, max_duration_seconds=params.max_duration_seconds)
        io_ms = int((time.monotonic() - io_started) * 1000)

        runner = _runner()
        separated = runner.separate(input_audio.data)

        output_target = output_target_from_job(job)
        stem_objects: dict[str, dict[str, str]] = {}
        write_started = time.monotonic()
        for stem in SOURCES:
            data = _wav_bytes(separated.stems[stem], sample_rate=input_audio.sample_rate)
            written = storage.write_bytes(
                bucket=output_target["oss_bucket"],
                region=output_target["oss_region"],
                key=_output_key(job, stem),
                data=data,
                content_type=AUDIO_WAV_CONTENT_TYPE,
                content_disposition=_attachment_content_disposition(job, stem),
            )
            stem_objects[stem] = oss_url_ref_from_output_object(
                bucket=str(written["oss_bucket"]),
                region=str(written["oss_region"]),
                key=str(written["oss_key"]),
                content_type=AUDIO_WAV_CONTENT_TYPE,
                content_hash=str(written["content_hash"]),
                public_endpoint=settings.storage.oss_public_endpoint or None,
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
