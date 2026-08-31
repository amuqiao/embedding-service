from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

# 1. Personal China account.
# If poc/asset-vector/asset_vector_poc.py works with your personal .env,
# copy that DASHSCOPE_API_KEY value here.
PERSONAL_CN_DASHSCOPE_API_KEY = "sk-ws-H.EXREXMR.OoMP.MEYCIQDIG_97wJNBExk_EMXHxu_mrYMjHPgA8l354pYIf_jXIwIhAIF3UbEGxQGH2DoCqMnJcvCNudHiYvGBfcHPUy7PcohR"
PERSONAL_CN_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

# 2. Personal International account.
PERSONAL_INTL_DASHSCOPE_API_KEY = "sk-ws-H.EXREXMR.OoMP.MEYCIQDIG_97wJNBExk_EMXHxu_mrYMjHPgA8l354pYIf_jXIwIhAIF3UbEGxQGH2DoCqMnJcvCNudHiYvGBfcHPUy7PcohR"
PERSONAL_INTL_DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"

# 3. Company China account.
# Use the base URL that belongs to the same company key or workspace.
# Example workspace native URL:
#   https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1
COMPANY_CN_DASHSCOPE_API_KEY = "sk-35bbe2fa9fa34c14a624216356dea085"
COMPANY_CN_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

# 4. Company International account.
# Example workspace native URL:
#   https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1
COMPANY_INTL_DASHSCOPE_API_KEY = "sk-dc4a227832cb47b08c195793142569af"
COMPANY_INTL_DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"

# 5. Probe settings. The default target "all" tests all four targets together.
DEFAULT_TARGET = "all"
# Each item is (model_name, requested_dimension).
# For connectivity checks, keep requested_dimension=None so the provider default is used.
MODELS: tuple[tuple[str, int | None], ...] = (
    ("qwen3-vl-embedding", None),
    ("tongyi-embedding-vision-plus", None),
    ("tongyi-embedding-vision-flash", None),
    ("multimodal-embedding-v1", None),
    ("tongyi-embedding-vision-plus-2026-03-06", None),
    ("tongyi-embedding-vision-flash-2026-03-06", None),
)
RERANK_MODELS: tuple[str, ...] = (
    "qwen3-vl-rerank",
)
DEFAULT_TEXT = "通用多模态表征模型示例"
DEFAULT_IMAGE_URL = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/256_1.png"
MULTIMODAL_EMBEDDING_PATH = "/services/embeddings/multimodal-embedding/multimodal-embedding"
RERANK_PATH = "/services/rerank/text-rerank/text-rerank"


@dataclass(frozen=True)
class ProbeTarget:
    name: str
    label: str
    api_key: str
    base_url: str


TARGETS: dict[str, ProbeTarget] = {
    "personal_cn": ProbeTarget(
        name="personal_cn",
        label="个人国内",
        api_key=PERSONAL_CN_DASHSCOPE_API_KEY,
        base_url=PERSONAL_CN_DASHSCOPE_BASE_URL,
    ),
    "personal_intl": ProbeTarget(
        name="personal_intl",
        label="个人国际",
        api_key=PERSONAL_INTL_DASHSCOPE_API_KEY,
        base_url=PERSONAL_INTL_DASHSCOPE_BASE_URL,
    ),
    "company_cn": ProbeTarget(
        name="company_cn",
        label="公司国内",
        api_key=COMPANY_CN_DASHSCOPE_API_KEY,
        base_url=COMPANY_CN_DASHSCOPE_BASE_URL,
    ),
    "company_intl": ProbeTarget(
        name="company_intl",
        label="公司国际",
        api_key=COMPANY_INTL_DASHSCOPE_API_KEY,
        base_url=COMPANY_INTL_DASHSCOPE_BASE_URL,
    ),
}


class ProbeError(RuntimeError):
    pass


def _selected_targets(target: str) -> list[ProbeTarget]:
    if target == "all":
        return [
            TARGETS["personal_cn"],
            TARGETS["personal_intl"],
            TARGETS["company_cn"],
            TARGETS["company_intl"],
        ]
    return [TARGETS[target]]


