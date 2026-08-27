from __future__ import annotations

import base64
from dataclasses import dataclass
from email.utils import formatdate
import hashlib
import hmac
import http.client
import importlib.util
from pathlib import Path
import time
from typing import Any, Callable, Collection, Iterable
from urllib.parse import SplitResult, quote, unquote, urlencode, urlsplit, urlunsplit
import urllib.error
import urllib.request
from xml.etree import ElementTree

from .contract import (
    ObjectStorageContractError,
    bare_sha256,
    is_bare_sha256,
    validate_object_key,
    validate_positive_int,
    validate_storage_object,
)


DOWNLOAD_CHUNK_SIZE = 1024 * 1024
REF_FIELDS = frozenset({"object", "access"})
ACCESS_FIELDS = frozenset({"presigned_url"})


class AliyunOSSError(RuntimeError):
    pass


@dataclass(frozen=True)
class AliyunOSSObjectLocation:
    bucket: str
    region: str
    key: str
    internal: bool

    @property
    def object_identity(self) -> tuple[str, str, str]:
        return self.bucket, self.region, self.key


@dataclass(frozen=True)
class AliyunOSSConfig:
    bucket: str
    region: str
    access_key_id: str
    access_key_secret: str
    endpoint: str = ""

    @property
    def normalized_endpoint(self) -> str:
        endpoint = self.endpoint or aliyun_oss_endpoint(self.region, "public")
        return normalize_endpoint(endpoint)


