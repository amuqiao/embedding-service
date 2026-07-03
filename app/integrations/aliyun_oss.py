from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.utils import formatdate
from xml.etree import ElementTree


@dataclass(frozen=True)
class AliyunOSSConfig:
    bucket: str
    region: str
    access_key_id: str
    access_key_secret: str
    project_root: str = ""
    endpoint: str = ""
    endpoint_style: str = "virtual_host"
    scheme: str = "https"

    @property
    def normalized_endpoint(self) -> str:
        endpoint = self.endpoint or f"oss-{self.region}.aliyuncs.com"
        return endpoint.removeprefix("https://").removeprefix("http://").strip("/")

    @property
    def normalized_project_root(self) -> str:
        return self.project_root.strip().strip("/")


class AliyunOSSError(Exception):
    pass


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


def normalize_object_key(project_root: str, key: str) -> str:
    clean_root = project_root.strip().strip("/")
    clean_key = key.strip().strip("/")
    if clean_root and (clean_key == clean_root or clean_key.startswith(f"{clean_root}/")):
        return clean_key
    parts = [part for part in (clean_root, clean_key) if part]
    return "/".join(parts)


class AliyunOSSClient:
    def __init__(self, config: AliyunOSSConfig):
        self.config = config

    def object_key(self, key: str) -> str:
        return normalize_object_key(self.config.normalized_project_root, key)

    def get_object(self, key: str) -> bytes:
        _, body, _ = self._request("GET", self.object_key(key))
        return body

    def put_object(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        content_disposition: str | None = None,
    ) -> dict[str, str]:
        _, _, headers = self._request(
            "PUT",
            self.object_key(key),
            data=data,
            content_type=content_type,
            content_disposition=content_disposition,
        )
        return headers

    def head_object(self, key: str) -> dict[str, str]:
        _, _, headers = self._request("HEAD", self.object_key(key))
        return headers

    def delete_object(self, key: str) -> None:
        self._request("DELETE", self.object_key(key))

    def signed_get_url(self, key: str, *, expires_seconds: int = 3600) -> str:
        if expires_seconds <= 0:
            raise AliyunOSSError("signed URL expires_seconds must be greater than 0")
        object_key = self.object_key(key)
        expires_at = str(int(time.time()) + expires_seconds)
        string_to_sign = "\n".join(["GET", "", "", expires_at, f"/{self.config.bucket}/{object_key}"])
        digest = hmac.new(
            self.config.access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        query = urllib.parse.urlencode(
            {
                "OSSAccessKeyId": self.config.access_key_id,
                "Expires": expires_at,
                "Signature": base64.b64encode(digest).decode("ascii"),
            }
        )
        return f"{self._request_url(object_key)}?{query}"

    def _request(
        self,
        method: str,
        object_key: str,
        *,
        data: bytes | None = None,
        content_type: str = "",
        content_disposition: str | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:

        content_md5 = ""
        if data is not None:
            content_md5 = base64.b64encode(hashlib.md5(data).digest()).decode("ascii")
        req = urllib.request.Request(
            self._request_url(object_key),
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
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            hint = _endpoint_mismatch_hint(body=body, config=self.config)
            raise AliyunOSSError(f"{method} failed: status={exc.code} body={body}{hint}") from exc
        except urllib.error.URLError as exc:
            raise AliyunOSSError(f"{method} failed: {exc.reason}") from exc

    def _request_url(self, object_key: str) -> str:
        escaped_key = urllib.parse.quote(object_key, safe="/")
        endpoint = self.config.normalized_endpoint
        if self.config.endpoint_style == "custom_domain":
            return f"{self.config.scheme}://{endpoint}/{escaped_key}"
        if self.config.endpoint_style == "path":
            return f"{self.config.scheme}://{endpoint}/{self.config.bucket}/{escaped_key}"
        if self.config.endpoint_style == "virtual_host":
            return f"{self.config.scheme}://{self.config.bucket}.{endpoint}/{escaped_key}"
        raise AliyunOSSError("OSS_ENDPOINT_STYLE must be one of: virtual_host, custom_domain, path")

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
            [
                method,
                content_md5,
                content_type,
                date,
                f"/{self.config.bucket}/{object_key}",
            ]
        )
        digest = hmac.new(
            self.config.access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        signature = base64.b64encode(digest).decode("ascii")
        headers = {
            "Authorization": f"OSS {self.config.access_key_id}:{signature}",
            "Date": date,
        }
        if content_type:
            headers["Content-Type"] = content_type
        if content_md5:
            headers["Content-MD5"] = content_md5
        if content_disposition:
            headers["Content-Disposition"] = content_disposition
        return headers
