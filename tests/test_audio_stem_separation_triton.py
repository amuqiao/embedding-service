import uuid
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from app.business_packages.audio_stem_separation import audio_io as media_audio_input
from app.core.exceptions import AppError
from app.object_storage import bare_sha256, sha256_digest
from app.tools.providers.triton_audio_stem import TritonAudioStemConfig, TritonAudioStemClient, TritonAudioStemInferenceError
from app.jobs import registry as job_registry
from app.business_packages.audio_stem_separation import shared as audio_stem_shared
from app.business_packages.audio_stem_separation import executor as audio_executor
from app.business_packages.audio_stem_separation.errors import AUDIO_STEM_INPUT_INVALID
from app.business_packages.audio_stem_separation.errors import AUDIO_STEM_RUNTIME_UNAVAILABLE
from app.business_packages.audio_stem_separation import triton_executor
from app.business_packages.audio_stem_separation import triton_storage_adapter
from app.business_packages.audio_stem_separation.triton_executor import AudioStemSeparationTritonJob
from app.business_packages.audio_stem_separation.triton_storage_adapter import AudioStemSeparationTritonStorageAdapter
from app.business_packages.audio_stem_separation.triton_storage_policy import AudioStemSeparationTritonStoragePolicy
from app.business_packages.register import register_all_business_packages
from app.models.job import Job
from app.business_packages.audio_stem_separation.triton_schemas import (
    AudioStemSeparationTritonParams,
    AudioStemSeparationTritonResult,
)
from app.services.job_runtime import build_runtime_snapshot, output_target_from_job, payload_hash, write_runtime_json


def _url_ref(key: str, data: bytes, *, content_type: str = "audio/wav") -> dict:
    return {
        "public_url": f"https://local-dev.oss-local.aliyuncs.com/{key}",
        "internal_url": f"https://local-dev.oss-local-internal.aliyuncs.com/{key}",
        "content_type": content_type,
        "sha256": bare_sha256(sha256_digest(data)),
    }


def _handler():
    register_all_business_packages()
    return job_registry.get("audio_stem_separation_triton")


def test_triton_storage_adapter_uses_settings_oss_endpoint_for_aliyun_config():
    storage = SimpleNamespace(
        backend="aliyun_oss",
        local_object_storage_path="storage/objects",
        oss_public_endpoint="",
        oss_bucket="bucket-a",
        oss_region="cn-hangzhou",
        oss_access_key_id="id",
        oss_access_key_secret_value="secret",
        oss_project_root="project-a",
        oss_endpoint="oss-cn-hangzhou.aliyuncs.com",
        oss_endpoint_style="virtual_host",
        oss_scheme="https",
    )

    config = triton_storage_adapter._repository_config_from_settings(SimpleNamespace(storage=storage))

    assert config.provider == "aliyun_oss"
    assert config.options["endpoint"] == "oss-cn-hangzhou.aliyuncs.com"
    assert config.options["endpoint_style"] == "virtual_host"


