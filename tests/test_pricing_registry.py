from decimal import Decimal

import pytest

from app.core.pricing_registry import (
    CallPrice,
    ImagePrice,
    ImageTokenPrice,
    SecondPrice,
    TokenPrice,
    _parse_price,
    calculate_cost,
    calculate_token_cost,
)
from app.core.usage_records import AudioUsageRecord, ImageUsageRecord, TextUsageRecord, VideoUsageRecord


def test_per_token_cost_uses_decimal_cached_price_and_eight_decimal_places():
    price = TokenPrice(
        ref="openai:gpt-test@2026-06-23",
        model_id="gpt-test",
        provider="openai",
        provider_model="gpt-test",
        pricing_type="per_token",
        input_per_1m=Decimal("2.00"),
        cached_input_per_1m=Decimal("0.50"),
        output_per_1m=Decimal("10.00"),
        currency="USD",
        version="2026-06-23",
    )

    amount = calculate_token_cost(
        price,
        {
            "input_tokens": 1000,
            "cached_input_tokens": 400,
            "output_tokens": 100,
            "total_tokens": 1100,
        },
    )

    assert isinstance(amount, Decimal)
    assert amount == Decimal("0.00240000")
    assert str(amount) == "0.00240000"


def test_pricing_parser_accepts_supported_pricing_types():
    common = {
        "model_id": "model-1",
        "provider": "openai",
        "provider_model": "provider-model-1",
    }

    token_price = _parse_price(
        "token-ref",
        {
            **common,
            "pricing_type": "per_token",
            "input_per_1m": "1.00",
            "cached_input_per_1m": "0.10",
            "output_per_1m": "2.00",
        },
        version="2026-06-24",
        currency="USD",
    )
    image_price = _parse_price(
        "image-ref",
        {**common, "pricing_type": "per_image", "amount_per_image": "0.04"},
        version="2026-06-24",
        currency="USD",
    )
    image_token_price = _parse_price(
        "image-token-ref",
        {
            **common,
            "pricing_type": "per_image_token",
            "text_input_per_1m": "5.00",
            "cached_text_input_per_1m": "1.25",
            "image_input_per_1m": "8.00",
            "cached_image_input_per_1m": "2.00",
            "image_output_per_1m": "30.00",
        },
        version="2026-06-24",
        currency="USD",
    )
    second_price = _parse_price(
        "second-ref",
        {**common, "pricing_type": "per_second", "amount_per_second": "0.01"},
        version="2026-06-24",
        currency="USD",
    )
    call_price = _parse_price(
        "call-ref",
        {**common, "pricing_type": "per_call", "amount_per_call": "0.25"},
        version="2026-06-24",
        currency="USD",
    )

    assert isinstance(token_price, TokenPrice)
    assert isinstance(image_price, ImagePrice)
    assert isinstance(image_token_price, ImageTokenPrice)
    assert isinstance(second_price, SecondPrice)
    assert isinstance(call_price, CallPrice)


def test_pricing_parser_allows_item_version_override():
    price = _parse_price(
        "image-token-ref",
        {
            "model_id": "model-1",
            "provider": "openai",
            "provider_model": "provider-model-1",
            "version": "2026-07-02",
            "pricing_type": "per_image_token",
            "text_input_per_1m": "5.00",
            "cached_text_input_per_1m": "1.25",
            "image_input_per_1m": "8.00",
            "cached_image_input_per_1m": "2.00",
            "image_output_per_1m": "30.00",
        },
        version="2026-06-23",
        currency="USD",
    )

    assert price.version == "2026-07-02"


def test_calculate_cost_supports_text_image_audio_video_and_call():
    text_price = TokenPrice(
        ref="text-ref",
        model_id="model-1",
        provider="openai",
        provider_model="provider-model-1",
        pricing_type="per_token",
        currency="USD",
        version="2026-06-24",
        input_per_1m=Decimal("1.00"),
        cached_input_per_1m=Decimal("0.10"),
        output_per_1m=Decimal("2.00"),
    )
    image_price = ImagePrice(
        ref="image-ref",
        model_id="model-1",
        provider="openai",
        provider_model="provider-model-1",
        pricing_type="per_image",
        currency="USD",
        version="2026-06-24",
        amount_per_image=Decimal("0.04"),
    )
    image_token_price = ImageTokenPrice(
        ref="image-token-ref",
        model_id="model-1",
        provider="openai",
        provider_model="provider-model-1",
        pricing_type="per_image_token",
        currency="USD",
        version="2026-06-24",
        text_input_per_1m=Decimal("5.00"),
        cached_text_input_per_1m=Decimal("1.25"),
        image_input_per_1m=Decimal("8.00"),
        cached_image_input_per_1m=Decimal("2.00"),
        image_output_per_1m=Decimal("30.00"),
    )
    second_price = SecondPrice(
        ref="second-ref",
        model_id="model-1",
        provider="openai",
        provider_model="provider-model-1",
        pricing_type="per_second",
        currency="USD",
        version="2026-06-24",
        amount_per_second=Decimal("0.01"),
    )
    call_price = CallPrice(
        ref="call-ref",
        model_id="model-1",
        provider="openai",
        provider_model="provider-model-1",
        pricing_type="per_call",
        currency="USD",
        version="2026-06-24",
        amount_per_call=Decimal("0.25"),
    )

    assert calculate_cost(
        text_price,
        TextUsageRecord(input_tokens=1000, cached_input_tokens=200, output_tokens=500, total_tokens=1500),
    ) == Decimal("0.00182000")
    assert calculate_cost(image_price, ImageUsageRecord(image_count=3)) == Decimal("0.12000000")
    assert calculate_cost(
        image_token_price,
        ImageUsageRecord(
            image_count=1,
            input_tokens=17,
            output_tokens=196,
            total_tokens=213,
            text_input_tokens=17,
            image_input_tokens=0,
            image_output_tokens=196,
        ),
    ) == Decimal("0.00596500")
    assert calculate_cost(second_price, AudioUsageRecord(duration_ms=2500)) == Decimal("0.02500000")
    assert calculate_cost(second_price, VideoUsageRecord(duration_ms=1000)) == Decimal("0.01000000")
    assert calculate_cost(call_price, TextUsageRecord(input_tokens=1, output_tokens=1, total_tokens=2)) == Decimal(
        "0.25000000"
    )