def _validate_api_key(target: ProbeTarget) -> str:
    api_key = target.api_key.strip()
    if not api_key or api_key.startswith("PASTE_"):
        raise ProbeError(f"请先在脚本顶部填写 {target.name} 的 DASHSCOPE API Key")
    return api_key


def _native_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized or normalized.startswith("PASTE_"):
        raise ProbeError(f"请先在脚本顶部填写 base_url: {base_url}")
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ProbeError(f"base_url 必须是 HTTPS 绝对 URL: {base_url}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProbeError(f"base_url 不能包含账号、密码、query 或 fragment: {base_url}")
    if normalized.endswith("/compatible-mode/v1"):
        return normalized[: -len("/compatible-mode/v1")] + "/api/v1"
    if normalized.endswith("/api/v1"):
        return normalized
    raise ProbeError(f"base_url 必须以 /compatible-mode/v1 或 /api/v1 结尾: {base_url}")


def _failure_reason(
    *,
    http_status: int | None = None,
    provider_code: object = None,
    provider_message: object = None,
    default: str = "provider_error",
) -> str:
    code = str(provider_code or "")
    message = str(provider_message or "")
    lowered = f"{code} {message}".lower()
    if http_status in {401, 403} and ("invalidapikey" in lowered or "invalid api-key" in lowered):
        return "invalid_api_key"
    if code == "Model.AccessDenied" or "access denied" in lowered:
        return "permission_denied"
    if code == "InvalidParameter" and "model not exist" in lowered:
        return "model_not_found"
    if http_status == 404:
        return "bad_endpoint"
    if http_status in {401, 403}:
        return "auth_failed"
    return default


def _contents(kind: str, *, text: str, image_url: str) -> list[dict[str, str]]:
    if kind == "text":
        return [{"text": text}]
    if kind == "image":
        return [{"image": image_url}]
    if kind == "fused":
        return [{"text": text, "image": image_url}]
    raise ProbeError(f"unsupported kind: {kind}")


def _embedding_summary(payload: dict[str, Any]) -> dict[str, Any]:
    output = payload.get("output")
    embeddings = output.get("embeddings") if isinstance(output, dict) else None
    if not isinstance(embeddings, list):
        raise ProbeError("response output.embeddings must be a list")

    rows: list[dict[str, Any]] = []
    for item in embeddings:
        if not isinstance(item, dict):
            continue
        embedding = item.get("embedding")
        if not isinstance(embedding, list):
            continue
        rows.append(
            {
                "type": item.get("type"),
                "dimension": len(embedding),
                "preview": embedding[:5],
            }
        )
    if not rows:
        raise ProbeError("response output.embeddings does not contain embedding vectors")
    return {"embedding_count": len(rows), "embeddings": rows}


def _rerank_summary(payload: dict[str, Any]) -> dict[str, Any]:
    output = payload.get("output")
    results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(results, list):
        raise ProbeError("response output.results must be a list")

    rows: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "index": item.get("index"),
                "relevance_score": item.get("relevance_score"),
            }
        )
    if not rows:
        raise ProbeError("response output.results does not contain rerank results")
    return {"rerank_result_count": len(rows), "rerank_results": rows}


