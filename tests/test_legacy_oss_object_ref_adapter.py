import pytest

from app.core.exceptions import AppError
from app.integrations.object_storage import ObjectWriteResult
from app.jobs.payload_adapters.legacy_oss_object_ref import (
    canonical_ref_from_legacy_source_oss,
    legacy_oss_artifact_from_output_object,
)


def _source_oss(**overrides):
    payload = {
        "oss_key": " /novel-localization/input/source.txt ",
        "oss_url": "https://example.com/novel-localization/input/source.txt",
        "content_hash": f"sha256:{'a' * 64}",
        "content_type": "Text/Plain; Charset=UTF-8",
    }
    payload.update(overrides)
    return payload


def test_legacy_source_oss_projects_to_canonical_ref():
    ref = canonical_ref_from_legacy_source_oss(
        _source_oss(),
        bucket="aigc-datas",
        region="us-west-1",
    )

    assert ref.provider == "aliyun_oss"
    assert ref.bucket == "aigc-datas"
    assert ref.region == "us-west-1"
    assert ref.key == "novel-localization/input/source.txt"
    assert ref.content_type == "text/plain; charset=utf-8"
    assert ref.content_hash == f"sha256:{'a' * 64}"


def test_legacy_source_oss_accepts_missing_content_hash():
    payload = _source_oss()
    payload.pop("content_hash")

    ref = canonical_ref_from_legacy_source_oss(payload, bucket="aigc-datas", region="us-west-1")

    assert ref.content_hash is None


def test_legacy_source_oss_accepts_explicit_null_content_hash():
    ref = canonical_ref_from_legacy_source_oss(
        _source_oss(content_hash=None),
        bucket="aigc-datas",
        region="us-west-1",
    )

    assert ref.content_hash is None


def test_legacy_source_oss_requires_oss_url_without_parsing_identity():
    payload = _source_oss(oss_url="https://example.com/not-the-same-key.txt")

    ref = canonical_ref_from_legacy_source_oss(payload, bucket="aigc-datas", region="us-west-1")

    assert ref.key == "novel-localization/input/source.txt"

    with pytest.raises(AppError, match="oss_url is required"):
        canonical_ref_from_legacy_source_oss(_source_oss(oss_url=" "), bucket="aigc-datas", region="us-west-1")


def test_legacy_source_oss_rejects_empty_key_but_preserves_legacy_object_names():
    with pytest.raises(AppError, match="non-empty OSS object key"):
        canonical_ref_from_legacy_source_oss(_source_oss(oss_key=" / "), bucket="aigc-datas", region="us-west-1")

    ref = canonical_ref_from_legacy_source_oss(
        _source_oss(oss_key=" /novel/../source.txt "),
        bucket="aigc-datas",
        region="us-west-1",
    )

    assert ref.key == "novel/../source.txt"


def test_legacy_source_oss_requires_legacy_prefixed_hash():
    with pytest.raises(AppError, match="sha256:<64 lowercase hex>"):
        canonical_ref_from_legacy_source_oss(
            _source_oss(content_hash="b" * 64),
            bucket="aigc-datas",
            region="us-west-1",
        )

    with pytest.raises(AppError, match="sha256:<64 lowercase hex>"):
        canonical_ref_from_legacy_source_oss(
            _source_oss(content_hash=f"sha256:{'B' * 64}"),
            bucket="aigc-datas",
            region="us-west-1",
        )


def test_legacy_source_oss_accepts_wrapped_prefixed_hash_and_extra_content_type_params():
    ref = canonical_ref_from_legacy_source_oss(
        _source_oss(
            content_hash=f" sha256:{'b' * 64} ",
            content_type="text/plain; charset=utf-8; format=flowed",
        ),
        bucket="aigc-datas",
        region="us-west-1",
    )

    assert ref.content_hash == f"sha256:{'b' * 64}"
    assert ref.content_type == "text/plain; charset=utf-8"


def test_legacy_source_oss_rejects_non_text_content_type():
    with pytest.raises(AppError, match="text/plain; charset=utf-8"):
        canonical_ref_from_legacy_source_oss(
            _source_oss(content_type="application/json"),
            bucket="aigc-datas",
            region="us-west-1",
        )


def test_legacy_source_oss_applies_explicit_content_type_allowlist():
    ref = canonical_ref_from_legacy_source_oss(
        _source_oss(),
        bucket="aigc-datas",
        region="us-west-1",
        allowed_content_types={"text/plain; charset=utf-8"},
    )
    assert ref.content_type == "text/plain; charset=utf-8"

    with pytest.raises(AppError, match="content_type is not allowed"):
        canonical_ref_from_legacy_source_oss(
            _source_oss(),
            bucket="aigc-datas",
            region="us-west-1",
            allowed_content_types={"application/json"},
        )


