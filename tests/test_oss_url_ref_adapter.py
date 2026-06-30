import pytest

from app.core.exceptions import AppError
from app.jobs.adapters.oss_url_ref import canonical_ref_from_oss_url_ref, oss_url_ref_from_output_object


def test_oss_url_ref_adapter_projects_cdn_public_url():
    projected = oss_url_ref_from_output_object(
        bucket="aigc-datas",
        region="us-west-1",
        key="test-cms-poster-title/ai-jobs/title.png",
        content_type="image/png",
        content_hash=f"sha256:{'a' * 64}",
        public_endpoint="https://aigc-datas.epubgame.com/",
    )

    assert projected == {
        "public_url": "https://aigc-datas.epubgame.com/test-cms-poster-title/ai-jobs/title.png",
        "internal_url": (
            "https://aigc-datas.oss-us-west-1-internal.aliyuncs.com/"
            "test-cms-poster-title/ai-jobs/title.png"
        ),
        "content_type": "image/png",
        "sha256": "a" * 64,
    }


def test_oss_url_ref_adapter_parses_cdn_public_url_with_internal_identity():
    ref = canonical_ref_from_oss_url_ref(
        {
            "public_url": "https://aigc-datas.epubgame.com/test-cms-poster-title/reference.png",
            "internal_url": (
                "https://aigc-datas.oss-us-west-1-internal.aliyuncs.com/"
                "test-cms-poster-title/reference.png"
            ),
            "content_type": "image/png",
            "sha256": "b" * 64,
        },
        allowed_buckets={"aigc-datas"},
        allowed_regions={"us-west-1"},
        allowed_content_types={"image/png"},
        public_endpoint="aigc-datas.epubgame.com",
    )

    assert ref.bucket == "aigc-datas"
    assert ref.region == "us-west-1"
    assert ref.key == "test-cms-poster-title/reference.png"
    assert ref.content_hash == f"sha256:{'b' * 64}"


def test_oss_url_ref_adapter_rejects_cdn_public_url_path_mismatch():
    with pytest.raises(AppError, match="same OSS object"):
        canonical_ref_from_oss_url_ref(
            {
                "public_url": "https://aigc-datas.epubgame.com/test-cms-poster-title/other.png",
                "internal_url": (
                    "https://aigc-datas.oss-us-west-1-internal.aliyuncs.com/"
                    "test-cms-poster-title/reference.png"
                ),
                "content_type": "image/png",
                "sha256": "c" * 64,
            },
            public_endpoint="aigc-datas.epubgame.com",
        )
