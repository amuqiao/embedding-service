import pytest
from pydantic import ValidationError

from app.core.usage_records import (
    AudioUsageRecord,
    ImageUsageRecord,
    TextUsageRecord,
    VideoUsageRecord,
    normalize_text_usage,
)


def test_usage_records_expose_stable_units_for_supported_media():
    text = normalize_text_usage(
        prompt_tokens=100,
        cached_input_tokens=20,
        completion_tokens=50,
        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
    )
    image = ImageUsageRecord(image_count=2)
    audio = AudioUsageRecord(duration_ms=1500)
    video = VideoUsageRecord(duration_ms=2500)

    assert text.kind == "text"
    assert text.usage_units() == {
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 50,
        "total_tokens": 150,
    }
    assert image.usage_units() == {"image_count": 2}
    assert audio.usage_units() == {"duration_ms": 1500}
    assert video.usage_units() == {"duration_ms": 2500}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_tokens": True, "output_tokens": 1, "total_tokens": 2},
        {"input_tokens": -1, "output_tokens": 1, "total_tokens": 0},
        {"input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 1, "total_tokens": 2},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 3},
    ],
)
def test_text_usage_record_rejects_invalid_token_shape(kwargs):
    with pytest.raises(ValidationError):
        TextUsageRecord(**kwargs)


def test_usage_records_reject_invalid_non_text_units():
    with pytest.raises(ValidationError):
        ImageUsageRecord(image_count=True)
    with pytest.raises(ValidationError):
        AudioUsageRecord(duration_ms=-1)
    with pytest.raises(ValidationError):
        VideoUsageRecord(duration_ms=1.5)
