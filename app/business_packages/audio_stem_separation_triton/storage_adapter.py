from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from app.tools.private.audio_input import AUDIO_INPUT_CONTENT_TYPES, AUDIO_WAV_CONTENT_TYPE, PreparedAudioInput
from app.core.config import settings as app_settings
from app.core.exceptions import AppError
from app.models.job import Job
from app.object_storage import (
    BaseObjectStorageAdapter,
    ExpectedObjectIntegrity,
    ObjectReadPolicy,
    ObjectReadSpec,
    ObjectRef,
    ObjectStorageAdapterContext,
    ObjectStorageBackendError,
    ObjectStorageConfig,
    ObjectStorageConfigError,
    ObjectStorageNotFoundError,
    ObjectStorageValidationError,
    PutObjectResult,
)
from app.schemas.jobs import (
    AudioDecodeNormalizeSpec,
    AudioInputPlanSnapshot,
    AudioStemSeparationInputObject,
    CanonicalObjectRefSnapshot,
    MediaFetchSpec,
)
from app.services.job_runtime import output_target_from_job
from app.tools.private.media_audio import decode_normalize_audio

from app.business_packages.audio_stem_separation.errors import AUDIO_STEM_INPUT_INVALID
from .storage_policy import (
    STORAGE_POLICY,
    AudioStemSeparationTritonStoragePolicy,
    allowed_input_buckets,
    allowed_input_regions,
    input_max_bytes,
)


