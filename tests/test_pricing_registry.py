from decimal import Decimal

from app.core.pricing_registry import TokenPrice, calculate_token_cost


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
        },
    )

    assert isinstance(amount, Decimal)
    assert amount == Decimal("0.00240000")
    assert str(amount) == "0.00240000"