class AliyunOSSClient:
    def __init__(
        self,
        config: AliyunOSSConfig,
        *,
        timeout_seconds: float = 20,
        prefer_sdk: bool = True,
    ) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.prefer_sdk = prefer_sdk

    def object_key(self, key: str) -> str:
        return normalize_object_key("", key)

    def get_object(self, key: str) -> bytes:
        if self.prefer_sdk and _has_oss_sdk():
            return self._get_object_with_sdk(self.object_key(key))
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
        if self.prefer_sdk and _has_oss_sdk():
            return self._put_object_with_sdk(
                self.object_key(key),
                data=data,
                content_type=content_type,
                content_disposition=content_disposition,
            )
        _, _, headers = self._request(
            "PUT",
            self.object_key(key),
            data=data,
            content_type=content_type,
            content_disposition=content_disposition,
        )
        return headers

    def delete_object(self, key: str) -> None:
        if self.prefer_sdk and _has_oss_sdk():
            self._delete_object_with_sdk(self.object_key(key))
            return
        self._request("DELETE", self.object_key(key))

    def signed_get_url(self, key: str, *, expires_seconds: int = 3600) -> str:
        return signed_url(
            method="GET",
            bucket=self.config.bucket,
            key=self.object_key(key),
            access_key_id=self.config.access_key_id,
            access_key_secret=self.config.access_key_secret,
            expires_seconds=expires_seconds,
            endpoint=self.config.normalized_endpoint,
        )

    def signed_put_url(self, key: str, *, expires_seconds: int = 3600, content_type: str = "") -> str:
        return signed_url(
            method="PUT",
            bucket=self.config.bucket,
            key=self.object_key(key),
            access_key_id=self.config.access_key_id,
            access_key_secret=self.config.access_key_secret,
            expires_seconds=expires_seconds,
            endpoint=self.config.normalized_endpoint,
            content_type=content_type,
        )

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
        request = urllib.request.Request(
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
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.status, response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            hint = _endpoint_mismatch_hint(body=body, config=self.config)
            raise AliyunOSSError(f"{method} failed: status={exc.code} body={body}{hint}") from exc
        except urllib.error.URLError as exc:
            raise AliyunOSSError(f"{method} failed: {exc.reason}") from exc

    def _request_url(self, object_key: str) -> str:
        return aliyun_oss_object_url(
            bucket=self.config.bucket,
            endpoint=self.config.normalized_endpoint,
            key=object_key,
        )

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

    def _sdk_client(self) -> Any:
        import alibabacloud_oss_v2 as oss

        credentials_provider = oss.credentials.StaticCredentialsProvider(
            access_key_id=self.config.access_key_id,
            access_key_secret=self.config.access_key_secret,
        )
        cfg = oss.config.load_default()
        cfg.credentials_provider = credentials_provider
        cfg.region = self.config.region
        cfg.endpoint = self.config.normalized_endpoint
        return oss.Client(cfg)

    def _get_object_with_sdk(self, object_key: str) -> bytes:
        import alibabacloud_oss_v2 as oss

        try:
            result = self._sdk_client().get_object(
                oss.GetObjectRequest(bucket=self.config.bucket, key=object_key)
            )
            return _read_sdk_result_body(result)
        except Exception as exc:  # noqa: BLE001 - normalize SDK exceptions at the boundary
            raise AliyunOSSError(f"GET failed via SDK: key={object_key} error={exc}") from exc

    def _put_object_with_sdk(
        self,
        object_key: str,
        *,
        data: bytes,
        content_type: str,
        content_disposition: str | None,
    ) -> dict[str, str]:
        import alibabacloud_oss_v2 as oss

        try:
            request_kwargs: dict[str, Any] = {
                "bucket": self.config.bucket,
                "key": object_key,
                "content_type": content_type,
                "content_length": len(data),
                "body": data,
            }
            if content_disposition:
                request_kwargs["content_disposition"] = content_disposition
            result = self._sdk_client().put_object(
                oss.PutObjectRequest(**request_kwargs)
            )
        except Exception as exc:  # noqa: BLE001 - normalize SDK exceptions at the boundary
            raise AliyunOSSError(f"PUT failed via SDK: key={object_key} error={exc}") from exc
        return _sdk_result_headers(result)

    def _delete_object_with_sdk(self, object_key: str) -> None:
        import alibabacloud_oss_v2 as oss

        try:
            self._sdk_client().delete_object(
                oss.DeleteObjectRequest(bucket=self.config.bucket, key=object_key)
            )
        except Exception as exc:  # noqa: BLE001 - normalize SDK exceptions at the boundary
            raise AliyunOSSError(f"DELETE failed via SDK: key={object_key} error={exc}") from exc


def aliyun_oss_endpoint(region: str, endpoint_type: str) -> str:
    if endpoint_type == "public":
        return f"oss-{region}.aliyuncs.com"
    if endpoint_type == "internal":
        return f"oss-{region}-internal.aliyuncs.com"
    raise AliyunOSSError("endpoint_type must be one of: internal, public")


def normalize_endpoint(value: str) -> str:
    return value.removeprefix("https://").removeprefix("http://").strip("/").lower()


def normalize_object_key(project_root: str, key: str) -> str:
    clean_root = project_root.strip().strip("/")
    clean_key = key.strip().strip("/")
    if clean_root and (clean_key == clean_root or clean_key.startswith(f"{clean_root}/")):
        return clean_key
    parts = [part for part in (clean_root, clean_key) if part]
    object_key = "/".join(parts)
    validate_object_key(object_key, field="key")
    return object_key


def normalize_public_base_url(value: str | None) -> str:
    stripped = (value or "").strip().rstrip("/")
    if not stripped:
        return ""
    parsed = urlsplit(stripped)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ObjectStorageContractError("public_base_url must be an absolute https URL")
    if parsed.username or parsed.password:
        raise ObjectStorageContractError("public_base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ObjectStorageContractError("public_base_url must not contain query string or fragment")
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    return f"https://{host}/{path}" if path else f"https://{host}"


def aliyun_oss_object_url(*, bucket: str, endpoint: str, key: str) -> str:
    return f"https://{bucket}.{normalize_endpoint(endpoint)}/{quote(key.lstrip('/'), safe='/')}"


def direct_public_url(*, bucket: str, region: str, key: str, public_base_url: str | None = None) -> str:
    encoded_key = quote(key.lstrip("/"), safe="/")
    base = normalize_public_base_url(public_base_url)
    if base:
        return f"{base}/{encoded_key}"
    return f"https://{bucket}.oss-{region}.aliyuncs.com/{encoded_key}"


def signed_url(
    *,
    method: str,
    bucket: str,
    key: str,
    access_key_id: str,
    access_key_secret: str,
    expires_seconds: int,
    endpoint: str,
    content_type: str = "",
) -> str:
    validate_positive_int(expires_seconds, field="expires_seconds")
    object_key = normalize_object_key("", key)
    expires_at = str(int(time.time()) + expires_seconds)
    normalized_method = method.upper()
    string_to_sign = "\n".join([normalized_method, "", content_type, expires_at, f"/{bucket}/{object_key}"])
    digest = hmac.new(
        access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    query = urlencode(
        {
            "OSSAccessKeyId": access_key_id,
            "Expires": expires_at,
            "Signature": base64.b64encode(digest).decode("ascii"),
        }
    )
    return f"{aliyun_oss_object_url(bucket=bucket, endpoint=endpoint, key=object_key)}?{query}"


def parse_aliyun_oss_url(url: str) -> AliyunOSSObjectLocation:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https":
        raise ObjectStorageContractError("OSS URL must use https")
    if parsed.query or parsed.fragment:
        raise ObjectStorageContractError("OSS URL must not contain query string or fragment")
    if parsed.username or parsed.password or parsed.port is not None:
        raise ObjectStorageContractError("OSS URL must not contain credentials or port")
    identity = aliyun_oss_identity_from_virtual_host_url(parsed, field="OSS URL")
    if identity is None:
        raise ObjectStorageContractError("OSS URL host is not an Aliyun OSS virtual-host endpoint")
    bucket, region, key, internal = identity
    return AliyunOSSObjectLocation(bucket=bucket, region=region, key=key, internal=internal)


def validate_input_ref(
    value: Any,
    *,
    name: str,
    require_size_bytes: bool = False,
    require_sha256: bool | None = None,
    allowed_content_types: Collection[str] | None = None,
) -> None:
    ref = validate_object_ref(
        value,
        field=name,
        require_integrity=False,
        require_size_bytes=require_size_bytes,
        allow_public_url=False,
        require_sha256=require_sha256,
        allowed_content_types=allowed_content_types,
    )
    validate_presigned_access(ref["access"], field=f"{name}.access", expected_object=ref["object"])


def validate_output_spec(
    value: Any,
    *,
    names: Iterable[str],
    allowed_content_types: dict[str, Collection[str]] | None = None,
) -> None:
    if not isinstance(value, dict):
        raise ObjectStorageContractError("output must be an object")
    required_names = tuple(names)
    missing = sorted(set(required_names) - set(value))
    if missing:
        raise ObjectStorageContractError(f"output missing required objects: {', '.join(missing)}")
    unsupported = sorted(set(value) - set(required_names))
    if unsupported:
        raise ObjectStorageContractError(f"output contains unsupported objects: {', '.join(unsupported)}")
    for name in required_names:
        validate_output_item(
            value[name],
            name=name,
            allowed_content_types=(allowed_content_types or {}).get(name),
        )


def validate_output_item(
    value: Any,
    *,
    name: str,
    allowed_content_types: Collection[str] | None = None,
) -> None:
    ref = validate_object_ref(
        value,
        field=f"output.{name}",
        require_integrity=False,
        allow_public_url=True,
        allowed_content_types=allowed_content_types,
    )
    validate_presigned_access(ref["access"], field=f"output.{name}.access", expected_object=ref["object"])


def validate_object_ref(
    value: Any,
    *,
    field: str,
    require_integrity: bool = False,
    require_size_bytes: bool | None = None,
    allow_public_url: bool,
    require_sha256: bool | None = None,
    allowed_content_types: Collection[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ObjectStorageContractError(f"{field} must be an object")
    missing = sorted(REF_FIELDS - set(value))
    if missing:
        raise ObjectStorageContractError(f"{field} missing required keys: {', '.join(missing)}")
    unsupported = sorted(set(value) - REF_FIELDS)
    if unsupported:
        raise ObjectStorageContractError(f"{field} contains unsupported keys: {', '.join(unsupported)}")
    obj = validate_storage_object(
        value["object"],
        field=f"{field}.object",
        require_integrity=require_integrity,
        require_size_bytes=require_size_bytes,
        require_sha256=require_sha256,
        allow_public_url=allow_public_url,
        allowed_content_types=allowed_content_types,
    )
    if "public_url" in obj:
        validate_public_url(obj["public_url"], expected_key=obj["key"], field=f"{field}.object.public_url")
    return value


def validate_presigned_access(value: Any, *, field: str, expected_object: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise ObjectStorageContractError(f"{field} must be an object")
    missing = sorted(ACCESS_FIELDS - set(value))
    if missing:
        raise ObjectStorageContractError(f"{field} missing required keys: {', '.join(missing)}")
    unsupported = sorted(set(value) - ACCESS_FIELDS)
    if unsupported:
        raise ObjectStorageContractError(f"{field} contains unsupported keys: {', '.join(unsupported)}")
    url = access_url({"access": value}, field=field)
    identity = aliyun_oss_identity_from_access_url(url, field=f"{field}.presigned_url")
    expected = (expected_object["bucket"], expected_object["region"], expected_object["key"])
    if identity != expected:
        raise ObjectStorageContractError(f"{field}.presigned_url does not match object identity")


def validate_result_ref_matches_output(value: Any, *, expected_output: dict[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ObjectStorageContractError(f"{name} result ref must be an object")
    manifest_object = value.get("object")
    if not isinstance(manifest_object, dict):
        raise ObjectStorageContractError(f"{name}.object must be an object")
    expected_object = output_item(expected_output, name)["object"]
    for key in ("provider", "bucket", "region", "key", "content_type"):
        if manifest_object.get(key) != expected_object.get(key):
            raise ObjectStorageContractError(f"{name}.object.{key} does not match requested output")
    validate_storage_object(
        manifest_object,
        field=f"{name}.object",
        require_integrity=True,
        require_size_bytes=True,
        require_sha256=True,
        allow_public_url=True,
    )
    return value


def guess_suffix_from_ref(value: dict[str, Any], *, default: str) -> str:
    suffix = Path(unquote(value["object"]["key"])).suffix
    return suffix if suffix else default


def download_input_ref(
    value: dict[str, Any],
    dst: Path,
    *,
    timeout_seconds: float,
    user_agent: str,
    name: str,
    verify_size_bytes: bool = False,
    verify_sha256: bool = False,
    urlopen_func: Callable[..., Any] | None = None,
) -> Path:
    url = access_url(value, field=f"{name}.access")
    obj = value["object"]
    expected_size_bytes = obj.get("size_bytes")
    expected_sha256 = obj.get("sha256")
    if verify_size_bytes and expected_size_bytes is None:
        raise RuntimeError(f"{name}.object.size_bytes is required when verify_size_bytes=true")
    if verify_sha256 and not is_bare_sha256(expected_sha256):
        raise RuntimeError(f"{name}.object.sha256 is required when verify_sha256=true")
    dst.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    digest = hashlib.sha256() if verify_sha256 else None
    total = 0
    opener = urlopen_func or urllib.request.urlopen
    with opener(request, timeout=timeout_seconds) as response, dst.open("wb") as fh:
        while True:
            chunk = response.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if digest is not None:
                digest.update(chunk)
            fh.write(chunk)
    if total == 0:
        raise RuntimeError(f"downloaded empty {name} file from {redact_url(url)}")
    if verify_size_bytes and total != expected_size_bytes:
        raise RuntimeError(f"{name}.object.size_bytes mismatch: expected {expected_size_bytes}, got {total}")
    if digest is not None:
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError(f"{name}.object.sha256 mismatch: expected {expected_sha256}, got {actual_sha256}")
    return dst


def output_item(output: dict[str, Any], name: str) -> dict[str, Any]:
    value = output.get(name)
    if not isinstance(value, dict):
        raise RuntimeError(f"output.{name} must be an object")
    return value


def put_output_item(value: dict[str, Any], *, content: bytes, timeout_seconds: float = 120) -> None:
    put_url = access_url(value, field="output access")
    obj = value["object"]
    identity = aliyun_oss_identity_from_access_url(put_url, field="output presigned URL")
    if identity != (obj["bucket"], obj["region"], obj["key"]):
        raise RuntimeError("output presigned URL does not match output object identity")
    parsed = urlsplit(put_url)
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=timeout_seconds)
    try:
        connection.request(
            "PUT",
            target,
            body=content,
            headers={
                "Content-Length": str(len(content)),
                "Content-Type": obj["content_type"],
                "User-Agent": "object-storage/1",
            },
        )
        response = connection.getresponse()
        body = response.read(1024)
        if response.status < 200 or response.status >= 300:
            message = body.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"presigned PUT upload failed: HTTP {response.status} {message}")
    finally:
        connection.close()


def object_result_ref(value: dict[str, Any], *, content_hash: str, size_bytes: int) -> dict[str, Any]:
    obj = dict(value["object"])
    obj["sha256"] = bare_sha256(content_hash)
    obj["size_bytes"] = validate_positive_int(size_bytes, field="size_bytes")
    return {"object": obj}


def public_object_url(value: dict[str, Any]) -> str:
    obj = value["object"]
    public_url = obj.get("public_url")
    if isinstance(public_url, str) and public_url.strip():
        return public_url.strip()
    return direct_public_url(bucket=obj["bucket"], region=obj["region"], key=obj["key"])


def access_url(value: dict[str, Any], *, field: str) -> str:
    access = value.get("access")
    if not isinstance(access, dict):
        raise ObjectStorageContractError(f"{field} must be an object")
    url = access.get("presigned_url")
    if not isinstance(url, str) or not url.strip():
        raise ObjectStorageContractError(f"{field}.presigned_url must be a non-empty string")
    return url.strip()


def validate_public_url(value: Any, *, expected_key: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ObjectStorageContractError(f"{field} must be a non-empty string")
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise ObjectStorageContractError(f"{field} is invalid") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise ObjectStorageContractError(f"{field} must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None or parsed.port is not None:
        raise ObjectStorageContractError(f"{field} must not contain user information or port")
    if parsed.query or parsed.fragment:
        raise ObjectStorageContractError(f"{field} must not contain query string or fragment")
    key = unquote(parsed.path.lstrip("/"))
    if key != expected_key:
        raise ObjectStorageContractError(f"{field} path does not match object key")


def validate_access_url(value: str, *, field: str) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ObjectStorageContractError(f"{field} is invalid") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise ObjectStorageContractError(f"{field} must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ObjectStorageContractError(f"{field} must not contain user information")
    if parsed.fragment:
        raise ObjectStorageContractError(f"{field} must not contain a fragment")
    decoded_canonical_path(parsed.path, field=field)


def aliyun_oss_identity_from_access_url(value: str, *, field: str) -> tuple[str, str, str]:
    validate_access_url(value, field=field)
    parsed = urlsplit(value)
    identity = aliyun_oss_identity_from_virtual_host_url(parsed, field=field)
    if identity is None:
        raise ObjectStorageContractError(f"{field} must be an Aliyun OSS virtual-host URL")
    bucket, region, key, _internal = identity
    return bucket, region, key


def aliyun_oss_identity_from_virtual_host_url(
    parsed: SplitResult,
    *,
    field: str,
) -> tuple[str, str, str, bool] | None:
    if not parsed.hostname:
        raise ObjectStorageContractError(f"{field} must include a host")
    host = parsed.hostname.lower()
    suffix = ".aliyuncs.com"
    marker = ".oss-"
    if not host.endswith(suffix) or marker not in host:
        return None
    if parsed.port is not None:
        raise ObjectStorageContractError(
            f"{field} must not contain a port when using an Aliyun OSS virtual-host URL"
        )
    bucket, endpoint_part = host.split(marker, 1)
    region = endpoint_part.removesuffix(suffix.removeprefix(".")).rstrip(".")
    internal = False
    if region.endswith("-internal"):
        internal = True
        region = region.removesuffix("-internal")
    if not bucket or not region:
        raise ObjectStorageContractError(f"{field} OSS endpoint is invalid")
    key = decoded_canonical_path(parsed.path, field=field).lstrip("/")
    validate_object_key(key, field=f"{field} object key")
    return bucket, region, key, internal


def decoded_canonical_path(value: str, *, field: str) -> str:
    decoded = unquote(value)
    if "\\" in decoded or any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded):
        raise ObjectStorageContractError(f"{field} path contains an invalid character")
    segments = decoded.removeprefix("/").split("/")
    if not segments or any(segment in {"", ".", ".."} for segment in segments):
        raise ObjectStorageContractError(f"{field} path must identify one canonical object")
    return decoded


def redact_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.query:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _has_oss_sdk() -> bool:
    return importlib.util.find_spec("alibabacloud_oss_v2") is not None


def _read_sdk_result_body(result: Any) -> bytes:
    if hasattr(result, "read"):
        body = result.read()
    else:
        body = getattr(result, "body", None)
        if hasattr(body, "read"):
            body = body.read()
    if isinstance(body, bytes):
        return body
    if isinstance(body, bytearray):
        return bytes(body)
    raise AliyunOSSError("OSS SDK GET result body is not bytes")


def _sdk_result_headers(result: Any) -> dict[str, str]:
    headers: dict[str, str] = {}
    for source, target in (
        ("etag", "ETag"),
        ("content_md5", "Content-MD5"),
        ("hash_crc64", "x-oss-hash-crc64ecma"),
        ("version_id", "x-oss-version-id"),
        ("request_id", "x-oss-request-id"),
    ):
        value = getattr(result, source, None)
        if value is not None:
            headers[target] = str(value)
    return headers


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
        f"recommended_endpoint={recommended_endpoint}."
    )
