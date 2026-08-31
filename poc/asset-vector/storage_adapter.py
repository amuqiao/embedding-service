from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import mimetypes
from urllib.parse import urlsplit

from app.object_storage import (
    BaseObjectStorageAdapter,
    ExpectedObjectIntegrity,
    ObjectReadPolicy,
    ObjectStorageAdapterContext,
    ObjectStorageConfig,
    PublicUrlConfig,
    PublicUrlReadSpec,
    PublicUrlReader,
    PutObjectResult,
)


@dataclass(frozen=True)
class UploadedAsset:
    public_url: str
    content_type: str
    size_bytes: int
    sha256: str
    object_key: str


class AssetVectorPocStorageAdapter(BaseObjectStorageAdapter):
    def upload_local_image(self, local_path: Path, *, key: str) -> UploadedAsset:
        if not local_path.is_file():
            raise ValueError(f"local image does not exist: {local_path}")
        content_type = image_content_type(local_path)
        result = self.write_object_bytes(
            key,
            local_path.read_bytes(),
            content_type=content_type,
        )
        if not result.public_url:
            raise ValueError("object storage did not return public_url")
        return UploadedAsset(
            public_url=result.public_url,
            content_type=result.content_type,
            size_bytes=result.size_bytes,
            sha256=result.sha256,
            object_key=result.key,
        )

    def verify_public_image(self, public_url: str, *, sha256: str | None, max_bytes: int) -> bytes:
        return self.read_public_url(
            PublicUrlReadSpec(
                url=public_url,
                integrity=ExpectedObjectIntegrity(sha256=sha256),
                policy=ObjectReadPolicy(
                    verify_sha256=sha256 is not None,
                    max_bytes=max_bytes,
                ),
            )
        )


def build_oss_adapter_from_env(env: dict[str, str], *, allowed_hosts: tuple[str, ...]) -> AssetVectorPocStorageAdapter:
    missing = [
        name
        for name in (
            "OSS_BUCKET",
            "OSS_REGION",
            "OSS_ACCESS_KEY_ID",
            "OSS_ACCESS_KEY_SECRET",
            "OSS_PROJECT_ROOT",
        )
        if not env.get(name)
    ]
    if missing:
        raise ValueError(f"missing OSS env: {', '.join(missing)}")
    repository_config = ObjectStorageConfig(
        provider="aliyun_oss",
        options={
            "bucket": env["OSS_BUCKET"],
            "region": env["OSS_REGION"],
            "access_key_id": env["OSS_ACCESS_KEY_ID"],
            "access_key_secret": env["OSS_ACCESS_KEY_SECRET"],
            "key_prefix": env["OSS_PROJECT_ROOT"],
            "endpoint": env.get("OSS_ENDPOINT", ""),
            "public_base_url": public_base_url_from_env(env),
        },
    )
    return AssetVectorPocStorageAdapter(
        ObjectStorageAdapterContext.from_config(repository_config=repository_config),
        public_url_reader=PublicUrlReader(
            PublicUrlConfig(
                allowed_hosts=allowed_hosts,
                max_bytes_ceiling=int(env.get("POC_ASSET_VECTOR_IMAGE_MAX_BYTES", "10485760")),
            )
        ),
    )


def build_public_reader_adapter(env: dict[str, str], *, allowed_hosts: tuple[str, ...]) -> AssetVectorPocStorageAdapter:
    repository_config = ObjectStorageConfig(
        provider="local",
        options={
            "root": env.get("LOCAL_OBJECT_STORAGE_PATH", "storage/objects"),
            "bucket": env.get("OSS_BUCKET", "local-dev"),
            "region": env.get("OSS_REGION", "local"),
        },
    )
    return AssetVectorPocStorageAdapter(
        ObjectStorageAdapterContext.from_config(repository_config=repository_config),
        public_url_reader=PublicUrlReader(
            PublicUrlConfig(
                allowed_hosts=allowed_hosts,
                max_bytes_ceiling=int(env.get("POC_ASSET_VECTOR_IMAGE_MAX_BYTES", "10485760")),
            )
        ),
    )


def public_base_url_from_env(env: dict[str, str]) -> str:
    value = env.get("OSS_PUBLIC_ENDPOINT", "").strip()
    if not value:
        return ""
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("OSS_PUBLIC_ENDPOINT must be an https host or URL")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("OSS_PUBLIC_ENDPOINT must not include path, query, or fragment")
    return f"https://{parsed.hostname}"


def public_host_from_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"public_url must be https: {url}")
    return parsed.hostname.lower()


def image_content_type(path: Path) -> str:
    content_type = mimetypes.guess_type(path.name)[0]
    if content_type not in {"image/jpeg", "image/png", "image/webp", "image/bmp"}:
        raise ValueError(f"unsupported image content type for {path}: {content_type}")
    return content_type


def uploaded_asset_to_dict(result: UploadedAsset) -> dict[str, object]:
    return {
        "public_url": result.public_url,
        "content_type": result.content_type,
        "size_bytes": result.size_bytes,
        "sha256": result.sha256,
        "object_key": result.object_key,
    }
