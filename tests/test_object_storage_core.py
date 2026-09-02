from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.object_storage import (
    ExpectedObjectIntegrity,
    ObjectStorageValidationError,
    bare_sha256,
    normalize_content_hash,
    sha256_digest,
)
from app.services import jobs as job_services


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


def test_platform_object_storage_rejects_aliyun_bucket_mismatch(monkeypatch):
    fake_settings = SimpleNamespace(
        storage=SimpleNamespace(
            backend="aliyun_oss",
            local_object_storage_path="storage/objects",
            oss_public_endpoint="",
            oss_bucket="configured-bucket",
            oss_region="ap-southeast-1",
            oss_access_key_id="access-key",
            oss_access_key_secret_value="secret-key",
            oss_project_root="",
            oss_endpoint="",
            oss_endpoint_style="virtual_host",
            oss_scheme="https",
        )
    )
    monkeypatch.setattr(job_services, "settings", fake_settings)

    with pytest.raises(AppError) as exc_info:
        job_services._repository_config(bucket="other-bucket", region="ap-southeast-1")

    assert exc_info.value.code == "OSS_BUCKET_NOT_CONFIGURED"
