from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from scripts.jobs import formatters
from smoke.flows import llm_job_billing


HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
DOUBLE_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")
SINGLE_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{[^{}]+\}(?!\})")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TEXT_SLOT = "<text>"
HUMAN_PREVIEW_ITEM_LIMIT = 3
HUMAN_PREVIEW_TEXT_MAX_LENGTH = 500

FlowError = llm_job_billing.FlowError


def _protected_segments(text: str) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    for pattern in (HTML_TAG_RE, DOUBLE_PLACEHOLDER_RE, SINGLE_PLACEHOLDER_RE):
        matches.extend((match.start(), match.end(), match.group(0)) for match in pattern.finditer(text))
    segments: list[tuple[int, int, str]] = []
    cursor = 0
    for start, end, token in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if start < cursor:
            continue
        segments.append((start, end, token))
        cursor = end
    return segments


def _extract_protected_tokens(text: str) -> list[str]:
    return [token for _, _, token in _protected_segments(text)]


def _protected_structure(text: str) -> list[str]:
    structure: list[str] = []
    cursor = 0
    for start, end, token in _protected_segments(text):
        if text[cursor:start].strip():
            structure.append(TEXT_SLOT)
        structure.append(token)
        cursor = end
    if text[cursor:].strip():
        structure.append(TEXT_SLOT)
    return structure


def _visible_text_length(text: str) -> int:
    stripped = text
    for pattern in (HTML_TAG_RE, DOUBLE_PLACEHOLDER_RE, SINGLE_PLACEHOLDER_RE):
        stripped = pattern.sub("", stripped)
    return len(stripped)


def _human_preview(text: Any, *, max_length: int = HUMAN_PREVIEW_TEXT_MAX_LENGTH) -> str:
    value = "" if text is None else str(text)
    value = ANSI_ESCAPE_RE.sub("", value)
    value = value.replace("\\", "\\\\")
    value = value.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    value = CONTROL_CHAR_RE.sub("", value)
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 15]}... <truncated>"


def _load_items(items_json: str | None, *, item_id: str, text: str, max_target_chars_hint: int | None) -> list[dict[str, Any]]:
    if items_json is None:
        return [{"id": item_id, "text": text, "max_target_chars_hint": max_target_chars_hint}]
    try:
        raw = json.loads(Path(items_json).read_text(encoding="utf-8"))
    except OSError as exc:
        raise FlowError(f"items-json cannot be read: {items_json}", exit_code=2) from exc
    except json.JSONDecodeError as exc:
        raise FlowError(f"items-json must be valid JSON: {items_json}", exit_code=2) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise FlowError("items-json must be an object with items[]", exit_code=2)
    return raw["items"]


