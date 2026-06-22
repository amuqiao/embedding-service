from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return format_timedelta(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (frozenset, set)):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=json_default))


def section(name: str) -> None:
    print(f"\n== {name} ==")


def event(status: str, target: str, message: str = "") -> None:
    print(f"{status:<9} {target:<10} {message}")


def format_timedelta(value: timedelta | None) -> str:
    if value is None:
        return "-"
    total = int(value.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{sign}{days}d{hours}h"
    if hours:
        return f"{sign}{hours}h{minutes}m"
    if minutes:
        return f"{sign}{minutes}m{seconds}s"
    return f"{sign}{seconds}s"


def compact(value, *, max_length: int = 80) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return format_timedelta(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=json_default, separators=(",", ":"))
    else:
        text = str(value)
    text = text.replace("\n", "\\n")
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "..."


def print_table(rows: list[dict], columns: list[tuple[str, str]], *, empty_message: str = "no records") -> None:
    if not rows:
        print(empty_message)
        return

    headers = [header for _key, header in columns]
    rendered_rows = [[compact(row.get(key)) for key, _header in columns] for row in rows]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rendered_rows))
        for index in range(len(headers))
    ]

    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for rendered in rendered_rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(rendered)))


def trim_payload(value, *, max_items: int = 8):
    if value is None:
        return None
    if isinstance(value, list):
        if len(value) <= max_items:
            return [trim_payload(item, max_items=max_items) for item in value]
        return [trim_payload(item, max_items=max_items) for item in value[:max_items]] + [
            {"truncated_items": len(value) - max_items}
        ]
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["truncated_keys"] = len(value) - max_items
                break
            result[key] = trim_payload(item, max_items=max_items)
        return result
    return value


def summarize_job_payload(row: dict) -> dict:
    return {
        key: trim_payload(row.get(key))
        for key in (
            "job_params",
            "metadata",
            "runtime_ref",
            "execution_plan",
            "result",
            "result_ref",
            "canonical_result",
            "canonical_result_ref",
            "error",
            "callback_last_error",
        )
        if key in row
    }
