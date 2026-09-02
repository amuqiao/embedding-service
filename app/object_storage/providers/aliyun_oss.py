from __future__ import annotations

import base64
from dataclasses import dataclass
from email.utils import formatdate
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
import urllib.error
import urllib.request
from xml.etree import ElementTree

from ..exceptions import (
    ObjectStorageBackendError,
    ObjectStorageNotFoundError,
    ObjectStorageValidationError,
)
from ..models import (
    ObjectMeta,
    ObjectRef,
    PutObjectResult,
    join_key,
    normalize_name,
    normalize_object_key,
    sha256_digest,
)
from ..repository import ObjectStorageRepository


class AliyunOSSError(ObjectStorageBackendError):
    pass


@dataclass(frozen=True)
class AliyunOSSConfig:
    bucket: str
    region: str
    access_key_id: str
    access_key_secret: str
    key_prefix: str = ""
    endpoint: str = ""
    endpoint_style: str = "virtual_host"
    public_base_url: str = ""
    scheme: str = "https"
    timeout_seconds: float = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "bucket", normalize_name(self.bucket, "bucket"))
        object.__setattr__(self, "region", normalize_name(self.region, "region"))
        object.__setattr__(self, "access_key_id", _required_str(self.access_key_id, "access_key_id"))
        object.__setattr__(self, "access_key_secret", _required_str(self.access_key_secret, "access_key_secret"))
        object.__setattr__(self, "endpoint", _endpoint(self.endpoint))
        object.__setattr__(self, "endpoint_style", _endpoint_style(self.endpoint_style))
        object.__setattr__(self, "scheme", _scheme(self.scheme))
        object.__setattr__(self, "timeout_seconds", _positive_float(self.timeout_seconds, "timeout_seconds"))
        if self.key_prefix:
            object.__setattr__(self, "key_prefix", normalize_object_key(self.key_prefix))
        if self.public_base_url:
            _validate_public_base_url(self.public_base_url)

    @property
    def normalized_endpoint(self) -> str:
        return self.endpoint or f"oss-{self.region}.aliyuncs.com"


