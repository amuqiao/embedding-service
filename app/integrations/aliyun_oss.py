from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.utils import formatdate


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
        _, body, _ = self._request("GET", key.strip().strip("/"))
        return body

    def put_object(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> dict[str, str]:
        _, _, headers = self._request("PUT", self.object_key(key), data=data, content_type=content_type)
        return headers

    def head_object(self, key: str) -> dict[str, str]:
        _, _, headers = self._request("HEAD", self.object_key(key))
        return headers

    def delete_object(self, key: str) -> None:
        self._request("DELETE", self.object_key(key))

    def _request(
        self,
        method: str,
        object_key: str,
        *,
        data: bytes | None = None,
        content_type: str = "",
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
            ),
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise AliyunOSSError(f"{method} failed: status={exc.code} body={body}") from exc
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
        return headers