def test_legacy_source_oss_requires_explicit_bucket_and_region():
    with pytest.raises(AppError, match="bucket is required"):
        canonical_ref_from_legacy_source_oss(_source_oss(), bucket="", region="us-west-1")

    with pytest.raises(AppError, match="region is required"):
        canonical_ref_from_legacy_source_oss(_source_oss(), bucket="aigc-datas", region="")


def test_legacy_artifact_projects_mapping_output_result():
    artifact = legacy_oss_artifact_from_output_object(
        {
            "oss_bucket": "aigc-datas",
            "oss_key": " /novel-localization/jobs/job-1/localized.txt ",
            "oss_region": "us-west-1",
            "content_hash": f"sha256:{'c' * 64}",
            "content_size_bytes": 123,
        },
        key="localized_text",
        type="text",
        label="本地化正文",
    )

    assert artifact == {
        "key": "localized_text",
        "type": "text",
        "label": "本地化正文",
        "storage": "oss_object",
        "oss_bucket": "aigc-datas",
        "oss_key": "novel-localization/jobs/job-1/localized.txt",
        "oss_region": "us-west-1",
        "content_hash": f"sha256:{'c' * 64}",
        "content_size_bytes": 123,
    }


def test_legacy_artifact_projects_object_write_result():
    artifact = legacy_oss_artifact_from_output_object(
        ObjectWriteResult(
            provider="local",
            bucket="aigc-datas",
            region="local",
            key="novel-localization/jobs/job-1/translated.txt",
            content_type="text/plain; charset=utf-8",
            content_hash=f"sha256:{'d' * 64}",
            content_size_bytes=456,
        ),
        key="translated_text",
        type="text",
        label="英文终稿",
    )

    assert artifact["storage"] == "oss_object"
    assert artifact["oss_key"] == "novel-localization/jobs/job-1/translated.txt"
    assert artifact["content_hash"] == f"sha256:{'d' * 64}"
    assert "content" not in artifact


def test_legacy_artifact_projects_valid_apply_mode():
    artifact = legacy_oss_artifact_from_output_object(
        {
            "oss_bucket": "aigc-datas",
            "oss_key": "novel-localization/jobs/job-1/work-note.txt",
            "oss_region": "us-west-1",
            "content_hash": f"sha256:{'e' * 64}",
            "content_size_bytes": 12,
        },
        key="work_note",
        type="work_note",
        label="工作注释",
        apply_mode="replace",
    )

    assert artifact["apply_mode"] == "replace"


def test_legacy_artifact_rejects_invalid_apply_mode():
    with pytest.raises(AppError, match="apply_mode must be replace or append"):
        legacy_oss_artifact_from_output_object(
            {
                "oss_bucket": "aigc-datas",
                "oss_key": "novel-localization/jobs/job-1/work-note.txt",
                "oss_region": "us-west-1",
                "content_hash": f"sha256:{'e' * 64}",
                "content_size_bytes": 12,
            },
            key="work_note",
            type="work_note",
            label="工作注释",
            apply_mode="merge",
        )


def test_legacy_artifact_rejects_invalid_output_manifest():
    with pytest.raises(AppError, match="content_size_bytes must be a non-negative integer"):
        legacy_oss_artifact_from_output_object(
            {
                "oss_bucket": "aigc-datas",
                "oss_key": "novel-localization/jobs/job-1/localized.txt",
                "oss_region": "us-west-1",
                "content_hash": f"sha256:{'c' * 64}",
                "content_size_bytes": -1,
            },
            key="localized_text",
            type="text",
            label="本地化正文",
        )


def test_legacy_artifact_rejects_bool_size_and_bare_hash():
    valid = {
        "oss_bucket": "aigc-datas",
        "oss_key": "novel-localization/jobs/job-1/localized.txt",
        "oss_region": "us-west-1",
        "content_hash": f"sha256:{'c' * 64}",
        "content_size_bytes": 123,
    }

    with pytest.raises(AppError, match="content_size_bytes must be a non-negative integer"):
        legacy_oss_artifact_from_output_object(
            {**valid, "content_size_bytes": True},
            key="localized_text",
            type="text",
            label="本地化正文",
        )

    with pytest.raises(AppError, match="sha256:<64 lowercase hex>"):
        legacy_oss_artifact_from_output_object(
            {**valid, "content_hash": "c" * 64},
            key="localized_text",
            type="text",
            label="本地化正文",
        )


def test_legacy_artifact_rejects_missing_required_manifest_fields():
    valid = {
        "oss_bucket": "aigc-datas",
        "oss_key": "novel-localization/jobs/job-1/localized.txt",
        "oss_region": "us-west-1",
        "content_hash": f"sha256:{'c' * 64}",
        "content_size_bytes": 123,
    }

    for missing_field in ("oss_bucket", "oss_key", "oss_region", "content_hash", "content_size_bytes"):
        payload = dict(valid)
        payload.pop(missing_field)
        with pytest.raises(AppError):
            legacy_oss_artifact_from_output_object(
                payload,
                key="localized_text",
                type="text",
                label="本地化正文",
            )