def test_pricing_parser_rejects_unknown_type_missing_amount_and_negative_decimal():
    common = {
        "model_id": "model-1",
        "provider": "openai",
        "provider_model": "provider-model-1",
    }

    with pytest.raises(RuntimeError, match="unsupported pricing_type"):
        _parse_price(
            "unknown-ref",
            {**common, "pricing_type": "per_unit"},
            version="2026-06-24",
            currency="USD",
        )

    with pytest.raises(RuntimeError, match="amount_per_image"):
        _parse_price(
            "missing-ref",
            {**common, "pricing_type": "per_image"},
            version="2026-06-24",
            currency="USD",
        )

    with pytest.raises(RuntimeError, match="image_output_per_1m"):
        _parse_price(
            "missing-image-token-ref",
            {
                **common,
                "pricing_type": "per_image_token",
                "text_input_per_1m": "5.00",
                "cached_text_input_per_1m": "1.25",
                "image_input_per_1m": "8.00",
                "cached_image_input_per_1m": "2.00",
            },
            version="2026-06-24",
            currency="USD",
        )

    with pytest.raises(RuntimeError, match="non-negative"):
        _parse_price(
            "negative-ref",
            {**common, "pricing_type": "per_call", "amount_per_call": "-0.01"},
            version="2026-06-24",
            currency="USD",
        )


def test_calculate_cost_rejects_usage_kind_mismatch_and_bad_token_units():
    image_price = ImagePrice(
        ref="image-ref",
        model_id="model-1",
        provider="openai",
        provider_model="provider-model-1",
        pricing_type="per_image",
        currency="USD",
        version="2026-06-24",
        amount_per_image=Decimal("0.04"),
    )
    text_usage = TextUsageRecord(input_tokens=1, output_tokens=1, total_tokens=2)

    with pytest.raises(RuntimeError, match="per_image pricing requires image usage"):
        calculate_cost(image_price, text_usage)

    with pytest.raises(RuntimeError, match="per_image_token pricing requires image token usage"):
        calculate_cost(
            ImageTokenPrice(
                ref="image-token-ref",
                model_id="model-1",
                provider="openai",
                provider_model="provider-model-1",
                pricing_type="per_image_token",
                currency="USD",
                version="2026-06-24",
                text_input_per_1m=Decimal("5.00"),
                cached_text_input_per_1m=Decimal("1.25"),
                image_input_per_1m=Decimal("8.00"),
                cached_image_input_per_1m=Decimal("2.00"),
                image_output_per_1m=Decimal("30.00"),
            ),
            ImageUsageRecord(image_count=1),
        )

    with pytest.raises(RuntimeError, match="total_tokens"):
        calculate_token_cost(
            TokenPrice(
                ref="text-ref",
                model_id="model-1",
                provider="openai",
                provider_model="provider-model-1",
                pricing_type="per_token",
                currency="USD",
                version="2026-06-24",
                input_per_1m=Decimal("1.00"),
                cached_input_per_1m=Decimal("0.10"),
                output_per_1m=Decimal("2.00"),
            ),
            {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
            },
        )


def test_calculate_token_cost_rejects_non_token_price():
    image_price = ImagePrice(
        ref="image-ref",
        model_id="model-1",
        provider="openai",
        provider_model="provider-model-1",
        pricing_type="per_image",
        currency="USD",
        version="2026-06-24",
        amount_per_image=Decimal("0.04"),
    )

    with pytest.raises(RuntimeError, match="per_token pricing"):
        calculate_token_cost(
            image_price,  # type: ignore[arg-type]
            {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
                "total_tokens": 2,
            },
        )