def _media_input_plan(params: dict) -> dict:
    input_audio = params["input_audio"]
    return {
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
        client_request_id="client-audio-triton-1",
        job_type="audio_stem_separation_triton",
        status="running",
        job_params_ref=write_runtime_json(None, "job_params", params),
        job_params_hash=payload_hash(params),
        runtime_ref=write_runtime_json(
            None,
            "runtime",
            build_runtime_snapshot(
                job_type="audio_stem_separation_triton",
                job_params_hash=payload_hash(params),
                runtime_fields={
                    "operation": "audio_stem_separation_triton",
                    "media_input_plan": _media_input_plan(params),
                    "onnx_model_version": "test-model",
                    "model_service": "triton",
                    "triton_model_version": "1",
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
        audio_stem_triton = SimpleNamespace(
            url="localhost:8000",
            token_value="token",
            model_version="1",
            request_timeout_seconds=10,
        )

    class Storage:
        backend = "local"
        local_object_storage_path = "storage/objects"
        oss_public_endpoint = ""
        oss_bucket = ""
        oss_region = ""
        oss_access_key_id = ""
        oss_access_key_secret_value = ""
        oss_project_root = ""
        oss_endpoint = ""
        oss_endpoint_style = "virtual_host"
        oss_scheme = "https"

    job = Job()
    storage = Storage()


class FakeStorage:
    def __init__(self) -> None:
        self.writes: list[dict] = []

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
            "content_hash": f"sha256:{sha256_digest(data)}",
        }

    def write_stem(self, *, job: Job, stem: str, data: bytes, content_disposition: str) -> dict[str, str]:
        output_target = output_target_from_job(job)
        prefix = output_target["oss_prefix"].strip("/")
        key = f"audio-stem-separation-triton/{job.id}/{stem}.wav"
        if prefix:
            key = f"{prefix}/{key}"
        written = self.write_bytes(
            bucket=output_target["oss_bucket"],
            region=output_target["oss_region"],
            key=key,
            data=data,
            content_type="audio/wav",
            content_disposition=content_disposition,
        )
        return {
            "public_url": f"https://{written['oss_bucket']}.oss-{written['oss_region']}.aliyuncs.com/{written['oss_key']}",
            "internal_url": (
                f"https://{written['oss_bucket']}.oss-{written['oss_region']}-internal.aliyuncs.com/"
                f"{written['oss_key']}"
            ),
            "content_type": "audio/wav",
            "sha256": bare_sha256(written["content_hash"]),
        }


def _local_adapter_settings(root, *, bucket: str = "settings-bucket", region: str = "settings-region") -> SimpleNamespace:
    return SimpleNamespace(
        job=FakeSettings.Job(),
        storage=SimpleNamespace(
            backend="local",
            local_object_storage_path=root,
            oss_public_endpoint="",
            oss_bucket=bucket,
            oss_region=region,
            oss_access_key_id="",
            oss_access_key_secret_value="",
            oss_project_root="",
            oss_endpoint="",
            oss_endpoint_style="virtual_host",
            oss_scheme="https",
        ),
    )


class FakeTritonClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def infer_stems(self, *, model_name: str, model_input: np.ndarray) -> np.ndarray:
        self.calls.append({"model_name": model_name, "shape": model_input.shape})
        if self.fail:
            raise TritonAudioStemInferenceError("remote unavailable")
        raw = np.zeros((1, 4, 2, model_input.shape[-1]), dtype=np.float32)
        for index in range(4):
            raw[0, index, :, :] = index + 1
        return raw


class FakeRunner:
    model_version = "test-model"

    def separate(self, mix: np.ndarray) -> triton_executor.TritonSeparationOutput:
        assert mix.shape == (2, 4)
        stems = {
            stem: np.full_like(mix, index + 1, dtype=np.float32)
            for index, stem in enumerate(audio_executor.SOURCES)
        }
        return triton_executor.TritonSeparationOutput(stems=stems, segment_count=1, inference_ms=9)


def _asset(*, segment_samples: int = 8, overlap_ratio: float = 0.25) -> dict:
    return {
        "model": {"version": "test-model"},
        "runtime": {
            "sample_rate": 44100,
            "channels": 2,
            "segment_samples": segment_samples,
            "segment_seconds": segment_samples / 44100,
            "overlap_ratio": overlap_ratio,
        },
        "experts": {
            stem: {"target_row": index}
            for index, stem in enumerate(audio_executor.SOURCES)
        },
    }


def test_audio_stem_separation_triton_params_contract_accepts_supported_audio_refs():
    ref = _url_ref("input.wav", b"audio")
    params = AudioStemSeparationTritonParams.model_validate({"input_audio": ref})

    assert params.input_audio.content_type == "audio/wav"

    mp3 = _url_ref("input.mp3", b"audio", content_type="audio/mpeg")
    params = AudioStemSeparationTritonParams.model_validate({"input_audio": mp3})
    assert params.input_audio.content_type == "audio/mpeg"

    invalid = _url_ref("input.bin", b"audio", content_type="application/octet-stream")
    with pytest.raises(Exception):
        AudioStemSeparationTritonParams.model_validate({"input_audio": invalid})


def test_audio_stem_separation_triton_result_uses_distinct_job_type():
    result = AudioStemSeparationTritonResult.model_validate(
        {
            "stems": {
                stem: {
                    "public_url": f"https://local-dev.oss-local.aliyuncs.com/{stem}.wav",
                    "internal_url": f"https://local-dev.oss-local-internal.aliyuncs.com/{stem}.wav",
                    "content_type": "audio/wav",
                    "sha256": "a" * 64,
                }
                for stem in audio_executor.SOURCES
            },
            "source_duration_seconds": 1,
            "segment_count": 1,
            "onnx_model_version": "test-model",
            "triton_model_version": "1",
            "duration_ms": {"io": 1, "inference": 2, "total": 3},
        }
    )

    assert result.job_type == "audio_stem_separation_triton"
    assert result.model_service == "triton"


def test_triton_audio_stem_client_uses_http_contract_and_auth_header():
    class FakeResult:
        def __init__(self, output: np.ndarray) -> None:
            self.output = output

        def as_numpy(self, name: str) -> np.ndarray:
            assert name == "stems"
            return self.output

    class FakeInferenceServerClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.calls = []
            FakeHttpClientModule.client = self

        def infer(self, **kwargs):
            self.calls.append(kwargs)
            return FakeResult(np.zeros((1, 4, 2, 8), dtype=np.float32))

    class FakeInferInput:
        def __init__(self, name: str, shape: tuple[int, ...], dtype: str) -> None:
            self.name = name
            self.shape = shape
            self.dtype = dtype
            self.data = None

        def set_data_from_numpy(self, value: np.ndarray) -> None:
            self.data = value

    class FakeInferRequestedOutput:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeHttpClientModule:
        client = None
        InferenceServerClient = FakeInferenceServerClient
        InferInput = FakeInferInput
        InferRequestedOutput = FakeInferRequestedOutput

    config = TritonAudioStemConfig(
        url="service.cn-hangzhou.pai-eas.aliyuncs.com/api/predict/audio_stem_triton",
        token="token",
        model_version="1",
        request_timeout_seconds=12,
    )
    client = TritonAudioStemClient(config, httpclient_module=FakeHttpClientModule)
    model_input = np.zeros((1, 2, 8), dtype=np.float32)

    output = client.infer_stems(model_name="htdemucs_ft_drums", model_input=model_input)

    assert output.shape == (1, 4, 2, 8)
    fake_client = FakeHttpClientModule.client
    assert fake_client.kwargs == {
        "url": "service.cn-hangzhou.pai-eas.aliyuncs.com/api/predict/audio_stem_triton",
        "connection_timeout": 12,
        "network_timeout": 12,
    }
    call = fake_client.calls[0]
    assert call["model_name"] == "htdemucs_ft_drums"
    assert call["model_version"] == "1"
    assert call["headers"] == {"Authorization": "token"}
    assert call["timeout"] == 12_000_000
    assert call["inputs"][0].name == "mix"
    assert call["inputs"][0].shape == (1, 2, 8)
    assert call["inputs"][0].dtype == "FP32"
    assert call["inputs"][0].data is model_input
    assert call["outputs"][0].name == "stems"


def test_audio_stem_separation_triton_normalizes_and_rejects_disallowed_input_ref(monkeypatch):
    fake_settings = FakeSettings()
    monkeypatch.setattr(audio_executor, "settings", fake_settings)
    monkeypatch.setattr(triton_executor, "settings", fake_settings)
    handler = _handler()
    ref = _url_ref("input.wav", b"audio")

    assert handler.normalize_job_params({"input_audio": ref, "max_duration_seconds": 10}) == {
        "input_audio": ref,
        "max_duration_seconds": 10.0,
    }

    blocked = _url_ref("input.wav", b"audio") | {
        "public_url": "https://other-bucket.oss-local.aliyuncs.com/input.wav",
        "internal_url": "https://other-bucket.oss-local-internal.aliyuncs.com/input.wav",
    }
    with pytest.raises(AppError) as exc_info:
        handler.normalize_job_params({"input_audio": blocked})
    assert exc_info.value.code == AUDIO_STEM_INPUT_INVALID


def test_audio_stem_separation_triton_runtime_fields_reflect_model_asset(monkeypatch):
    monkeypatch.setattr(triton_executor, "settings", FakeSettings())
    handler = _handler()
    ref = _url_ref("input.wav", b"audio")
    fields = handler.runtime_job_fields({"input_audio": ref})

    assert fields | {"media_input_plan": None} == {
        "operation": "audio_stem_separation_triton",
        "media_input_plan": None,
        "onnx_model_version": "htdemucs-ft-onnx-fp32",
        "model_service": "triton",
        "triton_model_version": "1",
        "segment_seconds": 7.8,
        "overlap_ratio": 0.25,
    }
    assert fields["media_input_plan"]["tool_refs"] == ("object_storage_read:1", "audio_decode_normalize:1")
    assert fields["media_input_plan"]["source"]["content_hash"] == f"sha256:{ref['sha256']}"
    assert fields["media_input_plan"]["decode"] == {
        "source_content_type": "audio/wav",
        "target_sample_rate": 44100,
        "target_channels": 2,
    }


def test_audio_stem_separation_triton_storage_adapter_writes_to_runtime_output_target(tmp_path):
    adapter = AudioStemSeparationTritonStorageAdapter.from_settings(_local_adapter_settings(tmp_path))
    params = {"input_audio": _url_ref("input.wav", b"audio")}
    job = _job(params=params)
    data = b"wav-bytes"

    result = adapter.write_stem(
        job=job,
        stem="vocals",
        data=data,
        content_disposition='attachment; filename="vocals.wav"',
    )

    expected_key = f"outputs/audio-stem-separation-triton/{job.id}/vocals.wav"
    assert (tmp_path / "local-dev" / expected_key).read_bytes() == data
    assert not (tmp_path / "settings-bucket" / expected_key).exists()
    assert result == {
        "public_url": f"https://local-dev.oss-local.aliyuncs.com/{expected_key}",
        "internal_url": f"https://local-dev.oss-local-internal.aliyuncs.com/{expected_key}",
        "content_type": "audio/wav",
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def test_audio_stem_separation_triton_storage_policy_overrides_output_namespace(tmp_path):
    adapter = AudioStemSeparationTritonStorageAdapter.from_settings(
        _local_adapter_settings(tmp_path),
        storage_policy=AudioStemSeparationTritonStoragePolicy(output_namespace="custom-triton-audio"),
    )
    params = {"input_audio": _url_ref("input.wav", b"audio")}
    job = _job(params=params)
    data = b"wav-bytes"

    adapter.write_stem(
        job=job,
        stem="vocals",
        data=data,
        content_disposition='attachment; filename="vocals.wav"',
    )

    expected_key = f"outputs/custom-triton-audio/{job.id}/vocals.wav"
    assert (tmp_path / "local-dev" / expected_key).read_bytes() == data


def test_audio_stem_separation_triton_runner_calls_each_remote_model_and_preserves_edges():
    client = FakeTritonClient()
    runner = triton_executor.HTDemucsTritonRunner(asset=_asset(segment_samples=8), client=client)
    mix = np.zeros((2, 8), dtype=np.float32)

    result = runner.separate(mix)

    assert result.segment_count == 1
    assert [call["model_name"] for call in client.calls] == [
        "htdemucs_ft_drums",
        "htdemucs_ft_bass",
        "htdemucs_ft_other",
        "htdemucs_ft_vocals",
    ]
    assert np.allclose(result.stems["drums"], 1)
    assert np.allclose(result.stems["bass"], 2)
    assert np.allclose(result.stems["other"], 3)
    assert np.allclose(result.stems["vocals"], 4)


def test_audio_stem_separation_triton_runner_maps_remote_failure():
    runner = triton_executor.HTDemucsTritonRunner(asset=_asset(segment_samples=8), client=FakeTritonClient(fail=True))

    with pytest.raises(AppError) as exc_info:
        runner.separate(np.zeros((2, 8), dtype=np.float32))

    assert exc_info.value.code == "AUDIO_STEM_INFERENCE_FAILED"


def test_audio_stem_separation_triton_runner_requires_configured_endpoint(monkeypatch):
    class EmptyUrlSettings(FakeSettings):
        class Job(FakeSettings.Job):
            audio_stem_triton = SimpleNamespace(
                url="",
                token_value="token",
                model_version="1",
                request_timeout_seconds=10,
            )

        job = Job()

    monkeypatch.setattr(triton_executor, "settings", EmptyUrlSettings())
    triton_executor.clear_triton_runner_cache_for_tests()

    with pytest.raises(AppError) as exc_info:
        triton_executor._runner()

    assert exc_info.value.code == AUDIO_STEM_RUNTIME_UNAVAILABLE


def test_audio_stem_separation_triton_contract_matches_external_triton_repository():
    external_root = Path("/Users/admin/Code/audio-stem-separation-triton")
    manifest_path = external_root / "manifests/htdemucs-ft.yaml"
    if not manifest_path.exists():
        pytest.skip("external audio-stem-separation-triton repository is not present")

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["runtime"]["input"] == {
        "name": "mix",
        "dtype": "TYPE_FP32",
        "dims": [1, 2, 343980],
    }
    assert manifest["runtime"]["output"] == {
        "name": "stems",
        "dtype": "TYPE_FP32",
        "dims": [1, 4, 2, 343980],
    }

    asset = triton_executor._load_model_asset()
    assert asset["runtime"]["input"]["name"] == manifest["runtime"]["input"]["name"]
    assert asset["runtime"]["input"]["shape"] == manifest["runtime"]["input"]["dims"]
    assert asset["runtime"]["output"]["name"] == manifest["runtime"]["output"]["name"]
    assert asset["runtime"]["output"]["shape"] == manifest["runtime"]["output"]["dims"]

    for stem in audio_executor.SOURCES:
        spec = manifest["models"][stem]
        model_name = f"htdemucs_ft_{stem}"
        assert spec["triton_name"] == model_name
        assert spec["target_row"] == asset["experts"][stem]["target_row"]
        config = (external_root / "models" / model_name / "config.pbtxt").read_text(encoding="utf-8")
        assert f'name: "{model_name}"' in config
        assert 'platform: "onnxruntime_onnx"' in config
        assert "max_batch_size: 0" in config
        assert 'name: "mix"' in config
        assert "dims: [ 1, 2, 343980 ]" in config
        assert 'name: "stems"' in config
        assert "dims: [ 1, 4, 2, 343980 ]" in config
        assert "dynamic_batching" not in config


@pytest.mark.asyncio
async def test_audio_stem_separation_triton_executes_fake_runner_and_writes_four_stems(monkeypatch):
    fake_storage = FakeStorage()
    input_data = b"fake wav bytes"
    params = {"input_audio": _url_ref("input.wav", input_data)}
    job = _job(params=params)
    handler = AudioStemSeparationTritonJob()

    monkeypatch.setattr(triton_executor, "settings", FakeSettings())
    monkeypatch.setattr(triton_executor, "_storage_adapter", lambda: fake_storage)
    monkeypatch.setattr(
        triton_executor,
        "prepare_audio_input",
        lambda _plan: media_audio_input.PreparedAudioInput(
            data=np.zeros((2, 4), dtype=np.float32),
            sample_rate=44100,
            duration_seconds=4 / 44100,
        ),
    )
    monkeypatch.setattr(triton_executor, "_runner", lambda: FakeRunner())
    monkeypatch.setattr(
        triton_executor,
        "_wav_bytes",
        lambda audio, *, sample_rate: f"{sample_rate}:{audio[0, 0]}".encode("utf-8"),
    )

    result = await handler.execute(job, object())

    assert result["job_type"] == "audio_stem_separation_triton"
    assert result["source_duration_seconds"] == 4 / 44100
    assert result["segment_count"] == 1
    assert result["sample_rate"] == 44100
    assert result["channels"] == 2
    assert result["onnx_model_version"] == "test-model"
    assert result["model_service"] == "triton"
    assert result["triton_model_version"] == "1"
    assert set(result["stems"]) == set(audio_executor.SOURCES)
    assert all(
        write["key"].endswith(f"/{stem}.wav")
        for write, stem in zip(fake_storage.writes, audio_executor.SOURCES, strict=True)
    )
    assert all("/audio-stem-separation-triton/" in write["key"] for write in fake_storage.writes)
    assert [write["content_type"] for write in fake_storage.writes] == ["audio/wav"] * 4
