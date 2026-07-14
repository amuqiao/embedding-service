from __future__ import annotations

import json
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from app.core.exceptions import AppError
from app.schemas.jobs import AudioDecodeNormalizeRequest

SUPPORTED_AUDIO_INPUT_CONTENT_TYPES = frozenset({"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3"})
_CONTENT_TYPE_SUFFIX = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
}
_FFPROBE_TIMEOUT_SECONDS = 60
_FFMPEG_TIMEOUT_SECONDS = 1800
_MAX_DECODE_DURATION_SECONDS = 3600.0


@dataclass(frozen=True)
class DecodedAudio:
    data: np.ndarray
    sample_rate: int
    channels: int
    duration_seconds: float


def decode_normalize_audio(
    request: AudioDecodeNormalizeRequest | dict,
) -> DecodedAudio:
    try:
        snapshot = AudioDecodeNormalizeRequest.model_validate(request)
    except ValidationError as exc:
        raise AppError("AUDIO_STEM_INPUT_INVALID", "audio decode request is invalid") from exc
    data = snapshot.data
    source_content_type = snapshot.decode.source_content_type
    target_sample_rate = snapshot.decode.target_sample_rate
    target_channels = snapshot.decode.target_channels
    max_duration_seconds = snapshot.max_duration_seconds or _MAX_DECODE_DURATION_SECONDS

    if not data:
        raise AppError("AUDIO_STEM_INPUT_INVALID", "audio input must not be empty")
    if source_content_type not in SUPPORTED_AUDIO_INPUT_CONTENT_TYPES:
        raise AppError(
            "AUDIO_STEM_INPUT_INVALID",
            "audio input content_type is not supported",
            details={"content_type": source_content_type, "supported": sorted(SUPPORTED_AUDIO_INPUT_CONTENT_TYPES)},
        )
    if target_sample_rate != 44100 or target_channels != 2:
        raise AppError(
            "AUDIO_STEM_INPUT_INVALID",
            "audio decode target must be 44100Hz stereo",
            details={"target_sample_rate": target_sample_rate, "target_channels": target_channels},
        )

    suffix = _CONTENT_TYPE_SUFFIX[source_content_type]
    with tempfile.TemporaryDirectory(prefix="audio-decode-") as work_dir:
        source_path = Path(work_dir) / f"input{suffix}"
        output_path = Path(work_dir) / "output.f32le"
        source_path.write_bytes(data)
        probed_duration_seconds = _probe_audio_duration(source_path)
        if probed_duration_seconds > max_duration_seconds:
            raise AppError(
                "AUDIO_STEM_DURATION_EXCEEDS_LIMIT",
                "audio stem input duration exceeds max_duration_seconds",
                details={"actual": probed_duration_seconds, "max_duration_seconds": max_duration_seconds},
            )
        max_output_bytes = _max_decoded_bytes(
            max_duration_seconds,
            target_sample_rate=target_sample_rate,
            target_channels=target_channels,
        )
        raw = _run_ffmpeg_decode(
            source_path,
            output_path=output_path,
            target_sample_rate=target_sample_rate,
            target_channels=target_channels,
            max_duration_seconds=max_duration_seconds,
            max_output_bytes=max_output_bytes,
        )

    sample_count = _sample_count(raw, target_channels=target_channels)
    if sample_count < 1:
        raise AppError("AUDIO_STEM_INPUT_INVALID", "audio input contains no decodable samples")
    duration_seconds = sample_count / target_sample_rate
    if max_duration_seconds is not None and duration_seconds > max_duration_seconds:
        raise AppError(
            "AUDIO_STEM_DURATION_EXCEEDS_LIMIT",
            "audio stem input duration exceeds max_duration_seconds",
            details={"actual": duration_seconds, "max_duration_seconds": max_duration_seconds},
        )

    audio = np.frombuffer(raw, dtype="<f4").reshape(sample_count, target_channels).T
    return DecodedAudio(
        data=audio.astype(np.float32, copy=True),
        sample_rate=target_sample_rate,
        channels=target_channels,
        duration_seconds=duration_seconds,
    )


def _probe_audio_duration(source_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(source_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=_FFPROBE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise AppError("AUDIO_STEM_RUNTIME_UNAVAILABLE", "ffprobe is required for audio duration probe") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppError("AUDIO_STEM_INPUT_INVALID", "audio duration probe timed out") from exc
    if proc.returncode != 0:
        raise AppError(
            "AUDIO_STEM_INPUT_INVALID",
            "audio input duration could not be probed",
            details={"ffprobe_returncode": proc.returncode},
        )
    try:
        payload = json.loads(proc.stdout.decode("utf-8"))
        duration_seconds = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AppError("AUDIO_STEM_INPUT_INVALID", "audio input duration is not available") from exc
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise AppError(
            "AUDIO_STEM_INPUT_INVALID",
            "audio input duration must be positive",
            details={"duration_seconds": duration_seconds},
        )
    return duration_seconds


def _max_decoded_bytes(duration_seconds: float, *, target_sample_rate: int, target_channels: int) -> int:
    return math.ceil(duration_seconds * target_sample_rate) * target_channels * 4


def _run_ffmpeg_decode(
    source_path: Path,
    *,
    output_path: Path,
    target_sample_rate: int,
    target_channels: int,
    max_duration_seconds: float,
    max_output_bytes: int,
) -> bytes:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-t",
        str(max_duration_seconds),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ar",
        str(target_sample_rate),
        "-ac",
        str(target_channels),
        str(output_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=_FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise AppError("AUDIO_STEM_RUNTIME_UNAVAILABLE", "ffmpeg is required for audio decode") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppError("AUDIO_STEM_INPUT_INVALID", "audio decode timed out") from exc
    if proc.returncode != 0:
        raise AppError(
            "AUDIO_STEM_INPUT_INVALID",
            "audio input could not be decoded",
            details={"ffmpeg_returncode": proc.returncode},
        )
    if not output_path.exists():
        raise AppError("AUDIO_STEM_INPUT_INVALID", "audio decode produced no output")
    raw = output_path.read_bytes()
    if len(raw) > max_output_bytes:
        raise AppError(
            "AUDIO_STEM_DURATION_EXCEEDS_LIMIT",
            "decoded audio exceeds max_duration_seconds",
            details={"size_bytes": len(raw), "max_size_bytes": max_output_bytes},
        )
    return raw


def _sample_count(raw: bytes, *, target_channels: int) -> int:
    frame_size = target_channels * 4
    if len(raw) % frame_size != 0:
        raise AppError(
            "AUDIO_STEM_INPUT_INVALID",
            "decoded audio byte length is invalid",
            details={"size_bytes": len(raw), "frame_size": frame_size},
        )
    return len(raw) // frame_size
