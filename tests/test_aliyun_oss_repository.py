from io import BytesIO
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

from app.object_storage import (
    AliyunOSSConfig,
    AliyunOSSError,
    AliyunOSSRepository,
    ObjectRef,
    ObjectStorageValidationError,
    parse_aliyun_oss_access_url,
    parse_aliyun_oss_url,
    redact_aliyun_oss_url,
    sha256_digest,
    validate_aliyun_oss_access_url,
)


def test_aliyun_oss_repository_put_bytes_applies_key_prefix(monkeypatch):
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            key_prefix="project-a",
        )
    )
    calls = []

    def fake_request(method, object_key, **kwargs):
        calls.append((method, object_key, kwargs))
        return 200, b"", {}

    monkeypatch.setattr(repository, "_request", fake_request)

    result = repository.put_bytes("inputs/reference.png", b"data", content_type="image/png")

    assert result.key == "project-a/inputs/reference.png"
    assert calls == [
        (
            "PUT",
            "project-a/inputs/reference.png",
            {"data": b"data", "content_type": "image/png", "content_disposition": None},
        )
    ]


def test_aliyun_oss_repository_get_bytes_uses_object_ref_key(monkeypatch):
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            key_prefix="project-a",
        )
    )
    calls = []

    def fake_request(method, object_key, **_kwargs):
        calls.append((method, object_key))
        return 200, b"data", {}

    monkeypatch.setattr(repository, "_request", fake_request)

    assert repository.get_bytes(
        ObjectRef(
            provider="aliyun_oss",
            bucket="bucket-a",
            region="cn-hangzhou",
            key="project-a/inputs/reference.png",
        )
    ) == b"data"

    assert calls == [("GET", "project-a/inputs/reference.png")]


def test_aliyun_oss_repository_delete_uses_object_ref_key(monkeypatch):
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            key_prefix="project-a",
        )
    )
    calls = []

    def fake_request(method, object_key, **_kwargs):
        calls.append((method, object_key))
        return 204, b"", {}

    monkeypatch.setattr(repository, "_request", fake_request)

    repository.delete(
        ObjectRef(
            provider="aliyun_oss",
            bucket="bucket-a",
            region="cn-hangzhou",
            key="project-a/inputs/reference.png",
        )
    )

    assert calls == [("DELETE", "project-a/inputs/reference.png")]


def test_aliyun_oss_repository_signed_get_url_applies_key_prefix_and_hides_secret(monkeypatch):
    monkeypatch.setattr("app.object_storage.providers.aliyun_oss.time.time", lambda: 1000)
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="access-key-id-value",
            access_key_secret="secret-value",
            key_prefix="project-a",
        )
    )

    url = repository.signed_get_url(
        ObjectRef(
            provider="aliyun_oss",
            bucket="bucket-a",
            region="cn-hangzhou",
            key="project-a/inputs/reference.png",
        ),
        expires_seconds=60,
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "bucket-a.oss-cn-hangzhou.aliyuncs.com"
    assert parsed.path == "/project-a/inputs/reference.png"
    assert query["OSSAccessKeyId"] == ["access-key-id-value"]
    assert query["Expires"] == ["1060"]
    assert query["Signature"]
    assert "secret-value" not in url


def test_aliyun_oss_repository_signed_put_url_includes_content_type(monkeypatch):
    monkeypatch.setattr("app.object_storage.providers.aliyun_oss.time.time", lambda: 1000)
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="access-key-id-value",
            access_key_secret="secret-value",
            key_prefix="project-a",
        )
    )

    url = repository.signed_put_url(
        ObjectRef(
            provider="aliyun_oss",
            bucket="bucket-a",
            region="cn-hangzhou",
            key="project-a/outputs/title-layer.png",
        ),
        expires_seconds=60,
        content_type="image/png",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "bucket-a.oss-cn-hangzhou.aliyuncs.com"
    assert parsed.path == "/project-a/outputs/title-layer.png"
    assert query["OSSAccessKeyId"] == ["access-key-id-value"]
    assert query["Expires"] == ["1060"]
    assert query["Signature"]
    assert "secret-value" not in url


def test_aliyun_oss_repository_public_url_uses_configured_public_base_url():
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            public_base_url="https://cdn.example.com/assets",
        )
    )

    url = repository.public_url(
        ObjectRef(
            provider="aliyun_oss",
            bucket="bucket-a",
            region="cn-hangzhou",
            key="outputs/title layer.png",
        )
    )

    assert url == "https://cdn.example.com/assets/outputs/title%20layer.png"


def test_parse_aliyun_oss_url_returns_object_location():
    location = parse_aliyun_oss_url(
        "https://bucket-a.oss-cn-hangzhou-internal.aliyuncs.com/project-a/inputs/reference.png"
    )

    assert location.bucket == "bucket-a"
    assert location.region == "cn-hangzhou"
    assert location.key == "project-a/inputs/reference.png"
    assert location.internal is True
    assert location.endpoint == "oss-cn-hangzhou-internal.aliyuncs.com"
    assert location.object_identity == ("bucket-a", "cn-hangzhou", "project-a/inputs/reference.png")
    assert location.to_ref() == ObjectRef(
        provider="aliyun_oss",
        bucket="bucket-a",
        region="cn-hangzhou",
        key="project-a/inputs/reference.png",
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png",
        "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png?token=secret",
        "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/../reference.png",
        "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/./reference.png",
        "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a//reference.png",
        "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/reference.png/",
        "https://example.com/project-a/reference.png",
    ],
)
def test_parse_aliyun_oss_url_rejects_invalid_identity(url):
    with pytest.raises(ObjectStorageValidationError):
        parse_aliyun_oss_url(url)


