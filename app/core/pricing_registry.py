from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import yaml

from app.core.config import settings
from app.core.usage_records import AudioUsageRecord, ImageUsageRecord, TextUsageRecord, UsageRecord, VideoUsageRecord

MONEY_QUANT = Decimal("0.00000001")
ONE_MILLION = Decimal("1000000")
ONE_THOUSAND = Decimal("1000")


@dataclass(frozen=True)
class PricingRule:
    ref: str
    model_id: str
    provider: str
    provider_model: str
    pricing_type: str
    currency: str
    version: str


@dataclass(frozen=True)
class TokenPrice(PricingRule):
    input_per_1m: Decimal
    cached_input_per_1m: Decimal
    output_per_1m: Decimal


@dataclass(frozen=True)
class ImagePrice(PricingRule):
    amount_per_image: Decimal


@dataclass(frozen=True)
class SecondPrice(PricingRule):
    amount_per_second: Decimal


@dataclass(frozen=True)
class CallPrice(PricingRule):
    amount_per_call: Decimal


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


def _parse_common_fields(ref: str, config: dict[str, Any], *, version: str, currency: str) -> dict[str, str]:
    pricing_type = _required_str(config, "pricing_type", ref)
    return {
        "ref": ref,
        "model_id": _required_str(config, "model_id", ref),
        "provider": _required_str(config, "provider", ref),
        "provider_model": _required_str(config, "provider_model", ref),
        "pricing_type": pricing_type,
        "currency": currency,
        "version": version,
    }


def _parse_price(ref: str, config: dict[str, Any], *, version: str, currency: str) -> PricingRule:
    common = _parse_common_fields(ref, config, version=version, currency=currency)
    pricing_type = common["pricing_type"]
    if pricing_type != "per_token":
        if pricing_type == "per_image":
            return ImagePrice(
                **common,
                amount_per_image=_required_decimal(config, "amount_per_image", ref),
            )
        if pricing_type == "per_second":
            return SecondPrice(
                **common,
                amount_per_second=_required_decimal(config, "amount_per_second", ref),
            )
        if pricing_type == "per_call":
            return CallPrice(
                **common,
                amount_per_call=_required_decimal(config, "amount_per_call", ref),
            )
        raise RuntimeError(f"price {ref} unsupported pricing_type: {pricing_type}")
    return TokenPrice(
        **common,
        input_per_1m=_required_decimal(config, "input_per_1m", ref),
        cached_input_per_1m=_required_decimal(config, "cached_input_per_1m", ref),
        output_per_1m=_required_decimal(config, "output_per_1m", ref),
    )


def _prices() -> dict[str, PricingRule]:
    config = _load_pricing_config()
    version = _required_str(config, "version", "<root>")
    currency = _required_str(config, "currency", "<root>")
    if len(currency) > 8:
        raise RuntimeError("pricing config currency must be at most 8 characters")
    prices_config = config.get("prices")
    if not isinstance(prices_config, dict):
        raise RuntimeError("pricing config prices must be a YAML object")
    prices: dict[str, PricingRule] = {}
    for ref, item in prices_config.items():
        if not isinstance(ref, str) or not ref.strip():
            raise RuntimeError("pricing config price key must be a non-empty string")
        if not isinstance(item, dict):
            raise RuntimeError(f"price {ref} must be a YAML object")
        if ref in prices:
            raise RuntimeError(f"duplicate pricing ref: {ref}")
        prices[ref] = _parse_price(ref, item, version=version, currency=currency)
    return prices


def get_price(pricing_ref: str) -> PricingRule | None:
    return _prices().get(pricing_ref)


def default_currency() -> str:
    return _required_str(_load_pricing_config(), "currency", "<root>")


def require_price(pricing_ref: str) -> PricingRule:
    price = get_price(pricing_ref)
    if price is None:
        raise RuntimeError(f"pricing ref not found: {pricing_ref}")
    return price


def _required_usage_int(usage_units: dict[str, int], key: str) -> int:
    raw = usage_units.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise RuntimeError(f"usage_units requires integer field: {key}")
    if raw < 0:
        raise RuntimeError(f"usage_units field must be non-negative: {key}")
    return raw


def _quantize_money(amount: Decimal) -> Decimal:
    return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def calculate_cost(price: PricingRule, usage: UsageRecord) -> Decimal:
    if isinstance(price, TokenPrice):
        if not isinstance(usage, TextUsageRecord):
            raise RuntimeError("per_token pricing requires text usage")
        input_tokens = Decimal(usage.input_tokens)
        cached_input_tokens = Decimal(usage.cached_input_tokens)
        output_tokens = Decimal(usage.output_tokens)
        billable_input_tokens = input_tokens - cached_input_tokens
        amount = (
            billable_input_tokens * price.input_per_1m
            + cached_input_tokens * price.cached_input_per_1m
            + output_tokens * price.output_per_1m
        ) / ONE_MILLION
        return _quantize_money(amount)
    if isinstance(price, ImagePrice):
        if not isinstance(usage, ImageUsageRecord):
            raise RuntimeError("per_image pricing requires image usage")
        return _quantize_money(Decimal(usage.image_count) * price.amount_per_image)
    if isinstance(price, SecondPrice):
        if not isinstance(usage, AudioUsageRecord | VideoUsageRecord):
            raise RuntimeError("per_second pricing requires audio or video usage")
        seconds = Decimal(usage.duration_ms) / ONE_THOUSAND
        return _quantize_money(seconds * price.amount_per_second)
    if isinstance(price, CallPrice):
        return _quantize_money(price.amount_per_call)
    raise RuntimeError(f"unsupported pricing rule: {type(price).__name__}")


def calculate_token_cost(price: TokenPrice, usage_units: dict[str, int]) -> Decimal:
    if getattr(price, "pricing_type", "per_token") != "per_token":
        raise RuntimeError("calculate_token_cost requires per_token pricing")
    input_tokens = _required_usage_int(usage_units, "input_tokens")
    cached_input_tokens = _required_usage_int(usage_units, "cached_input_tokens")
    output_tokens = _required_usage_int(usage_units, "output_tokens")
    total_tokens = _required_usage_int(usage_units, "total_tokens")
    TextUsageRecord(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )
    billable_input_tokens = Decimal(input_tokens - cached_input_tokens)
    amount = (
        billable_input_tokens * price.input_per_1m
        + Decimal(cached_input_tokens) * price.cached_input_per_1m
        + Decimal(output_tokens) * price.output_per_1m
    ) / ONE_MILLION
    return _quantize_money(amount)


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
