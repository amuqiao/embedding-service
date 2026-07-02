from pathlib import Path

import pytest

from app.core.exceptions import AppError
from app.integrations.object_storage.aliyun_url import parse_aliyun_oss_url
from app.integrations.object_storage import bare_sha256, normalize_content_hash, sha256_digest
from app.integrations.object_storage import CanonicalObjectRef
from app.integrations.storage import AliyunObjectStorage, LocalObjectStorage


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
        "content_hash": sha256_digest(b"binary-data"),
        "content_size_bytes": len(b"binary-data"),
    }
    assert storage.read_bytes(bucket="bucket", region="local", key="objects/image.bin") == b"binary-data"

    text_written = storage.write_text(bucket="bucket", region="local", key="objects/text.txt", content="hello")
    assert text_written["content_hash"] == sha256_digest(b"hello")
    assert storage.read_text(bucket="bucket", region="local", key="objects/text.txt") == "hello"


def test_local_object_storage_rejects_path_traversal(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path)

    with pytest.raises(AppError, match="path traversal"):
        storage.write_bytes(bucket="bucket", region="local", key="../escape.bin", data=b"x")

    with pytest.raises(AppError, match="path traversal"):
        storage.write_bytes(bucket="../escape", region="local", key="object.bin", data=b"x")


def test_content_hash_helpers_are_strict():
    assert sha256_digest(b"x") == f"sha256:{'2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881'}"
    assert bare_sha256("a" * 64) == "a" * 64
    assert bare_sha256(f"sha256:{'a' * 64}") == "a" * 64
    assert normalize_content_hash("a" * 64) == f"sha256:{'a' * 64}"
    with pytest.raises(AppError, match="64 lowercase hex"):
        bare_sha256("A" * 64)


def test_canonical_object_ref_normalizes_content_hash():
    ref = CanonicalObjectRef(provider="local", bucket="bucket", region="local", key="object.bin", content_hash="a" * 64)

    assert ref.content_hash == f"sha256:{'a' * 64}"


def test_aliyun_object_storage_keeps_text_compatibility_contract():
    class FakeClient:
        class Config:
            bucket = "bucket"
            region = "ap-southeast-1"

        config = Config()

        def __init__(self):
            self.put_calls = []

        def object_key(self, key):
            return f"project/{key.strip('/')}"

        def get_object(self, key):
            assert key == "project/result.txt"
            return "hello".encode("utf-8")

        def put_object(self, key, data, *, content_type, content_disposition=None):
            self.put_calls.append((key, data, content_type, content_disposition))
            return {}

    client = FakeClient()
    storage = AliyunObjectStorage(client)

    written = storage.write_text(bucket="bucket", region="ap-southeast-1", key="result.txt", content="hello")

    assert client.put_calls == [("result.txt", b"hello", "text/plain; charset=utf-8", None)]
    assert written == {
        "oss_bucket": "bucket",
        "oss_key": "project/result.txt",
        "oss_region": "ap-southeast-1",
        "content_hash": sha256_digest(b"hello"),
        "content_size_bytes": len(b"hello"),
    }
    assert storage.read_text(bucket="bucket", region="ap-southeast-1", key="project/result.txt") == "hello"


def test_aliyun_object_storage_forwards_content_disposition():
    class FakeClient:
        class Config:
            bucket = "bucket"
            region = "ap-southeast-1"

        config = Config()

        def __init__(self):
            self.put_calls = []

        def object_key(self, key):
            return key

        def put_object(self, key, data, *, content_type, content_disposition=None):
            self.put_calls.append(
                {
                    "key": key,
                    "data": data,
                    "content_type": content_type,
                    "content_disposition": content_disposition,
                }
            )
            return {}

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


def test_parse_aliyun_oss_url_extracts_object_identity():
    location = parse_aliyun_oss_url(
        "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/a%20b/title.png"
    )

    assert location.bucket == "cpp-rs-dev"
    assert location.region == "ap-southeast-1"
    assert location.key == "a b/title.png"
    assert location.internal is True
    assert location.object_identity == ("cpp-rs-dev", "ap-southeast-1", "a b/title.png")


def test_parse_aliyun_oss_url_rejects_unsafe_url_parts():
    for url in (
        "http://bucket.oss-ap-southeast-1.aliyuncs.com/key.png",
        "https://bucket.oss-ap-southeast-1.aliyuncs.com/key.png?token=secret",
        "https://bucket.oss-ap-southeast-1.aliyuncs.com/../key.png",
        "https://example.com/key.png",
    ):
        with pytest.raises(AppError):
            parse_aliyun_oss_url(url)
