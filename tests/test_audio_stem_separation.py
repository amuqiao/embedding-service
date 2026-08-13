import builtins
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from app.capabilities.media import audio_input as media_audio_input
from app.core.exceptions import AppError
from app.integrations.onnx_runtime import OnnxRuntimeIntegrationError, OnnxSessionRuntime
from app.integrations.object_storage import bare_sha256, sha256_digest
from app.jobs import registry as job_registry
from app.jobs.types import audio_stem_shared
from app.jobs.types import audio_stem_separation as audio_pkg
from app.jobs.types.audio_stem_separation import executor as audio_executor
from app.jobs.types.audio_stem_separation.errors import AUDIO_STEM_INPUT_INVALID
from app.jobs.types.audio_stem_separation.errors import AUDIO_STEM_RUNTIME_UNAVAILABLE
from app.jobs.types.register import register_all_job_types
from app.models.job import Job
from app.schemas.jobs import AudioStemSeparationParams
from app.services.job_runtime import build_runtime_snapshot, payload_hash, write_runtime_json


def _url_ref(key: str, data: bytes, *, content_type: str = "audio/wav") -> dict:
    return {
        "public_url": f"https://local-dev.oss-local.aliyuncs.com/{key}",
        "internal_url": f"https://local-dev.oss-local-internal.aliyuncs.com/{key}",
        "content_type": content_type,
        "sha256": bare_sha256(sha256_digest(data)),
    }


def test_wav_bytes_missing_soundfile_points_to_audio_separation_extra(monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name == "soundfile":
            raise ModuleNotFoundError("No module named 'soundfile'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(AppError, match="uv sync --extra audio-separation"):
        audio_stem_shared.wav_bytes(np.zeros((2, 4), dtype=np.float32), sample_rate=44100)


def _handler():
    register_all_job_types()
    return job_registry.get("audio_stem_separation")


def _media_input_plan(params: dict) -> dict:
    input_audio = params["input_audio"]
    return {
        "capability_ref": "media.audio_input:2",
        "tool_refs": ("object_storage_read:1", "audio_decode_normalize:1"),
        "source": {
            "provider": "aliyun_oss",
            "bucket": "local-dev",
            "region": "local",
            "key": "input.wav",
            "content_type": input_audio["content_type"],
            "content_hash": f"sha256:{input_audio['sha256']}",
        },
        "fetch": {
            "read_mode": "object_storage",
            "endpoint_key": "canonical_object_ref",
            "max_bytes": 5_242_880,
            "redirect_policy": "forbid",
        },
        "decode": {
            "source_content_type": input_audio["content_type"],
            "target_sample_rate": 44100,
            "target_channels": 2,
        },
        **({"max_duration_seconds": params["max_duration_seconds"]} if params.get("max_duration_seconds") else {}),
    }


def _job(*, params: dict, output_prefix: str = "outputs") -> Job:
    job_id = uuid.uuid4()
    return Job(
        id=job_id,
        caller_id="caller-1",
        client_request_id="client-audio-1",
        job_type="audio_stem_separation",
        status="running",
        job_params_ref=write_runtime_json(None, "job_params", params),
        job_params_hash=payload_hash(params),
        runtime_ref=write_runtime_json(
            None,
            "runtime",
            build_runtime_snapshot(
                job_type="audio_stem_separation",
                job_params_hash=payload_hash(params),
                runtime_fields={
                    "operation": "audio_stem_separation",
                    "media_input_plan": _media_input_plan(params),
                    "onnx_model_version": "test-model",
                    "execution_provider": "cpu",
                    "segment_seconds": 7.8,
                    "overlap_ratio": 0.25,
                },
                output_target={
                    "type": "oss_prefix",
                    "oss_bucket": "local-dev",
                    "oss_region": "local",
                    "oss_prefix": output_prefix,
                },
            ),
        ),
        created_at=datetime(2026, 7, 10, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 10, 10, 0, tzinfo=UTC),
    )


class FakeSettings:
    class Job:
        oss_input_max_bytes = 5_242_880
        audio_stem_separation = SimpleNamespace(
            allowed_oss_buckets=("local-dev",),
            allowed_oss_regions=("local",),
        )

    class Storage:
        oss_public_endpoint = ""
        oss_bucket = ""
        oss_region = ""

    job = Job()
    storage = Storage()


class FakeStorage:
    def __init__(self, input_data: bytes) -> None:
        self.input_data = input_data
        self.writes: list[dict] = []

    def read_bytes(self, *, bucket: str, region: str, key: str) -> bytes:
        assert (bucket, region, key) == ("local-dev", "local", "input.wav")
        return self.input_data

    def write_bytes(
        self,
        *,
        bucket: str,
        region: str,
        key: str,
        data: bytes,
        content_type: str,
        content_disposition: str | None = None,
    ) -> dict:
        self.writes.append(
            {
                "bucket": bucket,
                "region": region,
                "key": key,
                "data": data,
                "content_type": content_type,
                "content_disposition": content_disposition,
            }
        )
        return {
            "oss_bucket": bucket,
            "oss_region": region,
            "oss_key": key,
            "content_hash": sha256_digest(data),
        }


class FakeRunner:
    model_version = "test-model"

    def separate(self, mix: np.ndarray) -> audio_executor.SeparationOutput:
        assert mix.shape == (2, 4)
        stems = {
            stem: np.full_like(mix, index + 1, dtype=np.float32)
            for index, stem in enumerate(audio_executor.SOURCES)
        }
        return audio_executor.SeparationOutput(
            stems=stems,
            segment_count=1,
            execution_provider="CPUExecutionProvider",
            inference_ms=7,
        )


class FakeONNXSession:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, output_names, input_feed):
        self.calls += 1
        segment_samples = input_feed["mix"].shape[-1]
        raw = np.zeros((1, 4, 2, segment_samples), dtype=np.float32)
        for index in range(4):
            raw[0, index, :, :] = index + 1
        return [raw]


class FakeNode:
    name = "mix"
    type = "tensor(float)"
    shape = [1, 2, 8]


class FakeOutputNode:
    name = "stems"
    type = "tensor(float)"
    shape = [1, 4, 2, 8]


class FakeRuntimeSession:
    def get_inputs(self):
        return [FakeNode()]

    def get_outputs(self):
        return [FakeOutputNode()]

    def get_providers(self):
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]