def build_payload(
    *,
    source_language: str | None,
    target_language: str,
    items: list[dict[str, Any]],
    client_request_id: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "target_language": target_language,
        "items": items,
    }
    if source_language is not None:
        params["source_language"] = source_language
    return {
        "client_request_id": client_request_id or f"smoke-tagged-text-translation-{uuid.uuid4()}",
        "job_type": "tagged_text_translation",
        "job_params": params,
        "metadata": {"source": "scripts/smoke.sh tagged-text-translation"},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def _assert_translation_result(
    payload_items: list[dict[str, Any]],
    job: dict[str, Any],
    *,
    source_language: str | None,
    target_language: str,
) -> None:
    if job.get("job_status") != "succeeded":
        raise FlowError(f"job {job.get('job_id')} finished with {job.get('job_status')}", exit_code=1)
    result = job.get("job_result")
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise FlowError("tagged_text_translation result missing items", exit_code=1)
    if source_language is not None and result.get("source_language") != source_language:
        raise FlowError(
            f"result source_language mismatch: expected={source_language} actual={result.get('source_language')}",
            exit_code=1,
        )
    if result.get("target_language") != target_language:
        raise FlowError(
            f"result target_language mismatch: expected={target_language} actual={result.get('target_language')}",
            exit_code=1,
        )
    result_items = result["items"]
    if [item.get("id") for item in result_items] != [item.get("id") for item in payload_items]:
        raise FlowError("result item ids do not preserve request order", exit_code=1)
    for source_item, result_item in zip(payload_items, result_items):
        source_text = source_item.get("text")
        if result_item.get("source_text") != source_text:
            raise FlowError(f"result item {source_item.get('id')} source_text mismatch", exit_code=1)
        translated_text = result_item.get("translated_text")
        if not isinstance(translated_text, str) or not translated_text:
            raise FlowError(f"result item {source_item.get('id')} missing translated_text", exit_code=1)
        expected_tokens = _extract_protected_tokens(str(source_item.get("text", "")))
        actual_tokens = _extract_protected_tokens(translated_text)
        if actual_tokens != expected_tokens:
            raise FlowError(
                f"result item {source_item.get('id')} did not preserve tags/placeholders: "
                f"expected={expected_tokens} actual={actual_tokens}",
                exit_code=1,
            )
        expected_structure = _protected_structure(str(source_item.get("text", "")))
        actual_structure = _protected_structure(translated_text)
        if actual_structure != expected_structure:
            raise FlowError(
                f"result item {source_item.get('id')} changed tag/placeholder structure: "
                f"expected={expected_structure} actual={actual_structure}",
                exit_code=1,
            )
        char_count = result_item.get("char_count")
        if not isinstance(char_count, dict):
            raise FlowError(f"result item {source_item.get('id')} missing char_count", exit_code=1)
        expected_source_count = _visible_text_length(str(source_text))
        if char_count.get("source") != expected_source_count:
            raise FlowError(
                f"result item {source_item.get('id')} source char_count mismatch: "
                f"expected={expected_source_count} actual={char_count.get('source')}",
                exit_code=1,
            )
        expected_target_count = _visible_text_length(translated_text)
        if char_count.get("target") != expected_target_count:
            raise FlowError(
                f"result item {source_item.get('id')} target char_count mismatch: "
                f"expected={expected_target_count} actual={char_count.get('target')}",
                exit_code=1,
            )
        target_limit_hint = source_item.get("max_target_chars_hint")
        if target_limit_hint is None:
            if char_count.get("target_limit_hint") is not None or char_count.get("within_hint") is not None:
                raise FlowError(f"result item {source_item.get('id')} char_count hint fields must be null", exit_code=1)
        else:
            if char_count.get("target_limit_hint") != target_limit_hint:
                raise FlowError(f"result item {source_item.get('id')} target_limit_hint mismatch", exit_code=1)
            if char_count.get("within_hint") is not (expected_target_count <= target_limit_hint):
                raise FlowError(f"result item {source_item.get('id')} within_hint mismatch", exit_code=1)


def _translation_items_evidence(
    payload_items: list[dict[str, Any]],
    terminal_job: dict[str, Any],
) -> list[dict[str, Any]]:
    result = terminal_job.get("job_result")
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise FlowError("tagged_text_translation result missing items", exit_code=1)
    evidence: list[dict[str, Any]] = []
    for index, (source_item, result_item) in enumerate(zip(payload_items, result["items"]), start=1):
        source_text = result_item.get("source_text")
        if source_text is None:
            source_text = source_item.get("text")
        evidence.append(
            {
                "index": index,
                "id": result_item.get("id") or source_item.get("id"),
                "source_text": source_text,
                "translated_text": result_item.get("translated_text"),
                "max_target_chars_hint": source_item.get("max_target_chars_hint"),
                "char_count": result_item.get("char_count"),
            }
        )
    return evidence


def _request_items_evidence(payload_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "id": item.get("id"),
            "source_text": item.get("text"),
            "max_target_chars_hint": item.get("max_target_chars_hint"),
        }
        for index, item in enumerate(payload_items, start=1)
    ]


def _print_translation_preview(
    *,
    evidence_items: list[dict[str, Any]],
    source_language: str | None,
    target_language: str,
) -> None:
    formatters.section("Translation")
    shown_items = evidence_items[:HUMAN_PREVIEW_ITEM_LIMIT]
    for item in shown_items:
        print(
            f"item[{item['index']}] id={formatters.compact(item.get('id'))} "
            f"source={source_language or '-'} target={target_language}"
        )
        print(f"source: {_human_preview(item.get('source_text'))}")
        print(f"target: {_human_preview(item.get('translated_text'))}")
        char_count = item.get("char_count")
        if isinstance(char_count, dict):
            print(
                "chars:  "
                f"source={formatters.compact(char_count.get('source'))} "
                f"target={formatters.compact(char_count.get('target'))} "
                f"within_hint={formatters.compact(char_count.get('within_hint'))}"
            )
        print()
    omitted = len(evidence_items) - len(shown_items)
    if omitted > 0:
        formatters.event("INFO", "translation", f"omitted={omitted}; use --json for complete texts")