class AliyunOSSRepository(ObjectStorageRepository):
    provider = "aliyun_oss"

    def __init__(self, config: AliyunOSSConfig):
        if config.timeout_seconds <= 0:
            raise ObjectStorageValidationError("timeout_seconds must be greater than 0")
        self.config = config

    def get_bytes(self, ref: ObjectRef) -> bytes:
        self._assert_ref(ref)
        _, body, _ = self._request("GET", ref.key)
        return body

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        content_disposition: str | None = None,
    ) -> PutObjectResult:
        object_key = join_key(self.config.key_prefix, key)
        self._request(
            "PUT",
            object_key,
            data=data,
            content_type=content_type,
            content_disposition=content_disposition,
        )
        return PutObjectResult(
            provider=self.provider,
            bucket=self.config.bucket,
            region=self.config.region,
            key=object_key,
            content_type=content_type,
            size_bytes=len(data),
            sha256=sha256_digest(data),
            public_url=self._public_url(object_key),
        )

    def head(self, ref: ObjectRef) -> ObjectMeta:
        self._assert_ref(ref)
        _, _, headers = self._request("HEAD", ref.key)
        return ObjectMeta(
            provider=self.provider,
            bucket=self.config.bucket,
            region=self.config.region,
            key=ref.key,
            content_type=headers.get("Content-Type"),
            size_bytes=_optional_int(headers.get("Content-Length")),
            etag=headers.get("ETag"),
        )

    def delete(self, ref: ObjectRef) -> None:
        self._assert_ref(ref)
        self._request("DELETE", ref.key)

    def signed_get_url(self, ref: ObjectRef, *, expires_seconds: int = 3600) -> str:
        self._assert_ref(ref)
        if (
            not isinstance(expires_seconds, int)
            or isinstance(expires_seconds, bool)
            or expires_seconds <= 0
        ):
            raise ObjectStorageValidationError("expires_seconds must be a positive integer")
        expires_at = str(int(time.time()) + expires_seconds)
        string_to_sign = "\n".join(["GET", "", "", expires_at, f"/{self.config.bucket}/{ref.key}"])
        digest = hmac.new(
            self.config.access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        query = urlencode(
            {
                "OSSAccessKeyId": self.config.access_key_id,
                "Expires": expires_at,
                "Signature": base64.b64encode(digest).decode("ascii"),
            }
        )
        return f"{self._object_url(ref.key)}?{query}"

    def _public_url(self, key: str) -> str:
        object_key = normalize_object_key(key)
        encoded_key = quote(object_key, safe="/")
        base = self.config.public_base_url.strip().rstrip("/")
        if base:
            return f"{base}/{encoded_key}"
        if self.config.endpoint_style == "custom_domain":
            return f"{self.config.scheme}://{self.config.normalized_endpoint}/{encoded_key}"
        return f"{self.config.scheme}://{self.config.bucket}.{self.config.normalized_endpoint}/{encoded_key}"

    def _request(
        self,
        method: str,
        key: str,
        *,
        data: bytes | None = None,
        content_type: str = "",
        content_disposition: str | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        object_key = normalize_object_key(key)
        content_md5 = ""
        if data is not None:
            content_md5 = base64.b64encode(hashlib.md5(data).digest()).decode("ascii")
        request = urllib.request.Request(
            self._object_url(object_key),
            data=data,
            method=method,
            headers=self._sign_headers(
                method=method,
                object_key=object_key,
                content_type=content_type,
                content_md5=content_md5,
                content_disposition=content_disposition,
            ),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                return response.status, response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 404:
                raise ObjectStorageNotFoundError(f"object not found: {self.config.bucket}/{object_key}") from exc
            raise AliyunOSSError(
                f"{method} failed: status={exc.code} body={body}"
                f"{_endpoint_mismatch_hint(body=body, config=self.config)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AliyunOSSError(f"{method} failed: {exc.reason}") from exc

    def _object_url(self, object_key: str) -> str:
        encoded_key = quote(object_key, safe="/")
        if self.config.endpoint_style == "custom_domain":
            return f"{self.config.scheme}://{self.config.normalized_endpoint}/{encoded_key}"
        return f"{self.config.scheme}://{self.config.bucket}.{self.config.normalized_endpoint}/{encoded_key}"

    def _sign_headers(
        self,
        *,
        method: str,
        object_key: str,
        content_type: str = "",
        content_md5: str = "",
        content_disposition: str | None = None,
    ) -> dict[str, str]:
        date = formatdate(timeval=None, localtime=False, usegmt=True)
        string_to_sign = "\n".join(
            [method, content_md5, content_type, date, f"/{self.config.bucket}/{object_key}"]
        )
        digest = hmac.new(
            self.config.access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        headers = {
            "Authorization": f"OSS {self.config.access_key_id}:{base64.b64encode(digest).decode('ascii')}",
            "Date": date,
        }
        if content_type:
            headers["Content-Type"] = content_type
        if content_md5:
            headers["Content-MD5"] = content_md5
        if content_disposition:
            headers["Content-Disposition"] = content_disposition
        return headers

    def _assert_ref(self, ref: ObjectRef) -> None:
        if ref.provider != self.provider:
            raise ObjectStorageValidationError("object ref provider does not match repository")
        if ref.bucket != self.config.bucket:
            raise ObjectStorageValidationError("object ref bucket does not match repository")
        if ref.region != self.config.region:
            raise ObjectStorageValidationError("object ref region does not match repository")


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _required_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ObjectStorageValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _endpoint(value: Any) -> str:
    if value is None:
        raise ObjectStorageValidationError("endpoint must not be null")
    if not isinstance(value, str):
        raise ObjectStorageValidationError("endpoint must be a string")
    endpoint = value.strip().lower()
    if not endpoint:
        return ""
    _validate_host_only(endpoint, "endpoint")
    return endpoint


def _scheme(value: Any) -> str:
    scheme = _required_str(value, "scheme").lower()
    if scheme not in {"http", "https"}:
        raise ObjectStorageValidationError("scheme must be http or https")
    return scheme


def _endpoint_style(value: Any) -> str:
    endpoint_style = _required_str(value, "endpoint_style")
    if endpoint_style not in {"virtual_host", "custom_domain"}:
        raise ObjectStorageValidationError("endpoint_style must be virtual_host or custom_domain")
    return endpoint_style


def _positive_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ObjectStorageValidationError(f"{field} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ObjectStorageValidationError(f"{field} must be a number") from exc
    if parsed <= 0:
        raise ObjectStorageValidationError(f"{field} must be greater than 0")
    return parsed


def _validate_public_base_url(value: str) -> None:
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname:
        raise ObjectStorageValidationError("public_base_url must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ObjectStorageValidationError("public_base_url must not contain credentials, query, or fragment")


def _validate_host_only(value: str, field: str) -> None:
    if any(char in value for char in ("/", "\\", "?", "#", "@", ":")):
        raise ObjectStorageValidationError(f"{field} must be a host without scheme, path, port, query, or fragment")
    parsed = urlsplit(f"https://{value}")
    if parsed.hostname != value:
        raise ObjectStorageValidationError(f"{field} must be a valid host")


def _endpoint_mismatch_hint(*, body: str, config: AliyunOSSConfig) -> str:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return ""
    code = (root.findtext("Code") or "").strip()
    recommended_endpoint = (root.findtext("Endpoint") or "").strip()
    if code != "AccessDenied" or not recommended_endpoint:
        return ""
    return (
        " OSS endpoint mismatch: "
        f"configured_endpoint={config.normalized_endpoint} "
        f"bucket={config.bucket} "
        f"configured_region={config.region} "
        f"recommended_endpoint={recommended_endpoint}. "
        "Check OSS_REGION and OSS_ENDPOINT in the selected env file."
    )