class AudioStemSeparationTritonStorageAdapter(BaseObjectStorageAdapter):
    def __init__(
        self,
        *args: Any,
        repository_provider: str,
        settings: Any = app_settings,
        storage_policy: AudioStemSeparationTritonStoragePolicy = STORAGE_POLICY,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._repository_provider = repository_provider
        self._settings = settings
        self._storage_policy = storage_policy

    @classmethod
    def from_settings(
        cls,
        settings: Any = app_settings,
        storage_policy: AudioStemSeparationTritonStoragePolicy = STORAGE_POLICY,
    ) -> AudioStemSeparationTritonStorageAdapter:
        repository_config = _repository_config_from_settings(settings)
        return cls.from_config(
            repository_config=repository_config,
            repository_provider=repository_config.provider,
            settings=settings,
            storage_policy=storage_policy,
        )

    @classmethod
    def from_config(
        cls,
        *,
        repository_config: ObjectStorageConfig,
        repository_provider: str | None = None,
        settings: Any = app_settings,
        storage_policy: AudioStemSeparationTritonStoragePolicy = STORAGE_POLICY,
    ) -> AudioStemSeparationTritonStorageAdapter:
        return cls(
            ObjectStorageAdapterContext.from_config(repository_config=repository_config),
            repository_provider=repository_provider or repository_config.provider,
            settings=settings,
            storage_policy=storage_policy,
        )

    def build_audio_input_plan(
        self,
        input_audio: AudioStemSeparationInputObject,
        *,
        max_duration_seconds: float | None,
    ) -> dict:
        ref = _canonical_input_ref(input_audio, settings=self._settings, storage_policy=self._storage_policy)
        plan = AudioInputPlanSnapshot(
            source=CanonicalObjectRefSnapshot(
                provider=ref["provider"],
                bucket=ref["bucket"],
                region=ref["region"],
                key=ref["key"],
                content_type=ref["content_type"],
                content_hash=f"sha256:{ref['sha256']}",
            ),
            fetch=MediaFetchSpec(max_bytes=input_max_bytes(self._storage_policy, self._settings)),
            decode=AudioDecodeNormalizeSpec(source_content_type=ref["content_type"]),
            max_duration_seconds=max_duration_seconds,
        )
        return plan.model_dump(exclude_none=True)

    def prepare_audio_input(self, plan: AudioInputPlanSnapshot | dict) -> PreparedAudioInput:
        snapshot = AudioInputPlanSnapshot.model_validate(plan)
        source = snapshot.source
        try:
            data = self.read_object(
                ObjectReadSpec(
                    ref=ObjectRef(
                        provider=self._repository_provider,
                        bucket=source.bucket,
                        region=source.region,
                        key=source.key,
                    ),
                    integrity=ExpectedObjectIntegrity(
                        sha256=source.content_hash if self._storage_policy.verify_input_sha256 else None
                    ),
                    policy=ObjectReadPolicy(
                        verify_sha256=self._storage_policy.verify_input_sha256,
                        max_bytes=snapshot.fetch.max_bytes,
                    ),
                )
            )
        except ObjectStorageNotFoundError as exc:
            raise AppError(
                "OSS_OBJECT_NOT_FOUND",
                f"OSS object not found: {source.bucket}/{source.key}",
                details={"oss_bucket": source.bucket, "oss_key": source.key, "oss_region": source.region},
            ) from exc
        except ObjectStorageValidationError as exc:
            raise _audio_input_error_from_validation(exc) from exc
        except ObjectStorageBackendError as exc:
            raise AppError("OSS_FETCH_FAILED", "Failed to read OSS object") from exc
        if source.content_type != snapshot.decode.source_content_type:
            raise AppError(
                AUDIO_STEM_INPUT_INVALID,
                "audio input decode source_content_type mismatch",
                details={"source": source.content_type, "decode": snapshot.decode.source_content_type},
            )
        decoded = decode_normalize_audio(
            {
                "data": data,
                "decode": snapshot.decode.model_dump(),
                "max_duration_seconds": snapshot.max_duration_seconds,
            }
        )
        return PreparedAudioInput(data=decoded.data, sample_rate=decoded.sample_rate, duration_seconds=decoded.duration_seconds)

    def write_stem(self, *, job: Job, stem: str, data: bytes, content_disposition: str) -> dict[str, str]:
        output_target = output_target_from_job(job)
        key = _output_key(output_target, job=job, stem=stem, storage_policy=self._storage_policy)
        try:
            written = _write_output_bytes(
                settings=self._settings,
                output_target=output_target,
                key=key,
                data=data,
                content_type=AUDIO_WAV_CONTENT_TYPE,
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
        return _oss_url_ref_from_written(
            bucket=written.bucket,
            region=written.region,
            key=written.key,
            content_type=written.content_type,
            sha256=written.sha256,
            public_url=written.public_url,
            public_endpoint=self._settings.storage.oss_public_endpoint or None,
        )


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


def _canonical_input_ref(
    input_audio: AudioStemSeparationInputObject,
    *,
    settings: Any,
    storage_policy: AudioStemSeparationTritonStoragePolicy = STORAGE_POLICY,
) -> dict[str, str]:
    try:
        payload = input_audio.model_dump()
        ref = _canonical_ref_from_oss_url_ref(
            payload,
            settings=settings,
            allowed_buckets=allowed_input_buckets(storage_policy, settings),
            allowed_regions=allowed_input_regions(storage_policy, settings),
            allowed_content_types=AUDIO_INPUT_CONTENT_TYPES,
        )
    except AppError as exc:
        raise AppError(
            AUDIO_STEM_INPUT_INVALID,
            "audio stem input_audio is invalid",
            details={"source_reason": exc.code, **(exc.details or {})},
        ) from exc
    return ref


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
    return {"provider": "aliyun_oss", "bucket": bucket, "region": region, "key": key, "content_type": content_type, "sha256": sha256}


def _parse_aliyun_oss_public_url(url: str) -> dict[str, str]:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https":
        raise AppError("INVALID_INPUT", "OSS URL must use https")
    if parsed.query or parsed.fragment:
        raise AppError("INVALID_INPUT", "OSS URL must not contain query string or fragment")
    if parsed.username or parsed.password or parsed.port is not None:
        raise AppError("INVALID_INPUT", "OSS URL must not contain credentials or port")
    host = (parsed.hostname or "").lower()
    suffix = ".aliyuncs.com"
    marker = ".oss-"
    if not host.endswith(suffix) or marker not in host:
        raise AppError("INVALID_INPUT", "OSS URL host is not an Aliyun OSS virtual-host endpoint")
    bucket, endpoint_part = host.split(marker, 1)
    region_part = endpoint_part.removesuffix(suffix.removeprefix(".")).rstrip(".")
    if not bucket or not region_part:
        raise AppError("INVALID_INPUT", "OSS URL host is not an Aliyun OSS virtual-host endpoint")
    if region_part.endswith("-internal"):
        raise AppError("INVALID_INPUT", "public_url must use a public OSS endpoint")
    key = unquote(parsed.path.lstrip("/"))
    if not key:
        raise AppError("INVALID_INPUT", "OSS URL object key is missing")
    if any(part in {"", ".", ".."} for part in key.split("/")):
        raise AppError("INVALID_INPUT", "OSS URL object key contains illegal path traversal")
    return {"bucket": bucket, "region": region_part, "key": key}


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


def _output_key(
    output_target: Mapping[str, Any],
    *,
    job: Job,
    stem: str,
    storage_policy: AudioStemSeparationTritonStoragePolicy,
) -> str:
    prefix = str(output_target["oss_prefix"]).strip("/")
    key = f"{storage_policy.output_namespace}/{job.id}/{stem}.wav"
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


def _audio_input_error_from_validation(exc: ObjectStorageValidationError) -> AppError:
    message = str(exc)
    if "sha256 mismatch" in message:
        return AppError("INPUT_HASH_MISMATCH", "audio stem input sha256 mismatch")
    if "max_bytes" in message:
        return AppError("INPUT_TOO_LARGE", "object input exceeds service limit")
    return AppError(AUDIO_STEM_INPUT_INVALID, message)
