from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import ipaddress
import socket
from typing import Any
from urllib.parse import SplitResult, unquote, urlsplit
import urllib.error
import urllib.request

from .exceptions import ObjectStorageBackendError, ObjectStorageValidationError
from .models import PublicUrlReadSpec, bare_sha256, sha256_digest


@dataclass(frozen=True)
class PublicUrlConfig:
    allowed_hosts: tuple[str, ...]
    timeout_seconds: float = 20
    max_bytes_ceiling: int | None = None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ObjectStorageBackendError("public_url redirects are not allowed")


class PublicUrlInputReader(ABC):
    @abstractmethod
    def read_public_url(self, spec: PublicUrlReadSpec) -> bytes: ...


class PublicUrlReader(PublicUrlInputReader):
    def __init__(self, config: PublicUrlConfig):
        if not isinstance(config, PublicUrlConfig):
            raise ObjectStorageValidationError("config must be PublicUrlConfig")
        if not config.allowed_hosts:
            raise ObjectStorageValidationError("allowed_hosts is required")
        if config.timeout_seconds <= 0:
            raise ObjectStorageValidationError("timeout_seconds must be greater than 0")
        if config.max_bytes_ceiling is not None:
            _positive_int(config.max_bytes_ceiling, "max_bytes_ceiling")
        self.config = config
        self._allowed_hosts = frozenset(_allowed_host(host) for host in config.allowed_hosts)

    def read_public_url(self, spec: PublicUrlReadSpec) -> bytes:
        if not isinstance(spec, PublicUrlReadSpec):
            raise ObjectStorageValidationError("spec must be PublicUrlReadSpec")
        return self._get_bytes(
            spec.url,
            expected_sha256=spec.integrity.sha256 if spec.policy.verify_sha256 else None,
            expected_size_bytes=spec.integrity.size_bytes if spec.policy.verify_size_bytes else None,
            max_bytes=spec.policy.max_bytes,
        )

    def _get_bytes(
        self,
        url: str,
        *,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        parsed = self._validate_url(url)
        effective_max_bytes = _effective_max_bytes(self.config.max_bytes_ceiling, max_bytes)
        expected = bare_sha256(expected_sha256) if expected_sha256 is not None else None
        if expected_size_bytes is not None:
            _non_negative_int(expected_size_bytes, "size_bytes")
        request = urllib.request.Request(parsed.geturl(), method="GET")
        opener = urllib.request.build_opener(_NoRedirectHandler)
        try:
            with opener.open(request, timeout=self.config.timeout_seconds) as response:
                _precheck_content_length(
                    response.headers.get("Content-Length"),
                    expected_size_bytes=expected_size_bytes,
                    max_bytes=effective_max_bytes,
                )
                data = response.read((effective_max_bytes + 1) if effective_max_bytes is not None else -1)
        except urllib.error.HTTPError as exc:
            raise ObjectStorageBackendError("failed to read public_url") from exc
        except urllib.error.URLError as exc:
            raise ObjectStorageBackendError("failed to read public_url") from exc

        if effective_max_bytes is not None and len(data) > effective_max_bytes:
            raise ObjectStorageValidationError(f"public_url input exceeds max_bytes={effective_max_bytes}")
        if expected_size_bytes is not None and len(data) != expected_size_bytes:
            raise ObjectStorageValidationError(
                f"public_url size_bytes mismatch: expected {expected_size_bytes}, got {len(data)}"
            )
        if expected is not None and sha256_digest(data) != expected:
            raise ObjectStorageValidationError("public_url sha256 mismatch")
        return data

    def _validate_url(self, url: str) -> SplitResult:
        if not isinstance(url, str) or not url.strip():
            raise ObjectStorageValidationError("public_url must be a non-empty string")
        parsed = urlsplit(url.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise ObjectStorageValidationError("public_url must be an absolute HTTPS URL")
        if parsed.username or parsed.password or _port(parsed) is not None:
            raise ObjectStorageValidationError("public_url must not contain credentials or port")
        if parsed.query or parsed.fragment:
            raise ObjectStorageValidationError("public_url must not contain query string or fragment")
        if parsed.hostname.lower() not in self._allowed_hosts:
            raise ObjectStorageValidationError("public_url host is not allowed")
        _validate_path(parsed.path)
        _reject_private_host(parsed.hostname)
        return parsed


def _validate_path(value: str) -> None:
    decoded = unquote(value)
    if "\\" in decoded or any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded):
        raise ObjectStorageValidationError("public_url path contains an invalid character")
    parts = decoded.lstrip("/").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ObjectStorageValidationError("public_url path must identify one canonical object")


def _allowed_host(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ObjectStorageValidationError("allowed_hosts entries must be non-empty host strings")
    host = value.strip().lower()
    if any(char in host for char in ("/", "\\", "?", "#", "@", ":")):
        raise ObjectStorageValidationError("allowed_hosts entries must not include scheme, path, port, query, or fragment")
    parsed = urlsplit(f"https://{host}")
    if parsed.hostname != host:
        raise ObjectStorageValidationError("allowed_hosts entries must be valid hosts")
    return host


def _reject_private_host(host: str) -> None:
    try:
        _reject_private_ip(ipaddress.ip_address(host))
        return
    except ValueError:
        pass
    try:
        addresses = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ObjectStorageValidationError("public_url host is not resolvable") from exc
    for item in addresses:
        _reject_private_ip(ipaddress.ip_address(item[4][0]))


def _reject_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise ObjectStorageValidationError("public_url host resolves to a non-public address")


def _port(parsed: SplitResult) -> int | None:
    try:
        return parsed.port
    except ValueError as exc:
        raise ObjectStorageValidationError("public_url must not contain credentials or port") from exc


def _precheck_content_length(
    value: str | None,
    *,
    expected_size_bytes: int | None,
    max_bytes: int | None,
) -> None:
    if value is None:
        return
    try:
        size_bytes = int(value)
    except ValueError:
        return
    if size_bytes < 0:
        raise ObjectStorageValidationError("public_url Content-Length must be non-negative")
    if max_bytes is not None and size_bytes > max_bytes:
        raise ObjectStorageValidationError(f"public_url input exceeds max_bytes={max_bytes}")
    if expected_size_bytes is not None and size_bytes != expected_size_bytes:
        raise ObjectStorageValidationError(
            f"public_url size_bytes mismatch: expected {expected_size_bytes}, got {size_bytes}"
        )


def _effective_max_bytes(config_max_bytes: int | None, request_max_bytes: int | None) -> int | None:
    if request_max_bytes is not None:
        _positive_int(request_max_bytes, "max_bytes")
    values = [value for value in (config_max_bytes, request_max_bytes) if value is not None]
    if not values:
        return None
    return min(values)


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ObjectStorageValidationError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ObjectStorageValidationError(f"{field} must be a non-negative integer")
    return value
