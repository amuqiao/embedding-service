import pytest

from app.core.exceptions import AppError
from smoke.flows.oss.url_ref import (
    CanonicalObjectRef,
    canonical_ref_from_cpp_oss_url_ref,
    cpp_oss_url_ref_from_canonical,
)


def _payload(**overrides):
    payload = {
        "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster/title-layer.png",
        "internal_url": (
            "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/"
            "ai-output/poster/title-layer.png"
        ),
        "content_type": "image/png",
        "sha256": "c" * 64,
    }
    payload.update(overrides)
    return payload


def test_cpp_oss_url_ref_adapter_normalizes_to_canonical_ref():
    ref = canonical_ref_from_cpp_oss_url_ref(_payload())

    assert ref.provider == "aliyun_oss"
    assert ref.bucket == "cpp-rs-dev"
    assert ref.region == "ap-southeast-1"
    assert ref.key == "ai-output/poster/title-layer.png"
    assert ref.content_type == "image/png"
    assert ref.content_hash == f"sha256:{'c' * 64}"


def test_cpp_oss_url_ref_adapter_rejects_public_url_query():
    with pytest.raises(AppError, match="query string or fragment"):
        canonical_ref_from_cpp_oss_url_ref(
            _payload(
                public_url=(
                    "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/"
                    "ai-output/poster/title-layer.png?x-oss-signature=secret"
                )
            )
        )


def test_cpp_oss_url_ref_adapter_does_not_validate_input_internal_url():
    ref = canonical_ref_from_cpp_oss_url_ref(
        _payload(
            internal_url=(
                "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/"
                "ai-output/poster/other.png?x-oss-signature=secret"
            )
        )
    )

    assert ref.key == "ai-output/poster/title-layer.png"


def test_cpp_oss_url_ref_adapter_still_requires_internal_url_field():
    payload = _payload()
    payload.pop("internal_url")

    with pytest.raises(AppError, match="internal_url is required"):
        canonical_ref_from_cpp_oss_url_ref(payload)


def test_cpp_oss_url_ref_adapter_requires_bare_sha256():
    with pytest.raises(AppError, match="64 lowercase hex"):
        canonical_ref_from_cpp_oss_url_ref(_payload(sha256=f"sha256:{'c' * 64}"))


def test_cpp_oss_url_ref_adapter_applies_explicit_profile_constraints():
    ref = canonical_ref_from_cpp_oss_url_ref(
        _payload(),
        allowed_buckets={"cpp-rs-dev"},
        allowed_regions={"ap-southeast-1"},
        allowed_content_types={"image/png"},
    )
    assert ref.bucket == "cpp-rs-dev"

    with pytest.raises(AppError, match="OSS bucket is not allowed"):
        canonical_ref_from_cpp_oss_url_ref(_payload(), allowed_buckets={"other-bucket"})

    with pytest.raises(AppError, match="content_type is not allowed"):
        canonical_ref_from_cpp_oss_url_ref(_payload(), allowed_content_types={"image/jpeg"})


def test_cpp_oss_url_ref_adapter_projects_from_canonical_ref():
    ref = canonical_ref_from_cpp_oss_url_ref(_payload())

    projected = cpp_oss_url_ref_from_canonical(
        ref,
        public_url=_payload()["public_url"],
        internal_url=_payload()["internal_url"],
    )

    assert projected == _payload()


def test_cpp_oss_url_ref_adapter_projection_requires_aliyun_provider():
    ref = CanonicalObjectRef(
        provider="local",
        bucket="cpp-rs-dev",
        region="ap-southeast-1",
        key="ai-output/poster/title-layer.png",
        content_type="image/png",
        content_hash=f"sha256:{'c' * 64}",
    )

    with pytest.raises(AppError, match="provider must be aliyun_oss"):
        cpp_oss_url_ref_from_canonical(
            ref,
            public_url=_payload()["public_url"],
            internal_url=_payload()["internal_url"],
        )
