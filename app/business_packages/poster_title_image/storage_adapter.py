from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from app.ai.adapters.base import ImageInput
from app.core.config import settings as app_settings
from app.core.exceptions import AppError
from app.business_packages.poster_title_image.image_policy import (
    POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES,
    POSTER_TITLE_IMAGE_REFERENCE_MAX_BYTES,
    POSTER_TITLE_IMAGE_REFERENCE_POLICY,
)
from app.tools.private.image import (
    validate_image_bytes,
)
from app.models.job import Job
from app.object_storage import (
    BaseObjectStorageAdapter,
    ExpectedObjectIntegrity,
    ObjectReadPolicy,
    ObjectStorageAdapterContext,
    ObjectStorageBackendError,
    ObjectStorageConfig,
    ObjectStorageConfigError,
    ObjectStorageValidationError,
    PublicUrlConfig,
    PublicUrlReader,
    PublicUrlReadSpec,
    PutObjectResult,
    parse_aliyun_oss_url,
)
from app.business_packages.poster_title_image.schemas import PosterTitleImageReferenceImage
from app.services.job_runtime import output_target_from_job

from .errors import POSTER_TITLE_IMAGE_REFERENCE_INVALID
from .storage_policy import (
    STORAGE_POLICY,
    PosterTitleImageStoragePolicy,
    allowed_input_buckets,
    allowed_input_regions,
)


