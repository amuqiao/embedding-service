from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.capabilities.media import audio_input
from app.tools import media_audio
from app.core.exceptions import AppError
from app.integrations.object_storage import bare_sha256, sha256_digest
from app.jobs.types import audio_stem_shared
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


def test_audio_input_plan_freezes_canonical_source_and_decode_policy(monkeypatch):
    monkeypatch.setattr(audio_stem_shared, "settings", FakeSettings())
    ref = AudioStemSeparationInputObject.model_validate(_url_ref("input.mp3", b"audio", content_type="audio/mpeg"))

    plan = audio_stem_shared.build_audio_input_plan(ref, max_duration_seconds=10)

    assert plan == {
        "capability_ref": "media.audio_input:2",
        "tool_refs": ("object_storage_read:1", "audio_decode_normalize:1"),
        "source": {
            "provider": "aliyun_oss",
            "bucket": "local-dev",
            "region": "local",
            "key": "input.mp3",
            "content_type": "audio/mpeg",
            "content_hash": f"sha256:{ref.sha256}",
        },
        "fetch": {
            "read_mode": "object_storage",
            "endpoint_key": "canonical_object_ref",
            "max_bytes": 5_242_880,
            "redirect_policy": "forbid",
        },
        "decode": {
            "source_content_type": "audio/mpeg",
            "target_sample_rate": 44100,
            "target_channels": 2,
        },
        "max_duration_seconds": 10.0,
    }
    assert "public_url" not in str(plan)
    assert "internal_url" not in str(plan)


def test_audio_input_plan_rejects_disallowed_bucket(monkeypatch):
    monkeypatch.setattr(audio_stem_shared, "settings", FakeSettings())
    blocked = _url_ref("input.wav", b"audio") | {
        "public_url": "https://other-bucket.oss-local.aliyuncs.com/input.wav",
        "internal_url": "https://other-bucket.oss-local-internal.aliyuncs.com/input.wav",
    }
    ref = AudioStemSeparationInputObject.model_validate(blocked)

    with pytest.raises(AppError) as exc_info:
        audio_stem_shared.build_audio_input_plan(ref, max_duration_seconds=None)

    assert exc_info.value.code == "AUDIO_STEM_INPUT_INVALID"


def test_prepare_audio_input_reads_frozen_object_ref_and_normalizes(monkeypatch):
    data = b"audio-bytes"
    captured: dict[str, object] = {}
    plan = {
        "capability_ref": "media.audio_input:2",
        "tool_refs": ("object_storage_read:1", "audio_decode_normalize:1"),
        "source": {
            "provider": "aliyun_oss",
            "bucket": "local-dev",
            "region": "local",
            "key": "input.mp3",
            "content_type": "audio/mpeg",
            "content_hash": sha256_digest(data),
        },
        "fetch": {
            "read_mode": "object_storage",
            "endpoint_key": "canonical_object_ref",
            "max_bytes": 1024,
            "redirect_policy": "forbid",
        },
        "decode": {
            "source_content_type": "audio/mpeg",
            "target_sample_rate": 44100,
            "target_channels": 2,
        },
        "max_duration_seconds": 10,
    }

    def fake_read_object_bytes(*, bucket: str, region: str, key: str, max_bytes: int | None = None):
        captured.update({"bucket": bucket, "region": region, "key": key, "max_bytes": max_bytes})
        return data

    def fake_decode_normalize_audio(
        request: dict,
    ):
        actual_data = request["data"]
        decode = request["decode"]
        assert actual_data == data
        assert decode["source_content_type"] == "audio/mpeg"
        assert decode["target_sample_rate"] == 44100
        assert decode["target_channels"] == 2
        assert request["max_duration_seconds"] == 10
        return media_audio.DecodedAudio(
            data=np.zeros((2, 4), dtype=np.float32),
            sample_rate=44100,
            channels=2,
            duration_seconds=4 / 44100,
        )

    monkeypatch.setattr(audio_input, "read_object_bytes", fake_read_object_bytes)
    monkeypatch.setattr(audio_input, "decode_normalize_audio", fake_decode_normalize_audio)

    result = audio_input.prepare_audio_input(plan)

    assert captured == {"bucket": "local-dev", "region": "local", "key": "input.mp3", "max_bytes": 1024}
    assert result.data.shape == (2, 4)
    assert result.sample_rate == 44100
    assert result.duration_seconds == 4 / 44100


def test_prepare_audio_input_rejects_decode_source_content_type_mismatch(monkeypatch):
    data = b"audio-bytes"
    plan = {
        "capability_ref": "media.audio_input:2",
        "tool_refs": ("object_storage_read:1", "audio_decode_normalize:1"),
        "source": {
            "provider": "aliyun_oss",
            "bucket": "local-dev",
            "region": "local",
            "key": "input.mp3",
            "content_type": "audio/mpeg",
            "content_hash": sha256_digest(data),
        },
        "fetch": {
            "read_mode": "object_storage",
            "endpoint_key": "canonical_object_ref",
            "max_bytes": 1024,
            "redirect_policy": "forbid",
        },
        "decode": {
            "source_content_type": "audio/wav",
            "target_sample_rate": 44100,
            "target_channels": 2,
        },
    }
    monkeypatch.setattr(audio_input, "read_object_bytes", lambda **_kwargs: data)

    with pytest.raises(AppError) as exc_info:
        audio_input.prepare_audio_input(plan)

    assert exc_info.value.code == "AUDIO_STEM_INPUT_INVALID"