def run(
    *,
    confirm_cost: bool,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    source_language: str | None,
    target_language: str,
    item_id: str,
    text: str,
    max_target_chars_hint: int | None,
    items_json: str | None,
    client_request_id: str | None,
    json_output: bool,
) -> None:
    if not confirm_cost:
        raise FlowError("tagged-text-translation smoke requires --confirm-cost", exit_code=2)
    context = llm_job_billing.resolve_runtime_context(
        env_file=env_file,
        api_url=api_url,
        allow_remote_api=allow_remote_api,
        caller_id=caller_id,
        service_api_key=service_api_key,
    )
    items = _load_items(items_json, item_id=item_id, text=text, max_target_chars_hint=max_target_chars_hint)
    jobs_url = str(context.summary["jobs_url"])
    headers = llm_job_billing.build_headers(context.app_env, caller_id=caller_id, service_api_key=service_api_key)
    create_payload = build_payload(
        source_language=source_language,
        target_language=target_language,
        items=items,
        client_request_id=client_request_id,
    )
    create_envelope = llm_job_billing.request_json(jobs_url, method="POST", headers=headers, payload=create_payload)
    created = llm_job_billing.data_object(create_envelope, "job")
    job_id = str(created["job_id"])
    get_job_envelope = llm_job_billing.poll_job_envelope(
        jobs_url=jobs_url,
        job_id=job_id,
        headers=headers,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    terminal_job = llm_job_billing.data_object(get_job_envelope, "job")
    _assert_translation_result(
        items,
        terminal_job,
        source_language=source_language,
        target_language=target_language,
    )
    result = terminal_job.get("job_result")
    if not isinstance(result, dict):
        raise FlowError("tagged_text_translation result missing object", exit_code=1)
    request_items = _request_items_evidence(items)
    result_items = _translation_items_evidence(items, terminal_job)
    billing_envelope = llm_job_billing.request_json(f"{jobs_url}/{job_id}/billing", method="GET", headers=headers)
    billing = llm_job_billing.data_object(billing_envelope, "billing")
    job_summary = {
        "id": job_id,
        "status": terminal_job.get("job_status"),
        "type": terminal_job.get("job_type"),
    }
    billing_summary = {
        "mode": billing.get("status"),
        "cost": billing.get("total_cost_amount"),
        "currency": billing.get("currency"),
        "ai_call_count": billing.get("ai_call_count"),
        "billable_call_count": billing.get("billable_call_count"),
        "failed_call_count": billing.get("failed_call_count"),
    }
    summary = {
        "note": "summary is generated by scripts/smoke.sh; raw HTTP envelopes are under responses",
        "scenario": "tagged-text-translation",
        "job_id": job_id,
        "job_status": terminal_job.get("job_status"),
        "item_count": len(items),
        "billing_status": billing.get("status"),
        "total_cost_amount": billing.get("total_cost_amount"),
        "currency": billing.get("currency"),
        "ai_call_count": billing.get("ai_call_count"),
        "context": context.summary,
    }
    if json_output:
        formatters.print_json(
            {
                "ok": True,
                "scenario": "tagged-text-translation",
                "conclusion": f"job={terminal_job.get('job_status')} items={len(items)} billing={billing.get('status')}",
                "job": job_summary,
                "request": {
                    "source_language": source_language,
                    "target_language": target_language,
                    "items": request_items,
                },
                "result": {
                    "source_language": result.get("source_language"),
                    "target_language": result.get("target_language"),
                    "items": result_items,
                },
                "billing": billing_summary,
                "summary": summary,
                "responses": {
                    "create_job": create_envelope,
                    "get_job": get_job_envelope,
                    "get_billing": billing_envelope,
                },
            }
        )
        return
    formatters.section("Smoke")
    formatters.event("OK", "job", f"id={job_id} status={terminal_job.get('job_status')}")
    formatters.event("OK", "translation", f"items={len(items)} target_language={target_language}")
    formatters.event(
        "OK",
        "billing",
        f"{billing.get('status')} cost={billing.get('total_cost_amount')} {billing.get('currency')}",
    )
    _print_translation_preview(
        evidence_items=result_items,
        source_language=result.get("source_language") or source_language,
        target_language=str(result.get("target_language") or target_language),
    )
    formatters.print_table(
        [summary],
        [
            ("job_id", "job_id"),
            ("job_status", "job"),
            ("item_count", "items"),
            ("billing_status", "billing"),
            ("total_cost_amount", "cost"),
            ("currency", "currency"),
        ],
    )
