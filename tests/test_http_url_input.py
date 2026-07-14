import urllib.error
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.jobs.payload_adapters.http_url_input import _NoRedirectHandler, read_http_url_bytes


class _Response:
    status = 200

    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int | None = None) -> bytes:
        if size is None:
            return self.data
        return self.data[:size]


def test_read_http_url_bytes_reads_with_size_limit(monkeypatch):
    recorded = {}

    class _Opener:
        def open(self, request, *, timeout):
            recorded["url"] = request.full_url
            recorded["timeout"] = timeout
            return _Response(b"abc")

    monkeypatch.setattr("app.jobs.payload_adapters.http_url_input.urllib.request.build_opener", lambda *_args: _Opener())

    assert read_http_url_bytes("https://bucket.oss-region.aliyuncs.com/key.png", timeout_seconds=3, max_bytes=3) == b"abc"
    assert recorded == {"url": "https://bucket.oss-region.aliyuncs.com/key.png", "timeout": 3}


def test_read_http_url_bytes_rejects_oversized_response(monkeypatch):
    class _Opener:
        def open(self, _request, *, timeout):
            return _Response(b"abcd")

    monkeypatch.setattr("app.jobs.payload_adapters.http_url_input.urllib.request.build_opener", lambda *_args: _Opener())

    with pytest.raises(AppError) as exc:
        read_http_url_bytes("https://bucket.oss-region.aliyuncs.com/key.png", max_bytes=3)

    assert exc.value.code == "INPUT_TOO_LARGE"
    assert exc.value.details == {"max_bytes": 3, "size_bytes": 4}


@pytest.mark.parametrize("status", [403, 404, 500])
def test_read_http_url_bytes_maps_http_errors_to_invalid_input(monkeypatch, status):
    class _Opener:
        def open(self, _request, *, timeout):
            raise urllib.error.HTTPError(
                "https://bucket.oss-region.aliyuncs.com/key.png",
                status,
                "HTTP Error",
                {},
                None,
            )

    monkeypatch.setattr("app.jobs.payload_adapters.http_url_input.urllib.request.build_opener", lambda *_args: _Opener())

    with pytest.raises(AppError) as exc:
        read_http_url_bytes("https://bucket.oss-region.aliyuncs.com/key.png")

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.details == {}


def test_read_http_url_bytes_maps_network_errors_to_invalid_input(monkeypatch):
    class _Opener:
        def open(self, _request, *, timeout):
            raise urllib.error.URLError("timed out")

    monkeypatch.setattr("app.jobs.payload_adapters.http_url_input.urllib.request.build_opener", lambda *_args: _Opener())

    with pytest.raises(AppError) as exc:
        read_http_url_bytes("https://bucket.oss-region.aliyuncs.com/key.png")

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.details == {}


def test_read_http_url_bytes_does_not_follow_redirects():
    handler = _NoRedirectHandler()

    with pytest.raises(AppError) as exc:
        handler.redirect_request(
            SimpleNamespace(),
            None,
            302,
            "Found",
            {},
            "https://other.example.com/key.png",
        )

    assert exc.value.code == "INVALID_INPUT"