class PosterTitleImageStorageAdapter(BaseObjectStorageAdapter):
    def __init__(
        self,
        *args: Any,
        settings: Any = app_settings,
        storage_policy: PosterTitleImageStoragePolicy = STORAGE_POLICY,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._settings = settings
        self._storage_policy = storage_policy

    @classmethod
    def from_settings(
        cls,
        settings: Any = app_settings,
        storage_policy: PosterTitleImageStoragePolicy = STORAGE_POLICY,
    ) -> PosterTitleImageStorageAdapter:
        repository_config = _repository_config_from_settings(settings)
        return cls(
            ObjectStorageAdapterContext.from_config(repository_config=repository_config),
            public_url_reader=PublicUrlReader(
                PublicUrlConfig(
                    allowed_hosts=_reference_allowed_hosts(settings, storage_policy=storage_policy),
                    max_bytes_ceiling=POSTER_TITLE_IMAGE_REFERENCE_MAX_BYTES,
                )
            ),
            settings=settings,
            storage_policy=storage_policy,
        )

    def validate_reference_ref_payload(self, reference_image: Any) -> None:
        try:
            _canonical_ref_from_oss_url_ref(
                _payload(reference_image),
                settings=self._settings,
                allowed_buckets=allowed_input_buckets(self._storage_policy, self._settings),
                allowed_regions=allowed_input_regions(self._storage_policy, self._settings),
                allowed_content_types=POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES,
            )
        except AppError as exc:
            _raise_reference_invalid_if_applicable(exc)
            raise

    def load_reference_image_from_ref(self, reference_image: Any) -> ImageInput:
        payload = _payload(reference_image)
        try:
            ref = _canonical_ref_from_oss_url_ref(
                payload,
                settings=self._settings,
                allowed_buckets=allowed_input_buckets(self._storage_policy, self._settings),
                allowed_regions=allowed_input_regions(self._storage_policy, self._settings),
                allowed_content_types=POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES,
            )
            data = self.read_public_url(
                PublicUrlReadSpec(
                    url=str(payload["public_url"]).strip(),
                    integrity=ExpectedObjectIntegrity(sha256=ref["sha256"]),
                    policy=ObjectReadPolicy(
                        verify_sha256=True,
                        max_bytes=POSTER_TITLE_IMAGE_REFERENCE_MAX_BYTES,
                    ),
                )
            )
            validate_image_bytes(data, content_type=ref["content_type"], policy=POSTER_TITLE_IMAGE_REFERENCE_POLICY)
        except AppError as exc:
            _raise_reference_invalid_if_applicable(exc)
            raise
        except ObjectStorageValidationError as exc:
            raise _reference_invalid_error(_app_error_from_object_storage_validation(exc)) from exc
        except ObjectStorageBackendError as exc:
            raise _reference_invalid_error(AppError("INVALID_INPUT", "reference image public_url is not readable")) from exc
        return ImageInput(data=data, content_type=ref["content_type"], detail="high")

    def write_title_layer(
        self,
        *,
        job: Job,
        item_id: str,
        image_index: int,
        data: bytes,
        content_disposition: str,
    ) -> dict[str, dict[str, str | int]]:
        output_target = output_target_from_job(job)
        key = _output_key_for_item_id(
            output_target,
            job=job,
            item_id=item_id,
            image_index=image_index,
            storage_policy=self._storage_policy,
        )
        try:
            written = _write_output_bytes(
                settings=self._settings,
                output_target=output_target,
                key=key,
                data=data,
                content_type="image/png",
                content_disposition=content_disposition,
            )
        except ObjectStorageValidationError as exc:
            raise AppError("OSS_WRITE_FAILED", "Failed to write OSS object") from exc
        except ObjectStorageBackendError as exc:
            raise AppError(
                "OSS_WRITE_FAILED",
                "Failed to write OSS object",
                details={
                    "oss_bucket": output_target["oss_bucket"],
                    "oss_key": key,
                    "oss_region": output_target["oss_region"],
                },
            ) from exc
        return {
            "written": {
                "oss_bucket": written.bucket,
                "oss_region": written.region,
                "oss_key": written.key,
                "content_hash": f"sha256:{written.sha256}",
                "content_size_bytes": written.size_bytes,
            },
            "object": _oss_url_ref_from_written(
                bucket=written.bucket,
                region=written.region,
                key=written.key,
                content_type=written.content_type,
                sha256=written.sha256,
                public_url=written.public_url,
                public_endpoint=self._settings.storage.oss_public_endpoint or None,
            ),
        }


def _payload(reference_image: Any) -> Mapping[str, Any]:
    if isinstance(reference_image, PosterTitleImageReferenceImage):
        return reference_image.model_dump()
    if hasattr(reference_image, "model_dump"):
        value = reference_image.model_dump()
        if isinstance(value, Mapping):
            return value
    if isinstance(reference_image, Mapping):
        return reference_image
    raise AppError("INVALID_INPUT", "reference_image must be an object")


def _repository_config_from_settings(settings: Any) -> ObjectStorageConfig:
    backend = settings.storage.backend
    return _repository_config(
        settings=settings,
        bucket=settings.storage.oss_bucket if backend == "aliyun_oss" else settings.storage.oss_bucket or "local-dev",
        region=settings.storage.oss_region if backend == "aliyun_oss" else settings.storage.oss_region or "local",
    )


def _repository_config_from_output_target(settings: Any, output_target: Mapping[str, Any]) -> ObjectStorageConfig:
    return _repository_config(
        settings=settings,
        bucket=str(output_target["oss_bucket"]),
        region=str(output_target["oss_region"]),
    )


def _repository_config(*, settings: Any, bucket: str, region: str) -> ObjectStorageConfig:
    backend = settings.storage.backend
    if backend == "local":
        return ObjectStorageConfig(
            provider="local",
            options={
                "root": settings.storage.local_object_storage_path,
                "bucket": bucket,
                "region": region,
                "public_base_url": _public_base_url(settings.storage.oss_public_endpoint),
            },
        )
    if backend == "aliyun_oss":
        return ObjectStorageConfig(
            provider="aliyun_oss",
            options={
                "bucket": bucket,
                "region": region,
                "access_key_id": settings.storage.oss_access_key_id,
                "access_key_secret": settings.storage.oss_access_key_secret_value,
                "key_prefix": settings.storage.oss_project_root,
                "endpoint": settings.storage.oss_endpoint,
                "endpoint_style": settings.storage.oss_endpoint_style,
                "public_base_url": _public_base_url(settings.storage.oss_public_endpoint),
                "scheme": settings.storage.oss_scheme,
            },
        )
    raise ObjectStorageConfigError("STORAGE_BACKEND must be local or aliyun_oss")


def _write_output_bytes(
    settings: Any,
    output_target: Mapping[str, Any],
    key: str,
    data: bytes,
    *,
    content_type: str,
    content_disposition: str | None,
) -> PutObjectResult:
    context = ObjectStorageAdapterContext.from_config(
        repository_config=_repository_config_from_output_target(settings, output_target)
    )
    return context.repository.put_bytes(
        key,
        data,
        content_type=content_type,
        content_disposition=content_disposition,
    )


def _reference_allowed_hosts(settings: Any, *, storage_policy: PosterTitleImageStoragePolicy) -> tuple[str, ...]:
    hosts: set[str] = set()
    for bucket in allowed_input_buckets(storage_policy, settings):
        for region in allowed_input_regions(storage_policy, settings):
            hosts.add(f"{bucket}.oss-{region}.aliyuncs.com")
    public_endpoint = _normalize_public_endpoint(settings.storage.oss_public_endpoint or None)
    if public_endpoint:
        hosts.add(public_endpoint)
    return tuple(sorted(hosts))


def _canonical_ref_from_oss_url_ref(
    payload: Mapping[str, Any],
    *,
    settings: Any,
    allowed_buckets: Collection[str],
    allowed_regions: Collection[str],
    allowed_content_types: Collection[str],
) -> dict[str, str]:
    public_url = _required_str(payload, "public_url")
    _required_str(payload, "internal_url")
    content_type = _required_str(payload, "content_type")
    sha256 = _required_bare_sha256(payload)
    public_endpoint = _normalize_public_endpoint(settings.storage.oss_public_endpoint or None)

    if public_endpoint and _url_host(public_url) == public_endpoint:
        key = _parse_public_endpoint_key(public_url, public_endpoint=public_endpoint)
        bucket = _required_setting("OSS bucket", getattr(settings.storage, "oss_bucket", "") or None)
        region = _required_setting("OSS region", getattr(settings.storage, "oss_region", "") or None)
    else:
        location = _parse_aliyun_oss_public_url(public_url)
        bucket = location["bucket"]
        region = location["region"]
        key = location["key"]

    _validate_allowed("OSS bucket", bucket, allowed_buckets)
    _validate_allowed("OSS region", region, allowed_regions)
    _validate_allowed("content_type", content_type, allowed_content_types)
    return {"bucket": bucket, "region": region, "key": key, "content_type": content_type, "sha256": sha256}


def _parse_aliyun_oss_public_url(url: str) -> dict[str, str]:
    try:
        location = parse_aliyun_oss_url(url)
    except ObjectStorageValidationError as exc:
        raise AppError("INVALID_INPUT", str(exc)) from exc
    if location.internal:
        raise AppError("INVALID_INPUT", "public_url must use a public OSS endpoint")
    return {"bucket": location.bucket, "region": location.region, "key": location.key}


def _parse_public_endpoint_key(url: str, *, public_endpoint: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https":
        raise AppError("INVALID_INPUT", "OSS URL must use https")
    if parsed.query or parsed.fragment:
        raise AppError("INVALID_INPUT", "OSS URL must not contain query string or fragment")
    if parsed.username or parsed.password or parsed.port is not None:
        raise AppError("INVALID_INPUT", "OSS URL must not contain credentials or port")
    if (parsed.hostname or "").lower() != public_endpoint:
        raise AppError("INVALID_INPUT", "public_url host does not match OSS public endpoint")
    key = unquote(parsed.path.lstrip("/"))
    if not key:
        raise AppError("INVALID_INPUT", "OSS URL object key is missing")
    if any(part in {"", ".", ".."} for part in key.split("/")):
        raise AppError("INVALID_INPUT", "OSS URL object key contains illegal path traversal")
    return key


def _output_key_for_item_id(
    output_target: Mapping[str, Any],
    *,
    job: Job,
    item_id: str,
    image_index: int,
    storage_policy: PosterTitleImageStoragePolicy,
) -> str:
    prefix = str(output_target["oss_prefix"]).strip("/")
    filename = "title-layer.png" if image_index == 1 else f"title-layer-{image_index}.png"
    key = f"{storage_policy.output_namespace}/{job.root_job_id or job.id}/{item_id}/{filename}"
    return f"{prefix}/{key}" if prefix else key


def _oss_url_ref_from_written(
    *,
    bucket: str,
    region: str,
    key: str,
    content_type: str,
    sha256: str,
    public_url: str | None,
    public_endpoint: str | None,
) -> dict[str, str]:
    encoded_key = quote(key.lstrip("/"), safe="/")
    normalized_public_endpoint = _normalize_public_endpoint(public_endpoint)
    return {
        "public_url": public_url
        or (
            f"https://{normalized_public_endpoint}/{encoded_key}"
            if normalized_public_endpoint
            else f"https://{bucket}.oss-{region}.aliyuncs.com/{encoded_key}"
        ),
        "internal_url": f"https://{bucket}.oss-{region}-internal.aliyuncs.com/{encoded_key}",
        "content_type": content_type,
        "sha256": _bare_sha256(sha256),
    }


def _public_base_url(public_endpoint: str | None) -> str:
    normalized = _normalize_public_endpoint(public_endpoint)
    return f"https://{normalized}" if normalized else ""


def _normalize_public_endpoint(value: str | None) -> str:
    return (value or "").strip().removeprefix("https://").removeprefix("http://").strip("/").lower()


def _url_host(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.username or parsed.password or parsed.port is not None:
        raise AppError("INVALID_INPUT", "OSS URL must not contain credentials or port")
    return (parsed.hostname or "").lower()


def _required_setting(label: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError("INVALID_INPUT", f"{label} is required for public endpoint refs")
    return value.strip()


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AppError("INVALID_INPUT", f"{key} is required")
    return value.strip()


def _required_bare_sha256(payload: Mapping[str, Any]) -> str:
    return _bare_sha256(_required_str(payload, "sha256"))


def _bare_sha256(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("sha256:"):
        candidate = candidate.removeprefix("sha256:")
    if len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate):
        return candidate
    raise AppError("INVALID_INPUT", "sha256 must be 64 lowercase hex characters")


def _validate_allowed(label: str, value: str, allowed: Collection[str]) -> None:
    if value not in set(allowed):
        raise AppError("INVALID_INPUT", f"{label} is not allowed")


def _app_error_from_object_storage_validation(exc: ObjectStorageValidationError) -> AppError:
    message = str(exc)
    if "sha256 mismatch" in message:
        return AppError("INPUT_HASH_MISMATCH", "reference image sha256 mismatch")
    if "max_bytes" in message or "Content-Length" in message:
        return AppError("INPUT_TOO_LARGE", "HTTP input exceeds service limit")
    return AppError("INVALID_INPUT", "reference image public_url is not readable")


def _reference_invalid_error(exc: AppError) -> AppError:
    details = {"source_reason": exc.code}
    if exc.details:
        details.update(exc.details)
    return AppError(
        POSTER_TITLE_IMAGE_REFERENCE_INVALID,
        "poster_title_image reference image is invalid",
        details=details,
    )


def _raise_reference_invalid_if_applicable(exc: AppError) -> None:
    if exc.code in {"INVALID_INPUT", "INPUT_HASH_MISMATCH", "INPUT_TOO_LARGE"}:
        raise _reference_invalid_error(exc) from exc
