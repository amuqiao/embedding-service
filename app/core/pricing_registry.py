from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import yaml

from app.core.config import settings

MONEY_QUANT = Decimal("0.00000001")
ONE_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class TokenPrice:
    ref: str
    model_id: str
    provider: str
    provider_model: str
    pricing_type: str
    input_per_1m: Decimal
    cached_input_per_1m: Decimal
    output_per_1m: Decimal
    currency: str
    version: str


def _load_pricing_config() -> dict[str, Any]:
    try:
        raw = settings.billing.pricing_config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"pricing config not found: {settings.billing.pricing_config_path}") from exc
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise RuntimeError("pricing config must be a YAML object")
    return data


def _required_str(config: dict[str, Any], key: str, ref: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"price {ref} requires string field: {key}")
    return value.strip()


def _required_decimal(config: dict[str, Any], key: str, ref: str) -> Decimal:
    raw = config.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError(f"price {ref} requires decimal string field: {key}")
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise RuntimeError(f"price {ref} has invalid decimal field: {key}") from exc
    if value < 0:
        raise RuntimeError(f"price {ref} decimal field must be non-negative: {key}")
    return value


def _parse_price(ref: str, config: dict[str, Any], *, version: str, currency: str) -> TokenPrice:
    pricing_type = _required_str(config, "pricing_type", ref)
    if pricing_type != "per_token":
        raise RuntimeError(f"price {ref} unsupported pricing_type: {pricing_type}")
    return TokenPrice(
        ref=ref,
        model_id=_required_str(config, "model_id", ref),
        provider=_required_str(config, "provider", ref),
        provider_model=_required_str(config, "provider_model", ref),
        pricing_type=pricing_type,
        input_per_1m=_required_decimal(config, "input_per_1m", ref),
        cached_input_per_1m=_required_decimal(config, "cached_input_per_1m", ref),
        output_per_1m=_required_decimal(config, "output_per_1m", ref),
        currency=currency,
        version=version,
    )


def _prices() -> dict[str, TokenPrice]:
    config = _load_pricing_config()
    version = _required_str(config, "version", "<root>")
    currency = _required_str(config, "currency", "<root>")
    if len(currency) > 8:
        raise RuntimeError("pricing config currency must be at most 8 characters")
    prices_config = config.get("prices")
    if not isinstance(prices_config, dict):
        raise RuntimeError("pricing config prices must be a YAML object")
    prices: dict[str, TokenPrice] = {}
    for ref, item in prices_config.items():
        if not isinstance(ref, str) or not ref.strip():
            raise RuntimeError("pricing config price key must be a non-empty string")
        if not isinstance(item, dict):
            raise RuntimeError(f"price {ref} must be a YAML object")
        if ref in prices:
            raise RuntimeError(f"duplicate pricing ref: {ref}")
        prices[ref] = _parse_price(ref, item, version=version, currency=currency)
    return prices


def get_price(pricing_ref: str) -> TokenPrice | None:
    return _prices().get(pricing_ref)


def default_currency() -> str:
    return _required_str(_load_pricing_config(), "currency", "<root>")


def require_price(pricing_ref: str) -> TokenPrice:
    price = get_price(pricing_ref)
    if price is None:
        raise RuntimeError(f"pricing ref not found: {pricing_ref}")
    return price


def calculate_token_cost(price: TokenPrice, usage_units: dict[str, int]) -> Decimal:
    input_tokens = Decimal(int(usage_units.get("input_tokens", 0)))
    cached_input_tokens = Decimal(int(usage_units.get("cached_input_tokens", 0)))
    output_tokens = Decimal(int(usage_units.get("output_tokens", 0)))
    billable_input_tokens = max(input_tokens - cached_input_tokens, Decimal("0"))
    amount = (
        billable_input_tokens * price.input_per_1m
        + cached_input_tokens * price.cached_input_per_1m
        + output_tokens * price.output_per_1m
    ) / ONE_MILLION
    return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def validate_price_matches_model(
    *,
    pricing_ref: str,
    model_id: str,
    provider: str,
    provider_model: str,
) -> None:
    price = require_price(pricing_ref)
    expected = {
        "model_id": model_id,
        "provider": provider,
        "provider_model": provider_model,
    }
    actual = {
        "model_id": price.model_id,
        "provider": price.provider,
        "provider_model": price.provider_model,
    }
    mismatches = [key for key, expected_value in expected.items() if actual[key] != expected_value]
    if mismatches:
        joined = ", ".join(mismatches)
        raise RuntimeError(f"pricing ref {pricing_ref} does not match model {model_id}: {joined}")
