from __future__ import annotations

import ast
from pathlib import Path
import sys

import numpy as np
import pytest

from app.capabilities.media import audio_input
from app.core.exceptions import AppError
from app.integrations.object_storage import bare_sha256, sha256_digest
from app.schemas.jobs import AudioStemSeparationInputObject


ROOT = Path(__file__).resolve().parents[1]


def _url_ref(key: str, data: bytes, *, content_type: str = "audio/wav") -> dict:
    return {
        "public_url": f"https://local-dev.oss-local.aliyuncs.com/{key}",
        "internal_url": f"https://local-dev.oss-local-internal.aliyuncs.com/{key}",
        "content_type": content_type,
        "sha256": bare_sha256(sha256_digest(data)),
    }


class FakeSettings:
    class Job:
        oss_input_max_bytes = 5_242_880
        audio_stem_separation_allowed_oss_buckets = ("local-dev",)
        audio_stem_separation_allowed_oss_regions = ("local",)

    class Storage:
        oss_public_endpoint = ""
        oss_bucket = ""
        oss_region = ""

    job = Job()
    storage = Storage()


def test_audio_wav_input_plan_freezes_canonical_source_not_public_url(monkeypatch):
    monkeypatch.setattr(audio_input, "settings", FakeSettings())
    ref = AudioStemSeparationInputObject.model_validate(_url_ref("input.wav", b"audio"))

    plan = audio_input.build_audio_wav_input_plan(ref, max_duration_seconds=10)

    assert plan == {
        "capability_ref": "media.audio_input:1",
        "tool_refs": ("object_storage_read:1",),
        "source": {
            "provider": "aliyun_oss",
            "bucket": "local-dev",
            "region": "local",
            "key": "input.wav",
            "content_type": "audio/wav",
            "content_hash": f"sha256:{ref.sha256}",
        },
        "fetch": {
            "read_mode": "object_storage",
            "endpoint_key": "canonical_object_ref",
            "max_bytes": 5_242_880,
            "redirect_policy": "forbid",
        },
        "max_duration_seconds": 10.0,
    }
    assert "public_url" not in str(plan)
    assert "internal_url" not in str(plan)


def test_audio_wav_input_plan_rejects_disallowed_bucket(monkeypatch):
    monkeypatch.setattr(audio_input, "settings", FakeSettings())
    blocked = _url_ref("input.wav", b"audio") | {
        "public_url": "https://other-bucket.oss-local.aliyuncs.com/input.wav",
        "internal_url": "https://other-bucket.oss-local-internal.aliyuncs.com/input.wav",
    }
    ref = AudioStemSeparationInputObject.model_validate(blocked)

    with pytest.raises(AppError) as exc_info:
        audio_input.build_audio_wav_input_plan(ref, max_duration_seconds=None)

    assert exc_info.value.code == "AUDIO_STEM_INPUT_INVALID"


def test_prepare_audio_wav_input_reads_frozen_object_ref(monkeypatch):
    data = b"audio-bytes"
    captured: dict[str, object] = {}
    plan = {
        "capability_ref": "media.audio_input:1",
        "tool_refs": ("object_storage_read:1",),
        "source": {
            "provider": "aliyun_oss",
            "bucket": "local-dev",
            "region": "local",
            "key": "input.wav",
            "content_type": "audio/wav",
            "content_hash": sha256_digest(data),
        },
        "fetch": {
            "read_mode": "object_storage",
            "endpoint_key": "canonical_object_ref",
            "max_bytes": 1024,
            "redirect_policy": "forbid",
        },
    }

    def fake_read_object_bytes(*, bucket: str, region: str, key: str, max_bytes: int | None = None):
        captured.update({"bucket": bucket, "region": region, "key": key, "max_bytes": max_bytes})
        return data

    class FakeSoundFile:
        @staticmethod
        def read(_buffer, *, dtype: str, always_2d: bool):
            assert dtype == "float32"
            assert always_2d is True
            return np.zeros((4, 2), dtype=np.float32), 44100

    monkeypatch.setattr(audio_input, "read_object_bytes", fake_read_object_bytes)
    monkeypatch.setitem(sys.modules, "soundfile", FakeSoundFile)

    result = audio_input.prepare_audio_wav_input(plan)

    assert captured == {"bucket": "local-dev", "region": "local", "key": "input.wav", "max_bytes": 1024}
    assert result.data.shape == (2, 4)
    assert result.sample_rate == 44100
    assert result.duration_seconds == 4 / 44100


def test_prepare_audio_wav_input_rejects_hash_mismatch(monkeypatch):
    plan = {
        "capability_ref": "media.audio_input:1",
        "tool_refs": ("object_storage_read:1",),
        "source": {
            "provider": "aliyun_oss",
            "bucket": "local-dev",
            "region": "local",
            "key": "input.wav",
            "content_type": "audio/wav",
            "content_hash": f"sha256:{'0' * 64}",
        },
        "fetch": {
            "read_mode": "object_storage",
            "endpoint_key": "canonical_object_ref",
            "max_bytes": 1024,
            "redirect_policy": "forbid",
        },
    }
    monkeypatch.setattr(audio_input, "read_object_bytes", lambda **_kwargs: b"different")

    with pytest.raises(AppError) as exc_info:
        audio_input.prepare_audio_wav_input(plan)

    assert exc_info.value.code == "INPUT_HASH_MISMATCH"


def test_triton_audio_job_does_not_import_audio_executor_private_helpers():
    path = ROOT / "app/jobs/types/audio_stem_separation_triton/executor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "app.jobs.types.audio_stem_separation.executor" not in imported_modules
