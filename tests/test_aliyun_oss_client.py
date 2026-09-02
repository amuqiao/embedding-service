from io import BytesIO
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

from app.tools.providers.aliyun_oss import AliyunOSSClient, AliyunOSSConfig, AliyunOSSError


def test_aliyun_oss_client_get_object_applies_project_root(monkeypatch):
    client = AliyunOSSClient(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            project_root="project-a",
        )
    )
    calls = []

    def fake_request(method, object_key, **_kwargs):
        calls.append((method, object_key))
        return 200, b"data", {}

    monkeypatch.setattr(client, "_request", fake_request)

    assert client.get_object("inputs/reference.png") == b"data"
    assert client.get_object("project-a/inputs/reference.png") == b"data"

    assert calls == [
        ("GET", "project-a/inputs/reference.png"),
        ("GET", "project-a/inputs/reference.png"),
    ]


def test_aliyun_oss_client_signed_get_url_applies_project_root_and_hides_secret(monkeypatch):
    monkeypatch.setattr("app.tools.providers.aliyun_oss.time.time", lambda: 1000)
    client = AliyunOSSClient(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="access-key-id-value",
            access_key_secret="secret-value",
            project_root="project-a",
        )
    )

    url = client.signed_get_url("inputs/reference.png", expires_seconds=60)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "bucket-a.oss-cn-hangzhou.aliyuncs.com"
    assert parsed.path == "/project-a/inputs/reference.png"
    assert query["OSSAccessKeyId"] == ["access-key-id-value"]
    assert query["Expires"] == ["1060"]
    assert query["Signature"]
    assert "secret-value" not in url


def test_aliyun_oss_client_put_object_sends_content_disposition(monkeypatch):
    client = AliyunOSSClient(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            project_root="project-a",
        )
    )
    calls = []

    def fake_request(method, object_key, **kwargs):
        calls.append((method, object_key, kwargs))
        return 200, b"", {}

    monkeypatch.setattr(client, "_request", fake_request)

    client.put_object(
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


def test_aliyun_oss_client_explains_bucket_endpoint_mismatch(monkeypatch):
    client = AliyunOSSClient(
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

    monkeypatch.setattr("app.tools.providers.aliyun_oss.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(AliyunOSSError) as exc_info:
        client.put_object("key.png", b"png", content_type="image/png")

    message = str(exc_info.value)
    assert "OSS endpoint mismatch" in message
    assert "configured_endpoint=oss-ap-southeast-1.aliyuncs.com" in message
    assert "configured_region=ap-southeast-1" in message
    assert "recommended_endpoint=oss-us-west-1.aliyuncs.com" in message
    assert "Check OSS_REGION and OSS_ENDPOINT in the selected env file." in message


def test_aliyun_oss_client_signed_headers_include_content_disposition():
    client = AliyunOSSClient(
        AliyunOSSConfig(
            bucket="bucket-a",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
        )
    )

    headers = client._sign_headers(
        method="PUT",
        object_key="outputs/title-layer.png",
        content_type="image/png",
        content_disposition='attachment; filename="poster-title-job-item.png"',
    )

    assert headers["Content-Disposition"] == 'attachment; filename="poster-title-job-item.png"'
