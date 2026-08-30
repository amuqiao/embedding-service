from types import SimpleNamespace

import pytest

from app.jobs.types.audio_stem_separation.storage_policy import (
    AudioStemSeparationStoragePolicy,
    allowed_input_buckets as audio_allowed_input_buckets,
    allowed_input_regions as audio_allowed_input_regions,
    input_max_bytes as audio_input_max_bytes,
)
from app.jobs.types.audio_stem_separation_triton.storage_policy import (
    AudioStemSeparationTritonStoragePolicy,
)
from app.jobs.types.poster_title_image.storage_policy import (
    PosterTitleImageStoragePolicy,
    allowed_input_buckets as poster_allowed_input_buckets,
    allowed_input_regions as poster_allowed_input_regions,
)


def _settings(*, bucket: str = "bucket-a", region: str = "cn-hangzhou", backend: str = "aliyun_oss"):
    return SimpleNamespace(
        storage=SimpleNamespace(
            backend=backend,
            oss_bucket=bucket,
            oss_region=region,
        ),
        job=SimpleNamespace(oss_input_max_bytes=1024),
    )


def test_poster_storage_policy_defaults_to_storage_settings():
    policy = PosterTitleImageStoragePolicy()

    assert poster_allowed_input_buckets(policy, _settings(bucket="bucket-a")) == ("bucket-a",)
    assert poster_allowed_input_regions(policy, _settings(region="cn-hangzhou")) == ("cn-hangzhou",)


def test_audio_storage_policy_defaults_to_storage_settings():
    policy = AudioStemSeparationStoragePolicy()

    assert audio_allowed_input_buckets(policy, _settings(bucket="bucket-a")) == ("bucket-a",)
    assert audio_allowed_input_regions(policy, _settings(region="cn-hangzhou")) == ("cn-hangzhou",)
    assert audio_input_max_bytes(policy, _settings()) == 1024


def test_audio_storage_policy_overrides_storage_settings():
    policy = AudioStemSeparationStoragePolicy(
        allowed_input_buckets=("bucket-b",),
        allowed_input_regions=("cn-shanghai",),
        input_max_bytes=512,
    )

    assert audio_allowed_input_buckets(policy, _settings(bucket="bucket-a")) == ("bucket-b",)
    assert audio_allowed_input_regions(policy, _settings(region="cn-hangzhou")) == ("cn-shanghai",)
    assert audio_input_max_bytes(policy, _settings()) == 512


@pytest.mark.parametrize(
    "policy_cls",
    [
        PosterTitleImageStoragePolicy,
        AudioStemSeparationStoragePolicy,
        AudioStemSeparationTritonStoragePolicy,
    ],
)
@pytest.mark.parametrize(
    "kwargs",
    [
        {"output_namespace": ""},
        {"output_namespace": "/job-a"},
        {"output_namespace": "job-a/../x"},
        {"allowed_input_buckets": ()},
        {"allowed_input_buckets": ("",)},
        {"allowed_input_regions": (" cn-hangzhou",)},
    ],
)
def test_job_type_storage_policy_rejects_invalid_common_values(policy_cls, kwargs):
    with pytest.raises(ValueError):
        policy_cls(**kwargs)


@pytest.mark.parametrize(
    "policy_cls",
    [
        AudioStemSeparationStoragePolicy,
        AudioStemSeparationTritonStoragePolicy,
    ],
)
def test_audio_storage_policy_rejects_invalid_input_max_bytes(policy_cls):
    with pytest.raises(ValueError):
        policy_cls(input_max_bytes=0)
