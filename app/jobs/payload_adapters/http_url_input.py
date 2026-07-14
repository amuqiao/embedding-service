from __future__ import annotations

import urllib.error
import urllib.request

from app.core.exceptions import AppError

DEFAULT_HTTP_INPUT_TIMEOUT_SECONDS = 20


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AppError("INVALID_INPUT", "reference image public_url is not readable")


def read_http_url_bytes(
    url: str,
    *,
    timeout_seconds: int = DEFAULT_HTTP_INPUT_TIMEOUT_SECONDS,
    max_bytes: int | None = None,
) -> bytes:
    request = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            if max_bytes is None:
                return response.read()
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise AppError("INVALID_INPUT", "reference image public_url is not readable") from exc
    except urllib.error.URLError as exc:
        raise AppError("INVALID_INPUT", "reference image public_url is not readable") from exc

    if len(data) > max_bytes:
        raise AppError(
            "INPUT_TOO_LARGE",
            "HTTP input exceeds service limit",
            details={"max_bytes": max_bytes, "size_bytes": len(data)},
        )
    return data