def _runner_with_fake_sessions(*, segment_samples: int = 8, overlap_ratio: float = 0.25):
    runner = object.__new__(audio_executor.HTDemucsONNXRunner)
    runner.model_dir = None
    runner.asset = {
        "experts": {
            stem: {"target_row": index}
            for index, stem in enumerate(audio_executor.SOURCES)
        }
    }
    runner.sample_rate = 44100
    runner.channels = 2
    runner.segment_samples = segment_samples
    runner.overlap_ratio = overlap_ratio
    runner.model_version = "test-model"
    runner.providers = ["CPUExecutionProvider"]
    runner.execution_provider = "CPUExecutionProvider"
    runner.sessions = {stem: FakeONNXSession() for stem in audio_executor.SOURCES}
    return runner


def test_audio_stem_separation_params_contract_accepts_supported_audio_refs():
    ref = _url_ref("input.wav", b"audio")
    params = AudioStemSeparationParams.model_validate({"input_audio": ref})

    assert params.input_audio.content_type == "audio/wav"

    mp3 = _url_ref("input.mp3", b"audio", content_type="audio/mpeg")
    params = AudioStemSeparationParams.model_validate({"input_audio": mp3})
    assert params.input_audio.content_type == "audio/mpeg"

    invalid = _url_ref("input.bin", b"audio", content_type="application/octet-stream")
    with pytest.raises(Exception):
        AudioStemSeparationParams.model_validate({"input_audio": invalid})


