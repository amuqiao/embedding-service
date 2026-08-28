from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from smoke.harness.errors import FlowError


def request_json(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FlowError(f"{method} {url} failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise FlowError(f"{method} {url} failed: {exc.reason}") from exc
    if not isinstance(data, dict):
        raise FlowError(f"{method} {url} returned non-object JSON")
    return data


def data_object(envelope: dict[str, Any], key: str) -> dict[str, Any]:
    if envelope.get("code") != "0":
        raise FlowError(f"unexpected response envelope: {envelope}")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise FlowError(f"response missing data object: {envelope}")
    value = data.get(key)
    if not isinstance(value, dict):
        raise FlowError(f"response missing data.{key}: {envelope}")
    return value
