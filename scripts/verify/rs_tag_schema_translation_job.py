"""Smoke test the real RS tag-schema translation Job API.

This script calls the normal /api/v1/ai-jobs/jobs interface. It does not use
mock routes or fixture responses; the running worker will call the configured
model provider.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def load_dotenv_value(key: str) -> str | None:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name == key:
            return value.strip().strip('"').strip("'")
    return None


def env_value(key: str, default: str | None = None) -> str | None:
    return os.getenv(key) or load_dotenv_value(key) or default


def default_base_url() -> str:
    configured = env_value("BASE_URL") or env_value("API_URL")
    if configured:
        return configured.rstrip("/")
    host = env_value("API_HOST", "127.0.0.1") or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = env_value("API_PORT", "8100") or "8100"
    return f"http://{host}:{port}"


@dataclass(frozen=True)
class Config:
    base_url: str
    api_prefix: str
    service_api_key: str
    caller_id: str
    client_request_id: str
    expected_model: str
    poll_interval: float
    timeout_seconds: int
    request_timeout_seconds: float
    keep_going: bool


@dataclass(frozen=True)
class SmokeCase:
    name: str
    job_params: dict[str, Any]
    metadata: dict[str, Any]


BASE_METADATA = {
    "source_service": "rs",
    "business_scene": "tag_schema_translation",
    "smoke_test": "rs_tag_schema_translation",
}

DEFAULT_CASE_NAME = "audience-basic"


def _case(
    name: str,
    labels: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> SmokeCase:
    return SmokeCase(
        name=name,
        job_params={"labels": labels},
        metadata=metadata or {},
    )


def builtin_cases() -> dict[str, SmokeCase]:
    return {
        "audience-basic": _case(
            "audience-basic",
            [
                {
                    "label_id": "rs-smoke-audience-male",
                    "source_language": "zh",
                    "target_languages": ["en", "es", "pt"],
                    "display_name": "男频",
                    "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心。",
                },
                {
                    "label_id": "rs-smoke-audience-female",
                    "source_language": "zh",
                    "target_languages": ["en", "ko"],
                    "display_name": "女频",
                    "definition": "核心受众为女性群体，叙事视角、人物塑造、情感逻辑以女性主角为核心。",
                },
            ],
        ),
        "genre-emotion": _case(
            "genre-emotion",
            [
                {
                    "label_id": "rs-smoke-genre-rebirth",
                    "source_language": "zh",
                    "target_languages": ["en", "es", "pt", "in", "th"],
                    "display_name": "重生逆袭",
                    "definition": "主角带着前世记忆或失败经验重来一次，通过信息差完成身份、事业或情感层面的反转。",
                },
                {
                    "label_id": "rs-smoke-emotion-satisfy",
                    "source_language": "zh",
                    "target_languages": ["en", "es", "pt"],
                    "display_name": "爽感强",
                    "definition": "剧情持续提供压抑后的反击、误解澄清、身份揭露或惩恶扬善等高满足感情绪反馈。",
                },
                {
                    "label_id": "rs-smoke-topic-family",
                    "source_language": "zh",
                    "target_languages": ["en", "ko", "ja"],
                    "display_name": "家庭伦理",
                    "definition": "围绕婚姻、亲子、赡养、婆媳或家族利益冲突展开，强调家庭关系中的责任与情感拉扯。",
                },
            ],
        ),
        "traditional-source": _case(
            "traditional-source",
            [
                {
                    "label_id": "rs-smoke-zh-tw-contract-marriage",
                    "source_language": "zh-TW",
                    "target_languages": ["en", "ko", "ja"],
                    "display_name": "契約婚姻",
                    "definition": "男女主角基於利益、身份或家庭壓力達成婚姻協議，後續在相處中產生真實情感。",
                }
            ],
        ),
    }


def load_cases_file(path: Path) -> tuple[SmokeCase, ...]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read --cases-file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in --cases-file {path}: {exc}") from exc

    if isinstance(body, dict):
        if set(body) != {"cases"}:
            raise ValueError("--cases-file object must contain exactly one key: cases")
        case_items = body["cases"]
    else:
        case_items = body

    if not isinstance(case_items, list) or not case_items:
        raise ValueError("--cases-file must provide a non-empty cases array")

    cases: list[SmokeCase] = []
    for index, item in enumerate(case_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"cases[{index}] must be an object")
        if not set(item).issubset({"name", "job_params", "metadata"}):
            raise ValueError(f"cases[{index}] only supports keys: name, job_params, metadata")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"cases[{index}].name must be a non-empty string")
        job_params = item.get("job_params")
        if not isinstance(job_params, dict):
            raise ValueError(f"cases[{index}].job_params must be an object")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError(f"cases[{index}].metadata must be an object")
        cases.append(
            SmokeCase(name=name.strip(), job_params=copy.deepcopy(job_params), metadata=copy.deepcopy(metadata))
        )
    return tuple(cases)


def select_builtin_cases(case_names: list[str] | None) -> tuple[SmokeCase, ...]:
    available = builtin_cases()
    selected = case_names or [DEFAULT_CASE_NAME]
    if "all" in selected:
        if len(selected) != 1:
            raise ValueError("--case all cannot be combined with other --case values")
        return tuple(copy.deepcopy(case) for case in available.values())
    unknown = [name for name in selected if name not in available]
    if unknown:
        raise ValueError(f"unknown --case: {', '.join(unknown)}; available: {', '.join(available)}")
    return tuple(copy.deepcopy(available[name]) for name in selected)


def case_client_request_id(config: Config, case: SmokeCase, index: int, total: int) -> str:
    if total == 1:
        value = config.client_request_id
    else:
        suffix = re.sub(r"[^A-Za-z0-9_.:-]+", "-", case.name).strip("-") or f"case-{index}"
        value = f"{config.client_request_id}-{suffix}"
    if len(value) > 255:
        raise ValueError(f"client_request_id too long for case {case.name}: {len(value)} > 255")
    return value


def build_payload(config: Config, case: SmokeCase, client_request_id: str) -> dict[str, Any]:
    metadata = {
        **BASE_METADATA,
        **case.metadata,
        "smoke_case": case.name,
    }
    return {
        "client_request_id": client_request_id,
        "job_type": "short_drama.tag_schema.translation",
        "job_params": copy.deepcopy(case.job_params),
        "metadata": metadata,
    }


def example_cases_file() -> dict[str, Any]:
    return {
        "cases": [
            {
                "name": "custom-audience",
                "job_params": {
                    "labels": [
                        {
                            "label_id": "custom-label",
                            "source_language": "zh",
                            "target_languages": ["en", "es"],
                            "display_name": "男频",
                            "definition": "核心受众为男性群体。",
                        }
                    ]
                },
                "metadata": {"source_service": "rs"},
            }
        ]
    }


def api_path(config: Config, suffix: str) -> str:
    return f"{config.api_prefix}{suffix}"


def require_translation_contract(params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    if body.get("job_type") != "short_drama.tag_schema.translation":
        raise AssertionError(f"unexpected job_type: {body.get('job_type')}")
    if body.get("status") != "succeeded":
        raise AssertionError(f"job did not succeed: {body.get('status')}")
    if body.get("error") is not None:
        raise AssertionError(f"succeeded job returned error: {body.get('error')}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise AssertionError(f"result must be an object: {result!r}")
    if set(result) != {"artifacts", "signals"}:
        raise AssertionError(f"result keys changed: {sorted(result)}")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list):
        raise AssertionError("result.artifacts must be a list")
    signals = result.get("signals")
    if not isinstance(signals, dict):
        raise AssertionError("result.signals must be an object")
    if set(signals) != {"source_schema_hash", "translated_schemas_hash"}:
        raise AssertionError(f"result.signals keys changed: {sorted(signals)}")
    for key in ("source_schema_hash", "translated_schemas_hash"):
        value = signals.get(key)
        if not isinstance(value, str) or not HASH_RE.fullmatch(value):
            raise AssertionError(f"result.signals.{key} must match sha256:<64 lowercase hex>")
    labels = params["labels"]
    if len(artifacts) != len(labels):
        raise AssertionError(f"artifact count mismatch: expected={len(labels)} actual={len(artifacts)}")
    for index, label in enumerate(labels):
        artifact = artifacts[index]
        if not isinstance(artifact, dict):
            raise AssertionError(f"artifact at index {index} must be an object")
        if set(artifact) != {"label_id", "langs"}:
            raise AssertionError(f"artifact keys changed at index {index}: {sorted(artifact)}")
        if artifact["label_id"] != label["label_id"]:
            raise AssertionError(
                f"label_id changed at index {index}: expected={label['label_id']} actual={artifact['label_id']}"
            )
        if not isinstance(artifact["langs"], dict):
            raise AssertionError(f"langs for {label['label_id']} must be an object")
        actual_languages = list(artifact["langs"])
        if set(actual_languages) != set(label["target_languages"]):
            raise AssertionError(
                f"languages changed for {label['label_id']}: expected={label['target_languages']} actual={actual_languages}"
            )
        for language in label["target_languages"]:
            entry = artifact["langs"].get(language)
            if not isinstance(entry, dict):
                raise AssertionError(f"translation entry missing for {label['label_id']}:{language}")
            if set(entry) != {"name", "definition"}:
                raise AssertionError(
                    f"translation entry keys changed for {label['label_id']}:{language}: {sorted(entry)}"
                )
            for field in ("name", "definition"):
                value = entry.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise AssertionError(f"{label['label_id']}:{language}.{field} must be a non-empty string")
    return result


def parse_args() -> tuple[Config, tuple[SmokeCase, ...]]:
    parser = argparse.ArgumentParser(description="Run a real RS tag-schema translation Job smoke test.")
    parser.add_argument("--base-url", default=default_base_url())
    parser.add_argument("--api-prefix", default=env_value("SERVICE_API_PREFIX", "/api/v1/ai-jobs"))
    parser.add_argument("--service-api-key", default=env_value("SERVICE_API_KEY", "dev-service-key"))
    parser.add_argument("--caller-id", default="rs")
    parser.add_argument("--client-request-id", default=f"rs-translation-smoke-{int(time.time())}")
    parser.add_argument(
        "--expected-model",
        default="gpt-5.5",
        help="Expected /models.default_model_id. Pass an empty string to skip this default-model check.",
    )
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--request-timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--case",
        action="append",
        dest="case_names",
        help="Built-in case to run. Repeatable. Use --case all to run every built-in case.",
    )
    parser.add_argument(
        "--cases-file",
        type=Path,
        help=(
            "JSON file with {\"cases\":[{\"name\":\"...\",\"job_params\":{...},\"metadata\":{...}}]}. "
            "Cannot be combined with --case."
        ),
    )
    parser.add_argument("--list-cases", action="store_true", help="List built-in cases and exit.")
    parser.add_argument("--print-cases-file-example", action="store_true", help="Print a custom cases JSON example and exit.")
    parser.add_argument("--keep-going", action="store_true", help="Continue running remaining cases after a case failure.")
    args = parser.parse_args()
    if args.list_cases:
        print("built-in cases:")
        for name in builtin_cases():
            print(f"  {name}")
        raise SystemExit(0)
    if args.print_cases_file_example:
        print(json.dumps(example_cases_file(), ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(0)
    if args.case_names and args.cases_file:
        parser.error("--case cannot be combined with --cases-file")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be > 0")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be > 0")
    if args.request_timeout_seconds <= 0:
        parser.error("--request-timeout-seconds must be > 0")
    try:
        cases = load_cases_file(args.cases_file) if args.cases_file else select_builtin_cases(args.case_names)
    except ValueError as exc:
        parser.error(str(exc))
    return Config(
        base_url=args.base_url.rstrip("/"),
        api_prefix=args.api_prefix.rstrip("/"),
        service_api_key=args.service_api_key,
        caller_id=args.caller_id,
        client_request_id=args.client_request_id,
        expected_model=args.expected_model,
        poll_interval=args.poll_interval,
        timeout_seconds=args.timeout_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        keep_going=args.keep_going,
    ), cases


def run_case(
    client: httpx.Client,
    config: Config,
    headers: dict[str, str],
    case: SmokeCase,
    index: int,
    total: int,
) -> bool:
    client_request_id = case_client_request_id(config, case, index, total)
    payload = build_payload(config, case, client_request_id)
    print(f"case[{index}/{total}]:", case.name)
    print("client_request_id:", client_request_id)
    print("request:", json.dumps(payload, ensure_ascii=False, sort_keys=True))

    created = client.post(api_path(config, "/jobs"), headers=headers, json=payload)
    created.raise_for_status()
    created_body = created.json()
    print("created:", json.dumps(created_body, ensure_ascii=False, sort_keys=True))

    status_url = created_body["status_url"]
    deadline = time.monotonic() + config.timeout_seconds
    while time.monotonic() < deadline:
        status_resp = client.get(status_url, headers=headers)
        status_resp.raise_for_status()
        status_body = status_resp.json()
        progress = status_body.get("progress") or {}
        print("status:", status_body.get("status"), progress.get("percent"), progress.get("stage"))
        if status_body.get("status") == "succeeded":
            try:
                result = require_translation_contract(payload["job_params"], status_body)
            except AssertionError as exc:
                print(f"case failed contract: {case.name}: {exc}", file=sys.stderr)
                return False
            print("result:", json.dumps(result, ensure_ascii=False, sort_keys=True))
            print("case passed:", case.name)
            return True
        if status_body.get("status") == "failed":
            print("final:", json.dumps(status_body, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return False
        time.sleep(config.poll_interval)

    print(f"case timed out after {config.timeout_seconds}s: {case.name}", file=sys.stderr)
    return False


def main() -> int:
    config, cases = parse_args()
    headers = {
        "Authorization": f"Bearer {config.service_api_key}",
        "X-AI-Service-Caller-ID": config.caller_id,
    }
    print("base_url:", config.base_url)
    print("case_count:", len(cases))
    print("cases:", ", ".join(case.name for case in cases))

    with httpx.Client(base_url=config.base_url, timeout=config.request_timeout_seconds) as client:
        health = client.get("/health")
        health.raise_for_status()
        print("health:", health.json())

        models = client.get(api_path(config, "/models"), headers=headers)
        if models.status_code == 401:
            print(
                "authentication failed on /models: pass the target environment key with --service-api-key",
                file=sys.stderr,
            )
            return 1
        models.raise_for_status()
        models_body = models.json()
        print("default_model_id:", models_body.get("default_model_id"))
        if config.expected_model and models_body.get("default_model_id") != config.expected_model:
            print(
                f"expected default_model_id={config.expected_model}, got={models_body.get('default_model_id')}",
                file=sys.stderr,
            )
            return 1

        failures = 0
        for index, case in enumerate(cases, start=1):
            if index > 1:
                print()
            if run_case(client, config, headers, case, index, len(cases)):
                continue
            failures += 1
            if not config.keep_going:
                break

    if failures:
        print(f"rs tag schema translation smoke failed: {failures}/{len(cases)} case(s)", file=sys.stderr)
        return 1
    print(f"rs tag schema translation smoke passed: {len(cases)} case(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