def test_parse_aliyun_oss_access_url_allows_signed_query_and_redacts_url():
    access_url = (
        "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/inputs/reference.png"
        "?OSSAccessKeyId=access-key-id&Expires=1060&Signature=secret-signature"
    )

    location = parse_aliyun_oss_access_url(access_url)

    assert location.object_identity == ("bucket-a", "cn-hangzhou", "project-a/inputs/reference.png")
    assert location.internal is False
    assert (
        redact_aliyun_oss_url(access_url)
        == "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/inputs/reference.png"
    )


def test_redact_aliyun_oss_url_rejects_credentials():
    with pytest.raises(ObjectStorageValidationError, match="credentials or port"):
        redact_aliyun_oss_url(
            "https://user:password@bucket-a.oss-cn-hangzhou.aliyuncs.com/key.png?Signature=secret"
        )


def test_redact_aliyun_oss_url_rejects_non_aliyun_access_url():
    with pytest.raises(ObjectStorageValidationError, match="Aliyun OSS virtual-host endpoint"):
        redact_aliyun_oss_url("https://example.com/key.png?Signature=secret")


def test_redact_aliyun_oss_url_rejects_fragment():
    with pytest.raises(ObjectStorageValidationError, match="fragment"):
        redact_aliyun_oss_url("https://bucket-a.oss-cn-hangzhou.aliyuncs.com/key.png#secret")


def test_validate_aliyun_oss_access_url_matches_expected_object_ref():
    access_url = (
        "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/inputs/reference.png"
        "?OSSAccessKeyId=access-key-id&Expires=1060&Signature=secret-signature"
    )
    expected_ref = ObjectRef(
        provider="aliyun_oss",
        bucket="bucket-a",
        region="cn-hangzhou",
        key="project-a/inputs/reference.png",
    )

    location = validate_aliyun_oss_access_url(access_url, expected_ref=expected_ref)

    assert location.to_ref() == expected_ref


def test_validate_aliyun_oss_access_url_rejects_mismatched_object_ref():
    access_url = (
        "https://bucket-a.oss-cn-hangzhou.aliyuncs.com/project-a/inputs/reference.png"
        "?OSSAccessKeyId=access-key-id&Expires=1060&Signature=secret-signature"
    )
    expected_ref = ObjectRef(
        provider="aliyun_oss",
        bucket="bucket-a",
        region="cn-hangzhou",
        key="project-a/inputs/other.png",
    )

    with pytest.raises(
        ObjectStorageValidationError,
        match="does not match expected object identity",
    ):
        validate_aliyun_oss_access_url(access_url, expected_ref=expected_ref)


def test_aliyun_oss_repository_put_bytes_sends_content_disposition(monkeypatch):
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            key_prefix="project-a",
        )
    )
    calls = []

    def fake_request(method, object_key, **kwargs):
        calls.append((method, object_key, kwargs))
        return 200, b"", {}

    monkeypatch.setattr(repository, "_request", fake_request)

    result = repository.put_bytes(
        "outputs/title-layer.png",
        b"png",
        content_type="image/png",
        content_disposition='attachment; filename="poster-title-job-item.png"',
    )

    assert calls == [
        (
            "PUT",
            "project-a/outputs/title-layer.png",
            {
                "data": b"png",
                "content_type": "image/png",
                "content_disposition": 'attachment; filename="poster-title-job-item.png"',
            },
        )
    ]
    assert result.key == "project-a/outputs/title-layer.png"
    assert result.sha256 == sha256_digest(b"png")


def test_aliyun_oss_repository_explains_bucket_endpoint_mismatch(monkeypatch):
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="aigc-datas",
            region="ap-southeast-1",
            access_key_id="id",
            access_key_secret="secret",
            endpoint="oss-ap-southeast-1.aliyuncs.com",
        )
    )
    error_body = b"""<?xml version="1.0" encoding="UTF-8"?>
<Error>
  <Code>AccessDenied</Code>
  <Message>The bucket you are attempting to access must be addressed using the specified endpoint.</Message>
  <Bucket>aigc-datas</Bucket>
  <Endpoint>oss-us-west-1.aliyuncs.com</Endpoint>
</Error>
"""

    def fake_urlopen(_req, timeout):
        raise urllib.error.HTTPError(
            url="https://aigc-datas.oss-ap-southeast-1.aliyuncs.com/key.png",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=BytesIO(error_body),
        )

    monkeypatch.setattr("app.object_storage.providers.aliyun_oss.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(AliyunOSSError) as exc_info:
        repository.put_bytes("key.png", b"png", content_type="image/png")

    message = str(exc_info.value)
    assert "OSS endpoint mismatch" in message
    assert "configured_endpoint=oss-ap-southeast-1.aliyuncs.com" in message
    assert "configured_region=ap-southeast-1" in message
    assert "recommended_endpoint=oss-us-west-1.aliyuncs.com" in message
    assert "Check OSS_REGION and OSS_ENDPOINT in the selected env file." in message


def test_aliyun_oss_repository_signed_headers_include_content_disposition():
    repository = AliyunOSSRepository(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
        )
    )

    headers = repository._sign_headers(
        method="PUT",
        object_key="outputs/title-layer.png",
        content_type="image/png",
        content_disposition='attachment; filename="poster-title-job-item.png"',
    )

    assert headers["Content-Disposition"] == 'attachment; filename="poster-title-job-item.png"'
