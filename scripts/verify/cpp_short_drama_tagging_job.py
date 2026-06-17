"""Smoke test the CPP-facing short-drama tagging Job API.

This script simulates CPP by calling the normal /api/v1/ai-jobs/jobs route.
It does not read .env or service config files. Pass target environment details
with CLI flags.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = ROOT_DIR / ".data" / "poc" / "short_drama_tagging" / "inputs" / "jobs" / "per_book"
DEFAULT_CASE_ID = "200000000000000417"
WORK_CONTEXT_KEYS = {
    "title",
    "synopsis",
    "subtitle_language",
    "series_structure",
    "content_type",
    "episode_count",
    "audio_language",
}
ASSET_KEYS = {
    "asset_type",
    "episode_no",
    "format",
    "uri",
    "text",
    "content_hash",
    "language",
    "metadata",
}


@dataclass(frozen=True)
class Config:
    base_url: str
    api_prefix: str
    service_api_key: str
    caller_id: str
    client_request_id_prefix: str
    poll_interval: float
    timeout_seconds: int
    request_timeout_seconds: float
    callback_url: str | None
    keep_going: bool


@dataclass(frozen=True)
class TaggingCase:
    name: str
    path: Path
    payload: dict[str, Any]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read input JSON {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in input JSON {path}: {exc}") from exc


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def discover_case_paths(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise ValueError(f"POC input dir not found: {input_dir}")
    paths = sorted(input_dir.glob("*/input.json"))
    if not paths:
        raise ValueError(f"no POC input JSON files found under: {input_dir}")
    return paths


def load_case(path: Path) -> TaggingCase:
    payload = require_object(load_json(path), str(path))
    job_params = require_object(payload.get("job_params"), f"{path}.job_params")
    work_context = require_object(job_params.get("work_context"), f"{path}.job_params.work_context")
    t_book_id = job_params.get("t_book_id")
    if not isinstance(t_book_id, str) or not t_book_id.strip():
        raise ValueError(f"{path}.job_params.t_book_id must be a non-empty string")
    if payload.get("job_type") != "short_drama.tagging.initial":
        raise ValueError(f"{path}.job_type must be short_drama.tagging.initial")
    if not isinstance(job_params.get("assets"), list) or not job_params["assets"]:
        raise ValueError(f"{path}.job_params.assets must be a non-empty array")
    title = work_context.get("title")
    name = t_book_id if not isinstance(title, str) or not title.strip() else f"{t_book_id}:{title}"
    return TaggingCase(name=name, path=path, payload=payload)


def load_cases_from_paths(paths: list[Path]) -> tuple[TaggingCase, ...]:
    cases = [load_case(path) for path in paths]
    seen: set[str] = set()
    for case in cases:
        t_book_id = case.payload["job_params"]["t_book_id"]
        if t_book_id in seen:
            raise ValueError(f"duplicate t_book_id in selected cases: {t_book_id}")
        seen.add(t_book_id)
    return tuple(cases)


def select_cases(
    *,
    input_dir: Path,
    input_jsons: list[Path] | None,
    case_names: list[str] | None,
) -> tuple[TaggingCase, ...]:
    if input_jsons:
        cases = load_cases_from_paths(input_jsons)
    else:
        cases = load_cases_from_paths(discover_case_paths(input_dir))

    by_book_id = {case.payload["job_params"]["t_book_id"]: case for case in cases}
    if not case_names:
        if DEFAULT_CASE_ID not in by_book_id:
            raise ValueError(
                f"default case {DEFAULT_CASE_ID} not found under {input_dir}; "
                "run --list-cases or pass --case <t_book_id>"
            )
        return (by_book_id[DEFAULT_CASE_ID],)
    if "all" in case_names:
        if len(case_names) != 1:
            raise ValueError("--case all cannot be combined with other --case values")
        return cases
    unknown = [name for name in case_names if name not in by_book_id]
    if unknown:
        available = ", ".join(sorted(by_book_id))
        raise ValueError(f"unknown --case: {', '.join(unknown)}; available: {available}")
    return tuple(by_book_id[name] for name in case_names)


def case_summary(case: TaggingCase) -> dict[str, Any]:
    job_params = case.payload["job_params"]
    context = job_params["work_context"]
    return {
        "t_book_id": job_params["t_book_id"],
        "title": context.get("title"),
        "subtitle_language": context.get("subtitle_language"),
        "episode_count": context.get("episode_count"),
        "asset_count": len(job_params["assets"]),
        "path": str(case.path),
    }


def sanitize_request_id(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-")
    return sanitized or "case"


def api_job_params_from_poc(case: TaggingCase) -> dict[str, Any]:
    job_params = case.payload["job_params"]
    work_context = require_object(job_params.get("work_context"), f"{case.path}.job_params.work_context")
    assets = job_params.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError(f"{case.path}.job_params.assets must be a non-empty array")
    return {
        "t_book_id": job_params["t_book_id"],
        "work_context": {
            key: copy.deepcopy(value)
            for key, value in work_context.items()
            if key in WORK_CONTEXT_KEYS
        },
        "assets": [
            {
                key: copy.deepcopy(value)
                for key, value in require_object(asset, f"{case.path}.job_params.assets[{index}]").items()
                if key in ASSET_KEYS
            }
            for index, asset in enumerate(assets)
        ],
    }


def build_payload(config: Config, case: TaggingCase, index: int, total: int) -> dict[str, Any]:
    t_book_id = case.payload["job_params"]["t_book_id"]
    suffix = sanitize_request_id(t_book_id if total == 1 else f"{index}-{t_book_id}")
    client_request_id = f"{config.client_request_id_prefix}:{suffix}"
    if len(client_request_id) > 255:
        raise ValueError(f"client_request_id too long: {len(client_request_id)} > 255")
    payload: dict[str, Any] = {
        "client_request_id": client_request_id,
        "job_type": "short_drama.tagging.initial",
        "job_params": api_job_params_from_poc(case),
        "metadata": {
            "source_service": "cpp",
            "business_scene": "short_drama_tagging",
            "smoke_test": "cpp_short_drama_tagging",
            "smoke_case": t_book_id,
            "poc_input_path": str(case.path),
            "source_client_request_id": case.payload.get("client_request_id"),
        },
    }
    if config.callback_url:
        payload["callback"] = {
            "url": config.callback_url,
            "events": ["job.succeeded", "job.failed"],
        }
    return payload


def raise_for_status_with_body(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text
        if body:
            print("response body:", body[:2000], file=sys.stderr)
        raise exc


def api_path(config: Config, suffix: str) -> str:
    return f"{config.api_prefix}{suffix}"


def require_tagging_contract(payload: dict[str, Any], body: dict[str, Any]) -> None:
    if body.get("job_type") != "short_drama.tagging.initial":
        raise AssertionError(f"unexpected job_type: {body.get('job_type')}")
    if body.get("status") != "succeeded":
        raise AssertionError(f"job did not succeed: {body.get('status')}")
    if body.get("error") is not None:
        raise AssertionError(f"succeeded job returned error: {body.get('error')}")
    if body.get("result") is not None:
        raise AssertionError(f"short_drama.tagging.initial public result must be null: {body.get('result')!r}")
    metadata = body.get("metadata") or {}
    if metadata.get("business_scene") != "short_drama_tagging":
        raise AssertionError(f"metadata.business_scene changed: {metadata.get('business_scene')}")
    if body.get("client_request_id") != payload["client_request_id"]:
        raise AssertionError(
            "client_request_id changed: "
            f"expected={payload['client_request_id']} actual={body.get('client_request_id')}"
        )


def run_case(
    client: httpx.Client,
    config: Config,
    headers: dict[str, str],
    case: TaggingCase,
    index: int,
    total: int,
) -> bool:
    payload = build_payload(config, case, index, total)
    summary = case_summary(case)
    print(f"case[{index}/{total}]: {summary['t_book_id']} {summary.get('title') or ''}".strip())
    print("input:", json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print("client_request_id:", payload["client_request_id"])

    created = client.post(api_path(config, "/jobs"), headers=headers, json=payload)
    if created.status_code == 401:
        print("authentication failed: pass the target key with --service-api-key", file=sys.stderr)
        return False
    raise_for_status_with_body(created)
    created_body = created.json()
    print("created:", json.dumps(created_body, ensure_ascii=False, sort_keys=True))

    status_url = created_body["status_url"]
    deadline = time.monotonic() + config.timeout_seconds
    while time.monotonic() < deadline:
        status_resp = client.get(status_url, headers=headers)
        raise_for_status_with_body(status_resp)
        status_body = status_resp.json()
        progress = status_body.get("progress") or {}
        print("status:", status_body.get("status"), progress.get("percent"), progress.get("stage"))
        if status_body.get("status") == "succeeded":
            try:
                require_tagging_contract(payload, status_body)
            except AssertionError as exc:
                print(f"case failed contract: {summary['t_book_id']}: {exc}", file=sys.stderr)
                print("final:", json.dumps(status_body, ensure_ascii=False, sort_keys=True), file=sys.stderr)
                return False
            print("final:", json.dumps(status_body, ensure_ascii=False, sort_keys=True))
            print("case passed:", summary["t_book_id"])
            return True
        if status_body.get("status") in {"failed", "canceled"}:
            print("final:", json.dumps(status_body, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return False
        time.sleep(config.poll_interval)

    print(f"case timed out after {config.timeout_seconds}s: {summary['t_book_id']}", file=sys.stderr)
    return False


def parse_args() -> tuple[Config, tuple[TaggingCase, ...]]:
    parser = argparse.ArgumentParser(description="Run a CPP-facing short-drama tagging Job smoke test.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--api-prefix", default="/api/v1/ai-jobs")
    parser.add_argument("--service-api-key", default="dev-service-key")
    parser.add_argument("--caller-id", default="cpp")
    parser.add_argument("--client-request-id-prefix", default=f"cpp-tagging-smoke-{int(time.time())}")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--request-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--callback-url", help="Optional CPP callback URL to include in the create request.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input-json", action="append", type=Path, dest="input_jsons")
    source.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument(
        "--case",
        action="append",
        dest="case_names",
        help="POC t_book_id to run. Repeatable. Use --case all to run every discovered case.",
    )
    parser.add_argument("--list-cases", action="store_true", help="List discovered POC cases and exit.")
    parser.add_argument("--keep-going", action="store_true", help="Continue running remaining cases after a failure.")
    args = parser.parse_args()

    if args.poll_interval <= 0:
        parser.error("--poll-interval must be > 0")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be > 0")
    if args.request_timeout_seconds <= 0:
        parser.error("--request-timeout-seconds must be > 0")

    input_dir = args.input_dir if args.input_dir.is_absolute() else ROOT_DIR / args.input_dir
    input_jsons = None
    if args.input_jsons:
        input_jsons = [path if path.is_absolute() else ROOT_DIR / path for path in args.input_jsons]

    try:
        if args.list_cases:
            cases = load_cases_from_paths(input_jsons or discover_case_paths(input_dir))
            print("discovered cases:")
            for case in cases:
                print(json.dumps(case_summary(case), ensure_ascii=False, sort_keys=True))
            raise SystemExit(0)
        cases = select_cases(input_dir=input_dir, input_jsons=input_jsons, case_names=args.case_names)
    except ValueError as exc:
        parser.error(str(exc))

    return Config(
        base_url=args.base_url.rstrip("/"),
        api_prefix=args.api_prefix.rstrip("/"),
        service_api_key=args.service_api_key,
        caller_id=args.caller_id,
        client_request_id_prefix=args.client_request_id_prefix,
        poll_interval=args.poll_interval,
        timeout_seconds=args.timeout_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        callback_url=args.callback_url,
        keep_going=args.keep_going,
    ), cases


def main() -> int:
    config, cases = parse_args()
    headers = {
        "Authorization": f"Bearer {config.service_api_key}",
        "X-AI-Service-Caller-ID": config.caller_id,
    }
    print("base_url:", config.base_url)
    print("case_count:", len(cases))
    print("cases:", ", ".join(case.payload["job_params"]["t_book_id"] for case in cases))

    try:
        with httpx.Client(base_url=config.base_url, timeout=config.request_timeout_seconds) as client:
            health = client.get("/health")
            raise_for_status_with_body(health)
            print("health:", health.json())

            models = client.get(api_path(config, "/models"), headers=headers)
            if models.status_code == 401:
                print("authentication failed on /models: pass --service-api-key", file=sys.stderr)
                return 1
            raise_for_status_with_body(models)
            print("default_model_id:", models.json().get("default_model_id"))

            failures = 0
            for index, case in enumerate(cases, start=1):
                if index > 1:
                    print()
                if run_case(client, config, headers, case, index, len(cases)):
                    continue
                failures += 1
                if not config.keep_going:
                    break
    except httpx.HTTPError as exc:
        print(f"HTTP request failed: {exc}", file=sys.stderr)
        return 1

    if failures:
        print(f"cpp short drama tagging smoke failed: {failures}/{len(cases)} case(s)", file=sys.stderr)
        return 1
    print(f"cpp short drama tagging smoke passed: {len(cases)} case(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