def test_prepare_audio_input_rejects_hash_mismatch(monkeypatch):
    plan = {
        "capability_ref": "media.audio_input:2",
        "tool_refs": ("object_storage_read:1", "audio_decode_normalize:1"),
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
        "decode": {
            "source_content_type": "audio/wav",
            "target_sample_rate": 44100,
            "target_channels": 2,
        },
    }
    monkeypatch.setattr(audio_input, "read_object_bytes", lambda **_kwargs: b"different")

    with pytest.raises(AppError) as exc_info:
        audio_input.prepare_audio_input(plan)

    assert exc_info.value.code == "INPUT_HASH_MISMATCH"


def test_audio_decode_normalize_tool_emits_canonical_audio(monkeypatch):
    decoded = np.array(
        [
            [0.1, 0.2],
            [0.3, 0.4],
            [0.5, 0.6],
        ],
        dtype=np.float32,
    )

    def fake_run(cmd, *, capture_output: bool, check: bool, timeout: int):
        assert capture_output is True
        assert check is False
        assert timeout > 0
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout=b'{"format":{"duration":"0.000068"}}', stderr=b"")
        assert cmd[0] == "ffmpeg"
        assert "-ar" in cmd
        assert cmd[cmd.index("-ar") + 1] == "44100"
        assert "-ac" in cmd
        assert cmd[cmd.index("-ac") + 1] == "2"
        Path(cmd[-1]).write_bytes(decoded.astype("<f4").tobytes())
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(media_audio.subprocess, "run", fake_run)

    result = media_audio.decode_normalize_audio(
        {
            "data": b"mp3-bytes",
            "decode": {
                "source_content_type": "audio/mpeg",
                "target_sample_rate": 44100,
                "target_channels": 2,
            },
            "max_duration_seconds": 10,
        }
    )

    assert result.sample_rate == 44100
    assert result.channels == 2
    assert result.duration_seconds == 3 / 44100
    assert result.data.shape == (2, 3)
    np.testing.assert_allclose(result.data, decoded.T)


def test_audio_decode_normalize_tool_rejects_unsupported_content_type():
    with pytest.raises(AppError) as exc_info:
        media_audio.decode_normalize_audio(
            {
                "data": b"audio",
                "decode": {
                    "source_content_type": "application/octet-stream",
                    "target_sample_rate": 44100,
                    "target_channels": 2,
                },
            }
        )

    assert exc_info.value.code == "AUDIO_STEM_INPUT_INVALID"


def test_audio_decode_normalize_tool_rejects_duration_over_limit(monkeypatch):
    monkeypatch.setattr(
        media_audio.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b'{"format":{"duration":"0.000068"}}', stderr=b""),
    )

    with pytest.raises(AppError) as exc_info:
        media_audio.decode_normalize_audio(
            {
                "data": b"audio",
                "decode": {
                    "source_content_type": "audio/wav",
                    "target_sample_rate": 44100,
                    "target_channels": 2,
                },
                "max_duration_seconds": 1 / 44100,
            }
        )

    assert exc_info.value.code == "AUDIO_STEM_DURATION_EXCEEDS_LIMIT"


def test_audio_decode_normalize_tool_rejects_missing_probe_runtime(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(media_audio.subprocess, "run", fake_run)

    with pytest.raises(AppError) as exc_info:
        media_audio.decode_normalize_audio(
            {
                "data": b"audio",
                "decode": {
                    "source_content_type": "audio/wav",
                    "target_sample_rate": 44100,
                    "target_channels": 2,
                },
                "max_duration_seconds": 1,
            }
        )

    assert exc_info.value.code == "AUDIO_STEM_RUNTIME_UNAVAILABLE"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def test_audio_job_does_not_import_other_audio_executor_private_helpers():
    imported_modules = _imported_modules(ROOT / "app/jobs/types/audio_stem_separation_triton/executor.py")

    assert "app.jobs.types.audio_stem_separation.executor" not in imported_modules


def test_registry_layers_do_not_depend_on_callers():
    violations: list[str] = []
    rules = {
        ROOT / "app/capabilities": ("app.jobs", "app.integrations"),
        ROOT / "app/tools": ("app.jobs", "app.capabilities"),
        ROOT / "app/integrations": ("app.jobs", "app.capabilities", "app.tools"),
    }
    for directory, forbidden_prefixes in rules.items():
        for path in directory.rglob("*.py"):
            module_refs = _imported_modules(path)
            forbidden = sorted(
                ref
                for ref in module_refs
                if any(ref == prefix or ref.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
            )
            if forbidden:
                violations.append(f"{path.relative_to(ROOT)} imports {forbidden}")

    assert violations == []