def _probe(
    target: ProbeTarget,
    *,
    model: str,
    dimension: int | None,
    kind: str,
    text: str,
    image_url: str,
    timeout: float,
) -> dict[str, Any]:
    api_key = _validate_api_key(target)
    native_base_url = _native_base_url(target.base_url)
    endpoint = f"{native_base_url}{MULTIMODAL_EMBEDDING_PATH}"
    request_body = {
        "model": model,
        "input": {"contents": _contents(kind, text=text, image_url=image_url)},
    }
    if dimension is not None:
        request_body["parameters"] = {"dimension": dimension}

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=request_body,
            )
    except httpx.HTTPError as exc:
        return {
            "target": target.name,
            "label": target.label,
            "supported": False,
            "ok": False,
            "base_url": target.base_url,
            "native_base_url": native_base_url,
            "endpoint": endpoint,
            "failure_reason": "network_error",
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }

    try:
        payload: Any = response.json()
    except ValueError:
        payload = None

    base_result = {
        "target": target.name,
        "label": target.label,
        "model": model,
        "dimension_requested": dimension,
        "base_url": target.base_url,
        "native_base_url": native_base_url,
        "endpoint": endpoint,
        "http_status": response.status_code,
    }
    if response.status_code >= 400:
        code = payload.get("code") if isinstance(payload, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else None
        return {
            **base_result,
            "supported": False,
            "ok": False,
            "failure_reason": _failure_reason(
                http_status=response.status_code,
                provider_code=code,
                provider_message=message,
            ),
            "provider_code": code,
            "provider_message": message,
        }

    try:
        summary = _embedding_summary(payload if isinstance(payload, dict) else {})
    except ProbeError as exc:
        return {
            **base_result,
            "supported": False,
            "ok": False,
            "failure_reason": "bad_response",
            "provider_message": str(exc),
        }

    dimensions = [row["dimension"] for row in summary["embeddings"]]
    supported = bool(dimensions) and (dimension is None or dimension in dimensions)
    return {
        **base_result,
        "supported": supported,
        "ok": supported,
        "kind": kind,
        **summary,
    }


def _probe_rerank(
    target: ProbeTarget,
    *,
    model: str,
    text: str,
    image_url: str,
    timeout: float,
) -> dict[str, Any]:
    api_key = _validate_api_key(target)
    native_base_url = _native_base_url(target.base_url)
    endpoint = f"{native_base_url}{RERANK_PATH}"
    request_body = {
        "model": model,
        "input": {
            "query": {"image": image_url},
            "documents": [
                {"image": image_url},
                {"text": text},
            ],
        },
        "parameters": {
            "return_documents": False,
            "top_n": 2,
        },
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=request_body,
            )
    except httpx.HTTPError as exc:
        return {
            "target": target.name,
            "label": target.label,
            "supported": False,
            "ok": False,
            "base_url": target.base_url,
            "native_base_url": native_base_url,
            "endpoint": endpoint,
            "failure_reason": "network_error",
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }

    try:
        payload: Any = response.json()
    except ValueError:
        payload = None

    base_result = {
        "target": target.name,
        "label": target.label,
        "model": model,
        "base_url": target.base_url,
        "native_base_url": native_base_url,
        "endpoint": endpoint,
        "http_status": response.status_code,
    }
    if response.status_code >= 400:
        code = payload.get("code") if isinstance(payload, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else None
        return {
            **base_result,
            "supported": False,
            "ok": False,
            "failure_reason": _failure_reason(
                http_status=response.status_code,
                provider_code=code,
                provider_message=message,
            ),
            "provider_code": code,
            "provider_message": message,
        }

    try:
        summary = _rerank_summary(payload if isinstance(payload, dict) else {})
    except ProbeError as exc:
        return {
            **base_result,
            "supported": False,
            "ok": False,
            "failure_reason": "bad_response",
            "provider_message": str(exc),
        }

    return {
        **base_result,
        "supported": True,
        "ok": True,
        **summary,
    }


def _returned_dimensions(result: dict[str, Any]) -> list[int]:
    embeddings = result.get("embeddings")
    if not isinstance(embeddings, list):
        return []
    dimensions = []
    for item in embeddings:
        if isinstance(item, dict) and isinstance(item.get("dimension"), int):
            dimensions.append(item["dimension"])
    return dimensions


def _print_visual_summary(
    embedding_results: list[dict[str, Any]],
    rerank_results: list[dict[str, Any]],
) -> None:
    seen_targets: list[str] = []
    for result in [*embedding_results, *rerank_results]:
        if result["target"] not in seen_targets:
            seen_targets.append(result["target"])

    print("\nTested Embedding Models")
    for index, (model, _dimension) in enumerate(MODELS, start=1):
        print(f"- E{index}: {model}")

    print("\nTested Rerank Models")
    for index, model in enumerate(RERANK_MODELS, start=1):
        print(f"- R{index}: {model}")

    print("\nImage Search Capability By Account")
    print("| account | embedding_supported | rerank_supported |")
    print("|---|---|---|")
    for target_name in seen_targets:
        target_embedding_results = [
            result for result in embedding_results if result["target"] == target_name
        ]
        target_rerank_results = [result for result in rerank_results if result["target"] == target_name]
        first = (target_embedding_results or target_rerank_results)[0]

        embedding_supported = []
        for result in target_embedding_results:
            if not result["supported"]:
                continue
            dimensions = _returned_dimensions(result)
            dimensions_text = ",".join(str(dimension) for dimension in dimensions) or "unknown"
            embedding_supported.append(f"{result['model']} ({dimensions_text}维)")

        rerank_supported = [
            result["model"] for result in target_rerank_results if result["supported"]
        ]

        embedding_text = "<br>".join(embedding_supported) if embedding_supported else "none"
        rerank_text = "<br>".join(rerank_supported) if rerank_supported else "none"
        print(f"| {target_name} ({first['label']}) | {embedding_text} | {rerank_text} |")


def _print_result(
    embedding_results: list[dict[str, Any]],
    rerank_results: list[dict[str, Any]],
) -> None:
    print("Tongyi Image Search Capability Probe")
    print(f"- embedding_model_count: {len(MODELS)}")
    print(f"- rerank_model_count: {len(RERANK_MODELS)}")

    seen_targets: list[str] = []
    for result in embedding_results:
        if result["target"] not in seen_targets:
            seen_targets.append(result["target"])

    for target_name in seen_targets:
        target_results = [result for result in embedding_results if result["target"] == target_name]
        first = target_results[0]
        supported_count = sum(1 for result in target_results if result["supported"])

        print(f"\n[embedding:{first['target']}] {first['label']}")
        print(f"- base_url: {first['base_url']}")
        print(f"- native_base_url: {first['native_base_url']}")
        print(f"- supported_models: {supported_count}/{len(target_results)}")

        for result in target_results:
            status = "SUPPORTED" if result["supported"] else "UNSUPPORTED"
            dimension_requested = result.get("dimension_requested")
            dimension_text = (
                "provider_default" if dimension_requested is None else str(dimension_requested)
            )
            print(f"  - {status}: {result['model']} dimension={dimension_text}")
            if result["supported"]:
                dimensions = [item["dimension"] for item in result["embeddings"]]
                types = [item["type"] for item in result["embeddings"]]
                print(f"    returned_types: {types}")
                print(f"    returned_dimensions: {dimensions}")
                print(f"    preview: {result['embeddings'][0]['preview']}")
                continue

            if result.get("failure_reason"):
                print(f"    failure_reason: {result['failure_reason']}")
            if "http_status" in result:
                print(f"    http_status: {result['http_status']}")
            if result.get("provider_code"):
                print(f"    provider_code: {result['provider_code']}")
            if result.get("provider_message"):
                print(f"    provider_message: {result['provider_message']}")
            if result.get("error_type"):
                print(f"    error_type: {result['error_type']}")
                print(f"    error_message: {result.get('error_message', '')}")

    seen_rerank_targets: list[str] = []
    for result in rerank_results:
        if result["target"] not in seen_rerank_targets:
            seen_rerank_targets.append(result["target"])

    for target_name in seen_rerank_targets:
        target_results = [result for result in rerank_results if result["target"] == target_name]
        first = target_results[0]
        supported_count = sum(1 for result in target_results if result["supported"])

        print(f"\n[rerank:{first['target']}] {first['label']}")
        print(f"- base_url: {first['base_url']}")
        print(f"- native_base_url: {first['native_base_url']}")
        print(f"- supported_models: {supported_count}/{len(target_results)}")

        for result in target_results:
            status = "SUPPORTED" if result["supported"] else "UNSUPPORTED"
            print(f"  - {status}: {result['model']}")
            if result["supported"]:
                print(f"    rerank_result_count: {result['rerank_result_count']}")
                print(f"    preview: {result['rerank_results'][:2]}")
                continue

            if result.get("failure_reason"):
                print(f"    failure_reason: {result['failure_reason']}")
            if "http_status" in result:
                print(f"    http_status: {result['http_status']}")
            if result.get("provider_code"):
                print(f"    provider_code: {result['provider_code']}")
            if result.get("provider_message"):
                print(f"    provider_message: {result['provider_message']}")
            if result.get("error_type"):
                print(f"    error_type: {result['error_type']}")
                print(f"    error_message: {result.get('error_message', '')}")

    _print_visual_summary(embedding_results, rerank_results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate which configured DashScope targets can call image-search embedding and rerank models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Edit these constants at the top of this script:
  PERSONAL_CN_DASHSCOPE_API_KEY
  PERSONAL_CN_DASHSCOPE_BASE_URL
  PERSONAL_INTL_DASHSCOPE_API_KEY
  PERSONAL_INTL_DASHSCOPE_BASE_URL
  COMPANY_CN_DASHSCOPE_API_KEY
  COMPANY_CN_DASHSCOPE_BASE_URL
  COMPANY_INTL_DASHSCOPE_API_KEY
  COMPANY_INTL_DASHSCOPE_BASE_URL

Company base URLs are placeholders on purpose. Fill the exact public or workspace base URL that belongs to the same company key:
  https://dashscope.aliyuncs.com/api/v1
  https://dashscope-intl.aliyuncs.com/api/v1
  https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1
  https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1

Run personal China, personal International, company China, and company International together:
  uv run python poc/tongyi_embedding_vision_flash_probe.py --confirm-cost

Run one target:
  uv run python poc/tongyi_embedding_vision_flash_probe.py --target personal_cn --confirm-cost
  uv run python poc/tongyi_embedding_vision_flash_probe.py --target personal_intl --confirm-cost
  uv run python poc/tongyi_embedding_vision_flash_probe.py --target company_cn --confirm-cost
  uv run python poc/tongyi_embedding_vision_flash_probe.py --target company_intl --confirm-cost

For this image-search probe, keep the base URL as native /api/v1.
If you paste a /compatible-mode/v1 URL, the script converts it to /api/v1 before calling DashScope native API.

Edit MODELS and RERANK_MODELS at the top of this script to add or remove candidate model names.
Each configured embedding model and rerank model sends one real request per selected target.
""",
    )
    parser.add_argument(
        "--target",
        choices=("all", "personal_cn", "personal_intl", "company_cn", "company_intl"),
        default=DEFAULT_TARGET,
    )
    parser.add_argument("--kind", choices=("text", "image", "fused"), default="text")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--image-url", default=DEFAULT_IMAGE_URL)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--confirm-cost",
        action="store_true",
        help="Required because this sends real DashScope requests.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_cost:
        print("ERROR: pass --confirm-cost to send real DashScope requests", file=sys.stderr)
        return 2

    try:
        embedding_results = []
        rerank_results = []
        for target in _selected_targets(args.target):
            for model, dimension in MODELS:
                embedding_results.append(
                    _probe(
                        target,
                        model=model,
                        dimension=dimension,
                        kind=args.kind,
                        text=args.text,
                        image_url=args.image_url,
                        timeout=args.timeout,
                    )
                )
            for model in RERANK_MODELS:
                rerank_results.append(
                    _probe_rerank(
                        target,
                        model=model,
                        text=args.text,
                        image_url=args.image_url,
                        timeout=args.timeout,
                    )
                )
    except ProbeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    supported_count = sum(
        1 for result in [*embedding_results, *rerank_results] if result["supported"]
    )
    total_count = len(embedding_results) + len(rerank_results)
    payload = {
        "ok": supported_count > 0,
        "supported_count": supported_count,
        "total_count": total_count,
        "embedding_results": embedding_results,
        "rerank_results": rerank_results,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_result(embedding_results, rerank_results)
    return 0 if payload["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