def test_audio_stem_separation_normalizes_and_rejects_disallowed_input_ref(monkeypatch):
    monkeypatch.setattr(audio_executor, "settings", FakeSettings())
    monkeypatch.setattr(audio_stem_shared, "settings", FakeSettings())
    handler = _handler()
    ref = _url_ref("input.wav", b"audio")

    assert handler.normalize_job_params({"input_audio": ref, "max_duration_seconds": 10}) == {
        "input_audio": ref,
        "max_duration_seconds": 10.0,
    }

    blocked = _url_ref("input.wav", b"audio", content_type="audio/wav") | {
        "public_url": "https://other-bucket.oss-local.aliyuncs.com/input.wav",
        "internal_url": "https://other-bucket.oss-local-internal.aliyuncs.com/input.wav",
    }
    with pytest.raises(AppError) as exc_info:
        handler.normalize_job_params({"input_audio": blocked})
    assert exc_info.value.code == AUDIO_STEM_INPUT_INVALID


def test_audio_stem_separation_runtime_fields_reflect_model_asset(monkeypatch):
    monkeypatch.setattr(audio_stem_shared, "settings", FakeSettings())
    handler = _handler()
    ref = _url_ref("input.wav", b"audio")
    fields = handler.runtime_job_fields({"input_audio": ref})

    assert fields | {"media_input_plan": None} == {
        "operation": "audio_stem_separation",
        "media_input_plan": None,
        "onnx_model_version": "htdemucs-ft-onnx-fp32",
        "execution_provider": "cpu",
        "segment_seconds": 7.8,
        "overlap_ratio": 0.25,
    }
    assert fields["media_input_plan"]["capability_ref"] == "media.audio_input:2"
    assert fields["media_input_plan"]["source"]["content_hash"] == f"sha256:{ref['sha256']}"
    assert fields["media_input_plan"]["decode"] == {
        "source_content_type": "audio/wav",
        "target_sample_rate": 44100,
        "target_channels": 2,
    }


def test_audio_stem_separation_runner_uses_minimal_segments_and_preserves_edges():
    runner = _runner_with_fake_sessions(segment_samples=8, overlap_ratio=0.25)
    mix = np.zeros((2, 8), dtype=np.float32)

    result = runner.separate(mix)

    assert result.segment_count == 1
    assert {stem: session.calls for stem, session in runner.sessions.items()} == {
        "drums": 1,
        "bass": 1,
        "other": 1,
        "vocals": 1,
    }
    assert np.allclose(result.stems["drums"], 1)
    assert np.allclose(result.stems["bass"], 2)
    assert np.allclose(result.stems["other"], 3)
    assert np.allclose(result.stems["vocals"], 4)


def test_audio_stem_separation_runner_does_not_add_extra_tail_segment():
    runner = _runner_with_fake_sessions(segment_samples=8, overlap_ratio=0.25)
    stride = 6
    mix = np.zeros((2, 8 + stride), dtype=np.float32)

    result = runner.separate(mix)

    assert result.segment_count == 2
    assert {stem: session.calls for stem, session in runner.sessions.items()} == {
        "drums": 2,
        "bass": 2,
        "other": 2,
        "vocals": 2,
    }
    assert np.allclose(result.stems["vocals"], 4)


def test_audio_stem_separation_runner_uses_configured_execution_provider_mode(tmp_path, monkeypatch):
    calls = []
    asset = {
        "model": {"version": "test-model"},
        "runtime": {
            "sample_rate": 44100,
            "channels": 2,
            "segment_samples": 8,
            "overlap_ratio": 0.25,
            "input": {"name": "mix", "dtype": "float", "shape": [1, 2, 8]},
            "output": {"name": "stems", "dtype": "float", "shape": [1, 4, 2, 8]},
        },
        "experts": {
            stem: {"file": f"{stem}.onnx", "sha256": "sha", "target_row": index}
            for index, stem in enumerate(audio_executor.SOURCES)
        },
    }
    for stem in audio_executor.SOURCES:
        (tmp_path / f"{stem}.onnx").write_bytes(b"onnx")

    def fake_create_inference_session(path, *, execution_provider_mode):
        calls.append({"path": path.name, "execution_provider_mode": execution_provider_mode})
        return OnnxSessionRuntime(
            session=FakeRuntimeSession(),
            requested_providers=("CUDAExecutionProvider", "CPUExecutionProvider"),
            actual_providers=("CUDAExecutionProvider", "CPUExecutionProvider"),
            execution_provider="CUDAExecutionProvider",
        )

    monkeypatch.setattr(audio_executor, "_hash_file", lambda _path: "sha")
    monkeypatch.setattr(audio_executor, "create_inference_session", fake_create_inference_session)

    runner = audio_executor.HTDemucsONNXRunner(
        model_dir=tmp_path,
        asset=asset,
        execution_provider_mode="cuda",
    )

    assert runner.providers == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert runner.execution_provider == "CUDAExecutionProvider"
    assert [call["execution_provider_mode"] for call in calls] == ["cuda"] * 4
    assert {call["path"] for call in calls} == {f"{stem}.onnx" for stem in audio_executor.SOURCES}


