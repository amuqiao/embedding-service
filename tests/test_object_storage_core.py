from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.object_storage import (
    ExpectedObjectIntegrity,
    ObjectStorageConfigError,
    ObjectStorageValidationError,
    PutObjectResult,
    bare_sha256,
    normalize_content_hash,
    sha256_digest,
)
from app.services import object_storage as platform_object_storage
from app.services.object_storage import AliyunObjectStorage, LocalObjectStorage
from app.tools.private import object_storage_read


def _content_hash(data: bytes) -> str:
    return f"sha256:{sha256_digest(data)}"


def test_local_object_storage_supports_bytes_and_legacy_text(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path)

    written = storage.write_bytes(
        bucket="bucket",
        region="local",
        key="objects/image.bin",
        data=b"binary-data",
        content_type="application/octet-stream",
    )

    assert written == {
        "oss_bucket": "bucket",
        "oss_key": "objects/image.bin",
        "oss_region": "local",
        "content_hash": _content_hash(b"binary-data"),
        "content_size_bytes": len(b"binary-data"),
    }
    assert storage.read_bytes(bucket="bucket", region="local", key="objects/image.bin") == b"binary-data"

    text_written = storage.write_text(bucket="bucket", region="local", key="objects/text.txt", content="hello")
    assert text_written["content_hash"] == _content_hash(b"hello")
    assert storage.read_text(bucket="bucket", region="local", key="objects/text.txt") == "hello"


def test_local_object_storage_rejects_path_traversal(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path)

    with pytest.raises(AppError) as key_exc:
        storage.write_bytes(bucket="bucket", region="local", key="../escape.bin", data=b"x")
    assert key_exc.value.code == "INVALID_INPUT"

    with pytest.raises(AppError) as bucket_exc:
        storage.write_bytes(bucket="../escape", region="local", key="object.bin", data=b"x")
    assert bucket_exc.value.code == "INVALID_INPUT"


def test_content_hash_helpers_are_strict():
    assert sha256_digest(b"x") == "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
    assert bare_sha256("a" * 64) == "a" * 64
    assert bare_sha256(f"sha256:{'a' * 64}") == "a" * 64
    assert normalize_content_hash("a" * 64) == "a" * 64
    with pytest.raises(ObjectStorageValidationError, match="64 lowercase hex"):
        bare_sha256("A" * 64)


def test_expected_object_integrity_normalizes_content_hash():
    ref = ExpectedObjectIntegrity(sha256=f"sha256:{'a' * 64}")

    assert ref.sha256 == "a" * 64


def test_aliyun_object_storage_keeps_text_compatibility_contract():
    class FakeClient:
        class Config:
            bucket = "bucket"
            region = "ap-southeast-1"

        provider = "aliyun_oss"
        config = Config()

        def __init__(self):
            self.put_calls = []

        def get_bytes(self, ref):
            assert ref.key == "project/result.txt"
            return "hello".encode("utf-8")

        def put_bytes(self, key, data, *, content_type, content_disposition=None):
            self.put_calls.append((key, data, content_type, content_disposition))
            return PutObjectResult(
                provider="aliyun_oss",
                bucket="bucket",
                region="ap-southeast-1",
                key=f"project/{key.strip('/')}",
                content_type=content_type,
                size_bytes=len(data),
                sha256=sha256_digest(data),
            )

    client = FakeClient()
    storage = AliyunObjectStorage(client)

    written = storage.write_text(bucket="bucket", region="ap-southeast-1", key="result.txt", content="hello")

    assert client.put_calls == [("result.txt", b"hello", "text/plain; charset=utf-8", None)]
    assert written == {
        "oss_bucket": "bucket",
        "oss_key": "project/result.txt",
        "oss_region": "ap-southeast-1",
        "content_hash": _content_hash(b"hello"),
        "content_size_bytes": len(b"hello"),
    }
    assert storage.read_text(bucket="bucket", region="ap-southeast-1", key="project/result.txt") == "hello"


def test_aliyun_object_storage_forwards_content_disposition():
    class FakeClient:
        class Config:
            bucket = "bucket"
            region = "ap-southeast-1"

        provider = "aliyun_oss"
        config = Config()

        def __init__(self):
            self.put_calls = []

        def put_bytes(self, key, data, *, content_type, content_disposition=None):
            self.put_calls.append(
                {
                    "key": key,
                    "data": data,
                    "content_type": content_type,
                    "content_disposition": content_disposition,
                }
            )
            return PutObjectResult(
                provider="aliyun_oss",
                bucket="bucket",
                region="ap-southeast-1",
                key=key,
                content_type=content_type,
                size_bytes=len(data),
                sha256=sha256_digest(data),
            )

    client = FakeClient()
    storage = AliyunObjectStorage(client)

    storage.write_bytes(
        bucket="bucket",
        region="ap-southeast-1",
        key="title-layer.png",
        data=b"png",
        content_type="image/png",
        content_disposition='attachment; filename="poster-title-job-item.png"',
    )

    assert client.put_calls == [
        {
            "key": "title-layer.png",
            "data": b"png",
            "content_type": "image/png",
            "content_disposition": 'attachment; filename="poster-title-job-item.png"',
        }
    ]


def test_runtime_validate_configuration_fails_fast_for_missing_aliyun_config(monkeypatch):
    fake_settings = SimpleNamespace(
        storage=SimpleNamespace(
            backend="aliyun_oss",
            oss_bucket="",
            oss_region="ap-southeast-1",
            oss_access_key_id="",
            oss_access_key_secret_value="",
            oss_project_root="",
            oss_endpoint="",
            oss_endpoint_style="virtual_host",
            oss_public_endpoint="",
            oss_scheme="https",
        )
    )
    monkeypatch.setattr(platform_object_storage, "settings", fake_settings)

    with pytest.raises(ObjectStorageConfigError, match="missing Aliyun OSS config"):
        platform_object_storage.validate_configuration()


def test_object_storage_read_validate_configuration_rejects_invalid_aliyun_config(monkeypatch):
    fake_settings = SimpleNamespace(
        storage=SimpleNamespace(
            backend="aliyun_oss",
            oss_bucket="bucket",
            oss_region="ap-southeast-1",
            oss_access_key_id="access-key",
            oss_access_key_secret_value="secret-key",
            oss_project_root="",
            oss_endpoint="",
            oss_endpoint_style="invalid-style",
            oss_public_endpoint="",
            oss_scheme="https",
        )
    )
    monkeypatch.setattr(object_storage_read, "settings", fake_settings)

    with pytest.raises(ObjectStorageConfigError, match="invalid Aliyun OSS config"):
        object_storage_read.validate_configuration()


def test_object_storage_read_maps_runtime_config_error(monkeypatch):
    fake_settings = SimpleNamespace(
        storage=SimpleNamespace(
            backend="aliyun_oss",
            oss_bucket="bucket",
            oss_region="ap-southeast-1",
            oss_access_key_id="access-key",
            oss_access_key_secret_value="secret-key",
            oss_project_root="",
            oss_endpoint="",
            oss_endpoint_style="invalid-style",
            oss_public_endpoint="",
            oss_scheme="https",
        )
    )
    monkeypatch.setattr(object_storage_read, "settings", fake_settings)

    with pytest.raises(AppError) as exc_info:
        object_storage_read.read_object_bytes(bucket="bucket", region="ap-southeast-1", key="input.wav")

    assert exc_info.value.code == "OSS_FETCH_FAILED"
