from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _model_row(model) -> dict[str, Any]:
    return {
        "id": model.id,
        "enabled": model.enabled,
        "provider": model.public_provider,
        "model_type": model.model_type,
        "capabilities": list(model.capabilities),
        "routes": {
            capability: {
                "provider": route.provider,
                "adapter": route.adapter,
                "provider_model": route.provider_model,
                "adapter_model": route.adapter_model,
                "pricing_ref": route.pricing_ref,
                "requires_env": list(route.requires_env),
                "route_config_hash": route.config_hash,
            }
            for capability, route in sorted(model.routes.items())
        },
    }


def _resolved_row(selection) -> dict[str, Any]:
    resolved = selection.resolved_model
    return {
        "model_id": resolved.model_id,
        "capability": resolved.capability,
        "provider": resolved.provider,
        "adapter": resolved.adapter,
        "provider_model": resolved.provider_model,
        "adapter_model": resolved.adapter_model,
        "pricing_ref": resolved.pricing_ref,
        "route_config_hash": resolved.route_config_hash,
        "source_policy": selection.source_policy,
    }


def _provider_rows() -> list[dict[str, object]]:
    from app.ai.providers.registry import provider_snapshot

    return provider_snapshot()


def command_check(args: argparse.Namespace) -> int:
    from app.ai.catalog.registry import all_default_model_ids, all_model_catalog_entries, validate_model_catalog
    from app.jobs.types.register import register_all_job_types

    register_all_job_types()
    validate_model_catalog()
    models = all_model_catalog_entries()
    payload = {
        "status": "ok",
        "providers": _provider_rows(),
        "catalog": {
            "model_count": len(models),
            "enabled_model_count": sum(1 for model in models if model.enabled),
            "default_model_ids": all_default_model_ids(),
        },
    }
    if args.json:
        _print_json(payload)
        return 0
    print("AI Providers")
    for provider in payload["providers"]:
        print(
            f"- {provider['provider']}: api_key_configured={provider['api_key_configured']} "
            f"base_url_configured={provider['base_url_configured']}"
        )
    print("")
    print("Catalog")
    print(f"- model_count: {payload['catalog']['model_count']}")
    print(f"- enabled_model_count: {payload['catalog']['enabled_model_count']}")
    for capability, model_id in payload["catalog"]["default_model_ids"].items():
        print(f"- default {capability}: {model_id}")
    return 0


def command_models(args: argparse.Namespace) -> int:
    from app.ai.catalog.registry import all_default_model_ids, all_model_catalog_entries, list_models_response

    raw_models = all_model_catalog_entries()
    job_default_model_id = None
    if args.job_type:
        from app.jobs.types.register import register_all_job_types

        register_all_job_types()
        job_models = list_models_response(job_type=args.job_type)
        job_model_ids = {model.id for model in job_models.models}
        job_default_model_id = job_models.default_model_id
        raw_models = [model for model in raw_models if model.id in job_model_ids]
    if args.provider:
        provider = args.provider.strip().lower()
        raw_models = [
            model
            for model in raw_models
            if model.public_provider.lower() == provider
            or any(route.provider.lower() == provider for route in model.routes.values())
        ]
    if args.capability:
        capability = args.capability.strip()
        raw_models = [model for model in raw_models if capability in model.capabilities]
    models = [_model_row(model) for model in raw_models]
    payload = {
        "default_model_ids": all_default_model_ids(),
        "job_type": args.job_type,
        "job_default_model_id": job_default_model_id,
        "provider": args.provider,
        "capability": args.capability,
        "models": models,
    }
    if args.json:
        _print_json(payload)
        return 0
    print("Default Models")
    for capability, model_id in payload["default_model_ids"].items():
        print(f"- {capability}: {model_id}")
    if args.job_type:
        print(f"- job {args.job_type}: {job_default_model_id}")
    print("")
    print("Models")
    for model in models:
        print(f"- {model['id']} ({model['model_type']}, enabled={model['enabled']})")
        print(f"  capabilities: {', '.join(model['capabilities'])}")
        print(f"  provider: {model['provider']}")
    return 0


def command_resolve(args: argparse.Namespace) -> int:
    from app.ai.resolver import resolve_model
    from app.jobs.types.register import register_all_job_types

    register_all_job_types()
    selection = resolve_model(
        capability=args.capability,
        requested_model_id=args.model_id,
        job_type=args.job_type,
        slot=args.slot,
    )
    payload = _resolved_row(selection)
    if args.json:
        _print_json(payload)
        return 0
    print("Resolved Model")
    for key, value in payload.items():
        print(f"- {key}: {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./scripts/ai-providers.sh",
        description="诊断 AI provider、模型 catalog 和 resolver；默认不访问远端、不产生费用。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
作用域:
  检查当前服务代码注册的 provider、全局 models.yaml、pricing.yaml 和 resolver 结果。

不负责:
  不下载本地模型资产；本地权重归 scripts/models.sh。
  不提交 Job；业务链路验收归 scripts/smoke.sh。
  默认不访问远端 provider，不产生真实模型费用。

配置与环境变量:
  读取 .env / ENV_FILE 后由应用 Settings、app.ai.providers、app.ai.catalog 和 app.ai.resolver 统一解析。
  OPENAI_API_KEY / DASHSCOPE_API_KEY 只作为 route 执行凭证，不作为模型启停开关。
  MODEL_CONFIG_PATH / PRICING_CONFIG_PATH 只选择配置文件。

常用示例:
  ./scripts/ai-providers.sh check
  ./scripts/ai-providers.sh check --json
  ./scripts/ai-providers.sh models --json
  ./scripts/ai-providers.sh models --provider openai --capability text_generation
  ./scripts/ai-providers.sh models --job-type poster_title_image --json
  ./scripts/ai-providers.sh resolve --capability text_generation --json
  ./scripts/ai-providers.sh resolve --job-type poster_title_image --slot generation --capability image_generation --json

Exit Codes:
  0  成功
  2  参数错误
  4  配置、catalog、provider、adapter 或 pricing 校验失败
""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="离线校验 provider registry、model catalog 和 pricing。")
    check.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    check.set_defaults(func=command_check)

    models = subparsers.add_parser("models", help="列出 models.yaml 中的模型和 capability routes。")
    models.add_argument("--provider", default=None, help="按 provider 过滤，例如 openai / dashscope。")
    models.add_argument("--capability", default=None, help="按 capability 过滤，例如 text_generation / embeddings。")
    models.add_argument("--job-type", default=None, help="按 job_type 公开模型策略过滤。")
    models.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    models.set_defaults(func=command_models)

    resolve = subparsers.add_parser("resolve", help="解析一次模型选择会落到哪条 provider/adapter route。")
    resolve.add_argument("--capability", default="text_generation", help="能力名，例如 text_generation / image_generation。")
    resolve.add_argument("--model-id", default=None, help="显式请求的模型 ID；不传时读取 job slot 或全局默认。")
    resolve.add_argument("--job-type", default=None, help="可选 job_type；传入后会应用业务模型策略。")
    resolve.add_argument("--slot", default="default", help="job_type 模型 slot；例如 default / generation / style_probe。")
    resolve.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    resolve.set_defaults(func=command_resolve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", str(exc))
        if code:
            print(f"ERROR {code}: {message}", file=sys.stderr)
            return 4
        print(f"ERROR: {message}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