def test_audio_stem_separation_runner_reports_runtime_unavailable(tmp_path, monkeypatch):
    asset = {
        "model": {"version": "test-model"},
        "runtime": {
            "sample_rate": 44100,
            "channels": 2,
            "segment_samples": 8,
            "overlap_ratio": 0.25,
            "input": {"name": "mix", "dtype": "float", "shape": [1, 2, 8]},
            "output": {"name": "stems", "dtype": "float", "shape": [1, 4, 2, 8]},
        },
        "experts": {
            stem: {"file": f"{stem}.onnx", "sha256": "sha", "target_row": index}
            for index, stem in enumerate(audio_executor.SOURCES)
        },
    }
    for stem in audio_executor.SOURCES:
        (tmp_path / f"{stem}.onnx").write_bytes(b"onnx")

    def fake_create_inference_session(_path, *, execution_provider_mode):
        assert execution_provider_mode == "cuda"
        raise OnnxRuntimeIntegrationError("CUDAExecutionProvider is not available")

    monkeypatch.setattr(audio_executor, "_hash_file", lambda _path: "sha")
    monkeypatch.setattr(audio_executor, "create_inference_session", fake_create_inference_session)

    with pytest.raises(AppError) as exc_info:
        audio_executor.HTDemucsONNXRunner(
            model_dir=tmp_path,
            asset=asset,
            execution_provider_mode="cuda",
        )

    assert exc_info.value.code == AUDIO_STEM_RUNTIME_UNAVAILABLE


@pytest.mark.asyncio
async def test_audio_stem_separation_executes_fake_runner_and_writes_four_stems(monkeypatch):
    input_data = b"fake wav bytes"
    fake_storage = FakeStorage(input_data)
    params = {"input_audio": _url_ref("input.wav", input_data)}
    job = _job(params=params)
    handler = audio_pkg.AudioStemSeparationJob()

    monkeypatch.setattr(audio_executor, "storage", fake_storage)
    monkeypatch.setattr(
        audio_executor,
        "prepare_audio_input",
        lambda _plan: media_audio_input.PreparedAudioInput(
            data=np.zeros((2, 4), dtype=np.float32),
            sample_rate=44100,
            duration_seconds=4 / 44100,
        ),
    )
    monkeypatch.setattr(audio_executor, "_runner", lambda: FakeRunner())
    monkeypatch.setattr(
        audio_executor,
        "_wav_bytes",
        lambda audio, *, sample_rate: f"{sample_rate}:{audio[0, 0]}".encode("utf-8"),
    )

    result = await handler.execute(job, object())

    assert result["job_type"] == "audio_stem_separation"
    assert result["source_duration_seconds"] == 4 / 44100
    assert result["segment_count"] == 1
    assert result["sample_rate"] == 44100
    assert result["channels"] == 2
    assert result["onnx_model_version"] == "test-model"
    assert result["execution_provider"] == "CPUExecutionProvider"
    assert set(result["stems"]) == set(audio_executor.SOURCES)
    assert all(
        write["key"].endswith(f"/{stem}.wav")
        for write, stem in zip(fake_storage.writes, audio_executor.SOURCES, strict=True)
    )
    assert [write["content_type"] for write in fake_storage.writes] == ["audio/wav"] * 4
    assert all(output["content_type"] == "audio/wav" for output in result["stems"].values())
    assert all(output["sha256"] for output in result["stems"].values())
