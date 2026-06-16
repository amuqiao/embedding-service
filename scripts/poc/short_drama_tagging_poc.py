from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
POC_ROOT = ROOT_DIR / ".data" / "poc" / "short_drama_tagging"
DEFAULT_INPUT_DIR = POC_ROOT / "inputs" / "jobs" / "per_book"
DEFAULT_CONFIG_JSON = POC_ROOT / "config" / "ai_tagging_poc_config.json"
DEFAULT_WORKFLOW_JSON = POC_ROOT / "config" / "workflow_definition.json"
DEFAULT_PROMPT_TEMPLATES_JSON = POC_ROOT / "config" / "prompt_templates.json"
DEFAULT_OUTPUT_DIR = POC_ROOT / "runs" / "latest"
REQUIRED_CATEGORY_IDS = {"000001", "000002", "000003", "000004", "000005", "000006"}
MIN_LABEL_ID_PREFIX_LENGTH = 12
TEMPLATE_VAR_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the short-drama tagging POC from structured CPP/RS input JSON."
    )
    parser.add_argument("--poc-root", type=Path, default=POC_ROOT)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input-json", action="append", type=Path, dest="input_jsons")
    source.add_argument("--input-dir", type=Path, help="Directory containing per-book */input.json files.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--config-json", type=Path)
    parser.add_argument("--workflow-json", type=Path)
    parser.add_argument("--prompt-templates-json", type=Path)
    parser.add_argument("--limit", type=int, help="Limit number of input payloads after sorting.")
    parser.add_argument("--concurrency", type=int, help="Number of per-book inputs to process concurrently.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Only write prompts. This is the default.")
    mode.add_argument("--run-model", action="store_true", help="Call the configured model and write result artifacts.")
    parser.add_argument("--model", default=os.getenv("SHORT_DRAMA_POC_MODEL"))
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--timeout-seconds", type=int)
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT_DIR / path


def derive_runtime_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path]:
    poc_root = resolve_path(args.poc_root)
    input_dir = resolve_path(args.input_dir) if args.input_dir else poc_root / "inputs" / "jobs" / "per_book"
    output_dir = resolve_path(args.output_dir) if args.output_dir else poc_root / "runs" / "latest"
    config_json = resolve_path(args.config_json) if args.config_json else poc_root / "config" / "ai_tagging_poc_config.json"
    workflow_json = resolve_path(args.workflow_json) if args.workflow_json else poc_root / "config" / "workflow_definition.json"
    prompt_templates_json = (
        resolve_path(args.prompt_templates_json)
        if args.prompt_templates_json
        else poc_root / "config" / "prompt_templates.json"
    )
    return input_dir, output_dir, config_json, workflow_json, prompt_templates_json


def resolve_config_reference(config_path: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"config.{label} must be a non-empty string")
    path = Path(value)
    return path if path.is_absolute() else config_path.parent / path


def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(path: Path) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"config must be a JSON object: {path}")
    return value


def load_workflow_definition(path: Path) -> dict[str, Any]:
    value = load_json(path)
    workflow = require_object(value, f"{path}")
    validate_required_keys(workflow, f"{path}", {"workflow", "version", "stages"})
    stages = require_array(workflow["stages"], f"{path}.stages")
    if not stages:
        raise ValueError(f"{path}.stages must not be empty")
    for index, stage in enumerate(stages):
        stage_obj = require_object(stage, f"{path}.stages[{index}]")
        validate_required_keys(stage_obj, f"{path}.stages[{index}]", {"stage", "prompt_id"})
        if "output_artifact" not in stage_obj and "output_artifacts" not in stage_obj:
            raise ValueError(f"{path}.stages[{index}] must define output_artifact or output_artifacts")
        if "required_keys" in stage_obj:
            require_array(stage_obj["required_keys"], f"{path}.stages[{index}].required_keys")
    return workflow


def load_prompt_templates(path: Path) -> dict[str, dict[str, Any]]:
    value = load_json(path)
    root = require_object(value, f"{path}")
    validate_required_keys(root, f"{path}", {"version", "templates"})
    templates = require_array(root["templates"], f"{path}.templates")
    template_by_id: dict[str, dict[str, Any]] = {}
    for index, template in enumerate(templates):
        template_obj = require_object(template, f"{path}.templates[{index}]")
        validate_required_keys(template_obj, f"{path}.templates[{index}]", {"prompt_id", "messages"})
        prompt_id = template_obj["prompt_id"]
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ValueError(f"{path}.templates[{index}].prompt_id must be a non-empty string")
        if prompt_id in template_by_id:
            raise ValueError(f"{path}.templates contains duplicate prompt_id: {prompt_id}")
        messages = require_array(template_obj["messages"], f"{path}.templates[{index}].messages")
        if not messages:
            raise ValueError(f"{path}.templates[{index}].messages must not be empty")
        for message_index, message in enumerate(messages):
            message_obj = require_object(message, f"{path}.templates[{index}].messages[{message_index}]")
            validate_required_keys(message_obj, f"{path}.templates[{index}].messages[{message_index}]", {"role", "template"})
            if "blocks" in message_obj:
                blocks = require_array(message_obj["blocks"], f"{path}.templates[{index}].messages[{message_index}].blocks")
                for block_index, block in enumerate(blocks):
                    block_obj = require_object(block, f"{path}.templates[{index}].messages[{message_index}].blocks[{block_index}]")
                    validate_required_keys(
                        block_obj,
                        f"{path}.templates[{index}].messages[{message_index}].blocks[{block_index}]",
                        {"block_id", "enabled", "template"},
                    )
        template_by_id[prompt_id] = template_obj
    return template_by_id


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def discover_input_paths(args: argparse.Namespace, default_input_dir: Path) -> list[Path]:
    if args.input_jsons:
        paths = [resolve_path(path) for path in args.input_jsons]
    else:
        if not default_input_dir.is_dir():
            raise FileNotFoundError(f"input dir not found: {default_input_dir}")
        paths = sorted(default_input_dir.glob("*/input.json"))
    if not paths:
        raise FileNotFoundError("No structured input JSON files found")
    paths = sorted(paths)
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit must be greater than 0")
        paths = paths[: args.limit]
    return paths


def validate_input_payload(payload: dict[str, Any], source: Path) -> None:
    validate_required_keys(payload, f"{source}", {"job_id", "client_request_id", "job_type", "job_params", "rs_default_tag_bundle"})
    if payload["job_type"] not in {"short_drama.tagging.initial", "short_drama.tagging.incremental"}:
        raise ValueError(f"{source} has unsupported job_type: {payload['job_type']}")
    job_params = require_object(payload["job_params"], f"{source}.job_params")
    validate_required_keys(job_params, f"{source}.job_params", {"t_book_id", "work_context", "assets"})
    if not isinstance(job_params["t_book_id"], str) or not job_params["t_book_id"].strip():
        raise ValueError(f"{source}.job_params.t_book_id must be a non-empty string")
    work_context = require_object(job_params["work_context"], f"{source}.job_params.work_context")
    validate_required_keys(
        work_context,
        f"{source}.job_params.work_context",
        {"title", "synopsis", "subtitle_language", "audio_language", "series_structure", "content_type", "episode_count"},
    )
    assets = require_array(job_params["assets"], f"{source}.job_params.assets")
    if not assets:
        raise ValueError(f"{source}.job_params.assets must not be empty")
    for index, asset in enumerate(assets):
        asset_obj = require_object(asset, f"{source}.job_params.assets[{index}]")
        validate_required_keys(
            asset_obj,
            f"{source}.job_params.assets[{index}]",
            {"asset_type", "episode_no", "format", "text", "content_hash", "metadata"},
        )
        if asset_obj["asset_type"] != "subtitle_srt" or asset_obj["format"] != "srt":
            raise ValueError(f"{source}.job_params.assets[{index}] must be subtitle_srt/srt")
        if not isinstance(asset_obj["text"], str) or not asset_obj["text"].strip():
            raise ValueError(f"{source}.job_params.assets[{index}].text must be a non-empty string")
    validate_required_keys(
        payload["rs_default_tag_bundle"],
        f"{source}.rs_default_tag_bundle",
        {"tag_schema_snapshot", "mutual_exclusion_rules"},
    )
    bundle = require_object(payload["rs_default_tag_bundle"], f"{source}.rs_default_tag_bundle")
    validate_tag_schema_snapshot(bundle["tag_schema_snapshot"], f"{source}.rs_default_tag_bundle.tag_schema_snapshot")
    mutex_rules = require_array(bundle["mutual_exclusion_rules"], f"{source}.rs_default_tag_bundle.mutual_exclusion_rules")
    for index, rule in enumerate(mutex_rules):
        rule_obj = require_object(rule, f"{source}.rs_default_tag_bundle.mutual_exclusion_rules[{index}]")
        validate_required_keys(
            rule_obj,
            f"{source}.rs_default_tag_bundle.mutual_exclusion_rules[{index}]",
            {"label_id", "mutex_label_ids"},
        )
        require_array(rule_obj["mutex_label_ids"], f"{source}.rs_default_tag_bundle.mutual_exclusion_rules[{index}].mutex_label_ids")


def validate_tag_schema_snapshot(tag_schema: Any, stage: str) -> None:
    schema = require_object(tag_schema, stage)
    validate_required_keys(schema, stage, {"categories", "audience_filter_rules"})
    categories = require_array(schema["categories"], f"{stage}.categories")
    category_ids: set[str] = set()
    for index, category in enumerate(categories):
        category_obj = require_object(category, f"{stage}.categories[{index}]")
        validate_required_keys(
            category_obj,
            f"{stage}.categories[{index}]",
            {"category_id", "name", "required", "min_items", "max_items", "labels"},
        )
        category_ids.add(category_obj["category_id"])
        labels = require_array(category_obj["labels"], f"{stage}.categories[{index}].labels")
        if not labels:
            raise ValueError(f"{stage}.categories[{index}].labels must not be empty")
        label_names: set[str] = set()
        for label_index, label in enumerate(labels):
            label_obj = require_object(label, f"{stage}.categories[{index}].labels[{label_index}]")
            validate_required_keys(
                label_obj,
                f"{stage}.categories[{index}].labels[{label_index}]",
                {"label_id", "name", "definition"},
            )
            if label_obj["name"] in label_names:
                raise ValueError(f"{stage}.categories[{index}].labels contains duplicate name: {label_obj['name']}")
            label_names.add(label_obj["name"])
    missing = sorted(REQUIRED_CATEGORY_IDS - category_ids)
    if missing:
        raise ValueError(f"{stage}.categories missing required category_id values: {missing}")
    require_array(schema["audience_filter_rules"], f"{stage}.audience_filter_rules")


def require_object(value: Any, stage: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{stage} must be a JSON object")
    return value


def require_array(value: Any, stage: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{stage} must be an array")
    return value


def compact_material_for_prompt(job_params: dict[str, Any]) -> str:
    context = job_params["work_context"]
    parts = [
        f"t_book_id: {job_params['t_book_id']}",
        f"作品名称: {context['title']}",
        f"体裁类型: {context['content_type']}",
        f"字幕语言: {context['subtitle_language']}",
        f"音频语言: {context['audio_language']}",
        f"简介: {context['synopsis']}",
        "字幕:",
    ]
    for asset in job_params["assets"]:
        title = f"第{asset['episode_no']}集"
        if asset.get("metadata", {}).get("is_preview"):
            title += " preview"
        parts.append(f"\n## {title}\n{strip_srt_to_text(asset['text'])}")
    return "\n".join(parts)


def strip_srt_to_text(srt_text: str) -> str:
    lines: list[str] = []
    for line in srt_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return "\n".join(lines)


def compact_schema_for_prompt(tag_schema: dict[str, Any]) -> str:
    prompt_categories: list[dict[str, Any]] = []
    for category in tag_schema["categories"]:
        prompt_categories.append(
            {
                "category_id": category["category_id"],
                "name": category["name"],
                "required": category["required"],
                "min_items": category["min_items"],
                "max_items": category["max_items"],
                "labels": [
                    {
                        "name": label["name"],
                        "definition": label["definition"],
                    }
                    for label in category["labels"]
                ],
            }
        )
    return json.dumps(prompt_categories, ensure_ascii=False, indent=2)


def build_prompt_context(input_payload: dict[str, Any], artifacts: dict[str, Any] | None = None) -> dict[str, str]:
    job_params = input_payload["job_params"]
    tag_schema = input_payload["rs_default_tag_bundle"]["tag_schema_snapshot"]
    mutex_rules = input_payload["rs_default_tag_bundle"]["mutual_exclusion_rules"]
    context = {
        "t_book_id": str(job_params["t_book_id"]),
        "material_text": compact_material_for_prompt(job_params),
        "tag_schema": compact_schema_for_prompt(tag_schema),
        "tag_schema_json": json.dumps(tag_schema, ensure_ascii=False, indent=2),
        "audience_filter_rules": json.dumps(tag_schema["audience_filter_rules"], ensure_ascii=False, indent=2),
        "mutex_rules": json.dumps(mutex_rules, ensure_ascii=False, indent=2),
    }
    for key, value in (artifacts or {}).items():
        context[key] = json.dumps(value, ensure_ascii=False, indent=2)
    return context


def render_template(template: str, context: dict[str, str], *, strict: bool) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in context:
            return context[key]
        if strict:
            raise ValueError(f"prompt template references unknown variable: {key}")
        return match.group(0)

    return TEMPLATE_VAR_PATTERN.sub(replace, template)


def render_stage_messages(
    stage: dict[str, Any],
    prompt_templates: dict[str, dict[str, Any]],
    context: dict[str, str],
    *,
    strict: bool,
) -> list[dict[str, str]]:
    prompt_id = stage["prompt_id"]
    template = prompt_templates.get(prompt_id)
    if template is None:
        raise ValueError(f"workflow stage {stage['stage']} references unknown prompt_id: {prompt_id}")
    messages: list[dict[str, str]] = []
    for message in template["messages"]:
        content = render_template(str(message["template"]), context, strict=strict)
        for block in message.get("blocks", []):
            if block["enabled"] is True:
                content += "\n\n" + render_template(str(block["template"]), context, strict=strict)
        messages.append(
            {
                "role": str(message["role"]),
                "content": content,
            }
        )
    return messages


def build_prompts(
    input_payload: dict[str, Any],
    workflow_definition: dict[str, Any],
    prompt_templates: dict[str, dict[str, Any]],
    artifacts: dict[str, Any] | None = None,
    *,
    strict: bool,
    preview_outputs: bool = False,
) -> dict[str, list[dict[str, str]]]:
    artifact_context = dict(artifacts or {})
    prompts: dict[str, list[dict[str, str]]] = {}
    for stage in workflow_definition["stages"]:
        validate_stage_mode(stage)
        validate_stage_input_artifacts(stage, artifact_context)
        context = build_prompt_context(input_payload, artifact_context)
        prompts[stage["stage"]] = render_stage_messages(stage, prompt_templates, context, strict=strict)
        if preview_outputs and "output_artifact" in stage:
            artifact_context[Path(str(stage["output_artifact"])).stem] = build_dry_run_stage_result(stage, input_payload)
    return prompts


def validate_stage_mode(stage: dict[str, Any]) -> None:
    mode = stage.get("mode", "single")
    if mode != "single":
        raise ValueError(f"workflow stage {stage['stage']} has unsupported mode: {mode}")


def validate_stage_input_artifacts(stage: dict[str, Any], artifacts: dict[str, Any]) -> None:
    for artifact_path in stage.get("input_artifacts", []):
        key = Path(str(artifact_path)).stem
        if key not in artifacts:
            raise ValueError(f"workflow stage {stage['stage']} missing input artifact: {artifact_path}")


def build_dry_run_stage_result(stage: dict[str, Any], input_payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in stage.get("required_keys", []):
        if key == "t_book_id":
            result[key] = input_payload["job_params"]["t_book_id"]
        elif key.endswith("status"):
            result[key] = "dry_run_placeholder"
        elif key in {"world_setting"}:
            result[key] = {}
        else:
            result[key] = []
    return result


def write_dry_run_prompts(
    input_payload: dict[str, Any],
    output_dir: Path,
    workflow_definition: dict[str, Any],
    prompt_templates: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    intermediate_dir = output_dir / "intermediate"
    artifact_context: dict[str, Any] = {}
    rendered_prompts: dict[str, list[dict[str, str]]] = {}
    for stage in workflow_definition["stages"]:
        validate_stage_mode(stage)
        validate_stage_input_artifacts(stage, artifact_context)
        context = build_prompt_context(input_payload, artifact_context)
        rendered_prompts[stage["stage"]] = render_stage_messages(stage, prompt_templates, context, strict=True)
        write_json(intermediate_dir / "prompts.json", rendered_prompts)
        if "output_artifact" in stage:
            artifact_context[Path(str(stage["output_artifact"])).stem] = build_dry_run_stage_result(stage, input_payload)
    return rendered_prompts


async def call_model(model: str, messages: list[dict[str, str]], *, temperature: float, timeout_seconds: int) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("--run-model requires OPENAI_API_KEY")
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError("litellm is required for --run-model") from exc
    if os.getenv("OPENAI_BASE_URL"):
        litellm.api_base = os.getenv("OPENAI_BASE_URL")
    response = await asyncio.wait_for(
        litellm.acompletion(
            model=model if "/" in model else f"openai/{model}",
            messages=messages,
            temperature=temperature,
            timeout=timeout_seconds,
            num_retries=0,
            response_format={"type": "json_object"},
        ),
        timeout=timeout_seconds,
    )
    text = response.choices[0].message.content or ""
    if not text.strip():
        raise RuntimeError("Model returned empty content")
    return text.strip()


async def run_model_flow(
    input_payload: dict[str, Any],
    output_dir: Path,
    workflow_definition: dict[str, Any],
    prompt_templates: dict[str, dict[str, Any]],
    *,
    model: str,
    temperature: float,
    timeout_seconds: int,
) -> dict[str, list[dict[str, str]]]:
    intermediate_dir = output_dir / "intermediate"
    expected_t_book_id = input_payload["job_params"]["t_book_id"]
    artifacts: dict[str, Any] = {}
    rendered_prompts: dict[str, list[dict[str, str]]] = {}
    normalization_warnings: list[dict[str, Any]] = []

    for stage in workflow_definition["stages"]:
        stage_name = stage["stage"]
        validate_stage_mode(stage)
        validate_stage_input_artifacts(stage, artifacts)
        context = build_prompt_context(input_payload, artifacts)
        messages = render_stage_messages(stage, prompt_templates, context, strict=True)
        rendered_prompts[stage_name] = messages
        write_json(intermediate_dir / "prompts.json", rendered_prompts)
        raw_result = await call_model(model, messages, temperature=temperature, timeout_seconds=timeout_seconds)
        try:
            result = parse_model_json(raw_result, stage_name)
            normalization_warnings.extend(validate_stage_result(stage, result, expected_t_book_id))
            if stage_name == "candidate_tagging":
                normalization_warnings.extend(
                    normalize_candidate_label_ids(
                        result,
                        input_payload["rs_default_tag_bundle"]["tag_schema_snapshot"],
                    )
                )
            write_normalization_warnings(intermediate_dir, normalization_warnings)

            if "output_artifacts" in stage:
                normalization_warnings.extend(
                    write_final_artifacts(
                        result,
                        input_payload,
                        artifacts,
                        output_dir,
                        require_object(stage["output_artifacts"], f"{stage_name}.output_artifacts"),
                        expected_t_book_id=expected_t_book_id,
                    )
                )
                write_normalization_warnings(intermediate_dir, normalization_warnings)
                continue

            output_artifact = str(stage["output_artifact"])
            write_json(intermediate_dir / output_artifact, result)
            artifacts[Path(output_artifact).stem] = result
        except Exception:
            write_text(intermediate_dir / f"{stage_name}_raw_output.txt", raw_result)
            raise

    return rendered_prompts


def write_normalization_warnings(intermediate_dir: Path, warnings: list[dict[str, Any]]) -> None:
    write_json(intermediate_dir / "normalization_warnings.json", warnings)


def validate_stage_result(stage: dict[str, Any], result: dict[str, Any], expected_t_book_id: str) -> list[dict[str, Any]]:
    stage_name = stage["stage"]
    warnings: list[dict[str, Any]] = []
    if "required_keys" in stage:
        validate_stage_required_keys(stage, result, stage_name)
    if stage.get("validate_t_book_id", True) and "output_artifacts" not in stage:
        warning = validate_t_book_id(result, expected_t_book_id, stage_name)
        if warning:
            warnings.append(warning)
    if "output_artifacts" in stage:
        if "selected_tags" not in result and "final_tags" not in result:
            raise ValueError(f"{stage_name} model output must contain selected_tags")
        if "tagging_detail" not in result:
            raise ValueError(f"{stage_name} model output must contain tagging_detail")
        if not isinstance(result["tagging_detail"], dict):
            raise ValueError(f"{stage_name} tagging_detail must be a JSON object")
    return warnings


def validate_stage_required_keys(stage: dict[str, Any], result: dict[str, Any], stage_name: str) -> None:
    required_keys = set(stage["required_keys"])
    if "output_artifacts" in stage and "selected_tags" in required_keys and "final_tags" in result:
        required_keys.remove("selected_tags")
    validate_required_keys(result, stage_name, required_keys)


def write_final_artifacts(
    final_result: dict[str, Any],
    input_payload: dict[str, Any],
    artifacts: dict[str, Any],
    output_dir: Path,
    output_artifacts: dict[str, Any],
    *,
    expected_t_book_id: str,
) -> list[dict[str, Any]]:
    final_tags, warnings, validation_issues = build_final_tags(
        final_result,
        artifacts,
        input_payload["rs_default_tag_bundle"]["tag_schema_snapshot"],
        expected_t_book_id=expected_t_book_id,
    )
    tagging_detail = require_object(final_result["tagging_detail"], "finalize.tagging_detail")
    result_status = "partial_success" if validation_issues else "success"
    tagging_detail["result_status"] = result_status
    tagging_detail["validation_issues"] = validation_issues
    validate_required_keys(output_artifacts, "finalize.output_artifacts", {"final_tags", "tagging_detail", "job_result"})
    write_json(output_dir / str(output_artifacts["final_tags"]), final_tags)
    write_json(output_dir / str(output_artifacts["tagging_detail"]), tagging_detail)
    job_result = {
        "artifacts": [
            {"key": "final_tags", "type": "json", "label": "最终标签", "content": final_tags},
            {"key": "story_overview", "type": "json", "label": "剧情概览", "content": artifacts.get("story_overview_result")},
            {"key": "tagging_detail", "type": "json", "label": "打标明细", "content": tagging_detail},
        ],
        "signals": {
            "t_book_id": final_tags.get("t_book_id"),
            "result_status": result_status,
            "validation_issue_count": len(validation_issues),
        },
    }
    job_result["signals"]["result_checksum"] = checksum_job_result(job_result)
    write_json(output_dir / str(output_artifacts["job_result"]), job_result)
    return warnings


def parse_model_json(text: str, stage: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{stage} model output is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{stage} model output must be a JSON object")
    return value


def validate_required_keys(value: dict[str, Any], stage: str, keys: set[str]) -> None:
    require_object(value, stage)
    missing = sorted(keys - set(value))
    if missing:
        raise ValueError(f"{stage} missing keys: {missing}")


def validate_t_book_id(value: dict[str, Any], expected_t_book_id: str, stage: str) -> dict[str, Any] | None:
    actual_t_book_id = value.get("t_book_id")
    if str(actual_t_book_id) != expected_t_book_id:
        raise ValueError(f"{stage}.t_book_id must be {expected_t_book_id}: {actual_t_book_id}")
    value["t_book_id"] = expected_t_book_id
    if actual_t_book_id != expected_t_book_id:
        return {
            "stage": stage,
            "field": "t_book_id",
            "from_type": type(actual_t_book_id).__name__,
            "from_value": str(actual_t_book_id),
            "to_type": "str",
            "to_value": expected_t_book_id,
        }
    return None


def validate_final_tags(final_tags: dict[str, Any], tag_schema: dict[str, Any], *, expected_t_book_id: str | None = None) -> list[dict[str, Any]]:
    return validate_final_tags_with_issues(final_tags, tag_schema, expected_t_book_id=expected_t_book_id, validation_issues=[])[0]


def validate_final_tags_with_issues(
    final_tags: dict[str, Any],
    tag_schema: dict[str, Any],
    *,
    expected_t_book_id: str | None = None,
    validation_issues: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    validate_required_keys(final_tags, "final_tags", {"t_book_id", "tags"})
    warnings: list[dict[str, Any]] = []
    if expected_t_book_id is not None:
        warning = validate_t_book_id(final_tags, expected_t_book_id, "final_tags")
        if warning:
            warnings.append(warning)
    if not isinstance(final_tags["tags"], dict):
        raise ValueError("final_tags.tags must be a JSON object")
    categories = {category["category_id"]: category for category in tag_schema["categories"]}
    known_category_ids = set(categories)
    unknown_category_ids = sorted(set(final_tags["tags"]) - known_category_ids)
    if unknown_category_ids:
        raise ValueError(f"final_tags.tags contains unknown category_id values: {unknown_category_ids}")
    labels_by_id_by_category = {
        category_id: {label["label_id"]: label for label in category["labels"]}
        for category_id, category in categories.items()
    }
    for category_id, category in categories.items():
        items = final_tags["tags"].get(category_id)
        if category["required"] and items is None:
            if not has_validation_issue(validation_issues, category_id, "missing_required_category"):
                raise ValueError(f"final_tags.tags missing required category_id: {category_id}")
            continue
        if items is None:
            continue
        if not isinstance(items, list):
            raise ValueError(f"final_tags.tags.{category_id} must be an array")
        if len(items) < category["min_items"]:
            has_allowed_partial_issue = has_validation_issue(validation_issues, category_id, "below_min_items") or (
                len(items) == 0 and has_validation_issue(validation_issues, category_id, "missing_required_category")
            )
            if not has_allowed_partial_issue:
                raise ValueError(
                    f"final_tags.tags.{category_id} item count must be between "
                    f"{category['min_items']} and {category['max_items']}: {len(items)}"
                )
        if len(items) > category["max_items"]:
            raise ValueError(
                f"final_tags.tags.{category_id} item count must be between "
                f"{category['min_items']} and {category['max_items']}: {len(items)}"
            )
        for item in items:
            warnings.extend(validate_tag_item(category_id, item, labels_by_id_by_category[category_id]))
    return warnings, validation_issues


def has_validation_issue(validation_issues: list[dict[str, Any]], category_id: str, issue: str) -> bool:
    return any(
        item.get("category_id") == category_id and item.get("issue") == issue
        for item in validation_issues
    )


def build_final_tags(
    final_result: dict[str, Any],
    artifacts: dict[str, Any],
    tag_schema: dict[str, Any],
    *,
    expected_t_book_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    selected_source, selected_tags, extract_warnings = extract_selected_tags(final_result, expected_t_book_id=expected_t_book_id)
    warnings.extend(extract_warnings)
    final_tags, validation_issues = construct_final_tags_from_selected_tags(
        selected_tags,
        tag_schema,
        expected_t_book_id=expected_t_book_id,
        allow_label_id=selected_source == "legacy_final_tags",
    )
    validate_warnings, validation_issues = validate_final_tags_with_issues(
        final_tags,
        tag_schema,
        expected_t_book_id=expected_t_book_id,
        validation_issues=validation_issues,
    )
    warnings.extend(validate_warnings)
    return final_tags, warnings, validation_issues


def extract_selected_tags(final_result: dict[str, Any], *, expected_t_book_id: str) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    if "selected_tags" in final_result:
        return "selected_tags", require_object(final_result["selected_tags"], "finalize.selected_tags"), []
    final_tags = require_object(final_result["final_tags"], "finalize.final_tags")
    warnings: list[dict[str, Any]] = []
    warning = validate_t_book_id(final_tags, expected_t_book_id, "finalize.final_tags")
    if warning:
        warnings.append(warning)
    return "legacy_final_tags", require_object(final_tags.get("tags"), "finalize.final_tags.tags"), warnings


def flatten_candidate_category_decisions(category_decisions: Any) -> dict[str, list[dict[str, Any]]]:
    flattened: dict[str, list[dict[str, Any]]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        category_id = value.get("category_id")
        if isinstance(category_id, str):
            flattened.setdefault(category_id, []).append(value)
            return
        for item in value.values():
            visit(item)

    visit(category_decisions)
    return flattened


def construct_final_tags_from_selected_tags(
    selected_tags: dict[str, Any],
    tag_schema: dict[str, Any],
    *,
    expected_t_book_id: str,
    allow_label_id: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    categories = {category["category_id"]: category for category in tag_schema["categories"]}
    unknown_category_ids = sorted(set(selected_tags) - set(categories))
    if unknown_category_ids:
        raise ValueError(f"selected_tags contains unknown category_id values: {unknown_category_ids}")
    labels_by_name_by_category = {
        category_id: {label["name"]: label for label in category["labels"]}
        for category_id, category in categories.items()
    }
    labels_by_id_by_category = {
        category_id: {label["label_id"]: label for label in category["labels"]}
        for category_id, category in categories.items()
    }
    final_tags: dict[str, Any] = {"t_book_id": expected_t_book_id, "tags": {}}
    validation_issues: list[dict[str, Any]] = []
    for category_id, category in categories.items():
        items = selected_tags.get(category_id)
        if category["required"] and items is None:
            final_tags["tags"][category_id] = []
            validation_issues.append(
                {
                    "category_id": category_id,
                    "category_name": category["name"],
                    "issue": "missing_required_category",
                    "min_items": category["min_items"],
                    "max_items": category["max_items"],
                    "actual_items": 0,
                    "selected_labels": [],
                    "message": f"{category['name']} 是必填分类，当前未返回标签，按 partial_success 返回空数组。",
                }
            )
            continue
        if items is None:
            continue
        if not isinstance(items, list):
            raise ValueError(f"selected_tags.{category_id} must be an array")
        if len(items) < category["min_items"]:
            validation_issues.append(
                {
                    "category_id": category_id,
                    "category_name": category["name"],
                    "issue": "below_min_items",
                    "min_items": category["min_items"],
                    "max_items": category["max_items"],
                    "actual_items": len(items),
                    "selected_labels": selected_label_names(items),
                    "message": f"{category['name']} 至少需要 {category['min_items']} 个标签，当前仅 {len(items)} 个，按 partial_success 返回现有标签。",
                }
            )
        if len(items) > category["max_items"]:
            raise ValueError(
                f"selected_tags.{category_id} item count must be between "
                f"{category['min_items']} and {category['max_items']}: {len(items)}"
            )
        final_tags["tags"][category_id] = [
            build_final_tag_item(
                category_id,
                item,
                labels_by_name_by_category[category_id],
                labels_by_id_by_category[category_id],
                allow_label_id=allow_label_id,
            )
            for item in items
        ]
    return final_tags, validation_issues


def selected_label_names(items: list[Any]) -> list[str]:
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            label_name = item.get("标签名", item.get("label_name"))
            if isinstance(label_name, str):
                names.append(label_name)
    return names


def build_final_tag_item(
    category_id: str,
    item: Any,
    labels_by_name: dict[str, dict[str, Any]],
    labels_by_id: dict[str, dict[str, Any]],
    *,
    allow_label_id: bool,
) -> dict[str, Any]:
    item_obj = require_object(item, f"selected_tags.{category_id}[]")
    weight = item_obj.get("权重", item_obj.get("weight"))
    if not isinstance(weight, int | float) or weight <= 0 or weight > 1:
        raise ValueError(f"selected_tags.{category_id} item has invalid 权重: {weight}")
    label = resolve_selected_label(category_id, item_obj, labels_by_name, labels_by_id, allow_label_id=allow_label_id)
    reason = item_obj.get("打标原因", item_obj.get("reason"))
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"selected_tags.{category_id} item missing 打标原因")
    return {
        "label_id": label["label_id"],
        "标签名": label["name"],
        "权重": weight,
        "打标原因": reason,
        "标签释义": label["definition"],
    }


def resolve_selected_label(
    category_id: str,
    item: dict[str, Any],
    labels_by_name: dict[str, dict[str, Any]],
    labels_by_id: dict[str, dict[str, Any]],
    *,
    allow_label_id: bool,
) -> dict[str, Any]:
    label_name = item.get("标签名", item.get("label_name"))
    label_id = item.get("label_id")
    if label_id is not None and not allow_label_id:
        raise ValueError(f"selected_tags.{category_id} item must not contain label_id")
    if isinstance(label_name, str) and label_name in labels_by_name:
        label = labels_by_name[label_name]
        if isinstance(label_id, str):
            scratch = {"label_id": label_id}
            resolve_label_id_prefix(category_id, scratch, labels_by_id, stage="selected_tags", field_path=f"{category_id}")
            if scratch["label_id"] != label["label_id"]:
                raise ValueError(f"selected_tags.{category_id} item label_id does not match 标签名: {label_id}")
        return label
    if isinstance(label_id, str) and allow_label_id:
        scratch = {"label_id": label_id}
        resolve_label_id_prefix(category_id, scratch, labels_by_id, stage="selected_tags", field_path=f"{category_id}")
        return labels_by_id[scratch["label_id"]]
    raise ValueError(f"selected_tags.{category_id} item has unknown 标签名: {label_name}")


def normalize_candidate_label_ids(candidate_tags: dict[str, Any], tag_schema: dict[str, Any]) -> list[dict[str, Any]]:
    labels_by_id_by_category = {
        category["category_id"]: {label["label_id"]: label for label in category["labels"]}
        for category in tag_schema["categories"]
    }
    warnings: list[dict[str, Any]] = []
    for root_key in ("category_decisions", "raw_candidates"):
        if root_key not in candidate_tags:
            continue
        warnings.extend(
            normalize_candidate_label_ids_in_value(
                candidate_tags[root_key],
                labels_by_id_by_category,
                stage="candidate_tagging",
                field_path=root_key,
            )
        )
    return warnings


def normalize_candidate_label_ids_in_value(
    value: Any,
    labels_by_id_by_category: dict[str, dict[str, dict[str, Any]]],
    *,
    stage: str,
    field_path: str,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if isinstance(value, list):
        for index, item in enumerate(value):
            warnings.extend(
                normalize_candidate_label_ids_in_value(
                    item,
                    labels_by_id_by_category,
                    stage=stage,
                    field_path=f"{field_path}.{index}",
                )
            )
        return warnings
    if not isinstance(value, dict):
        return warnings
    category_id = value.get("category_id")
    if isinstance(category_id, str) and "label_id" in value:
        labels_by_id = labels_by_id_by_category.get(category_id)
        if labels_by_id is None:
            raise ValueError(f"{stage}.{field_path}.category_id has unknown value: {category_id}")
        warnings.extend(resolve_label_id_prefix(category_id, value, labels_by_id, stage=stage, field_path=field_path))
    for key, item in value.items():
        warnings.extend(
            normalize_candidate_label_ids_in_value(
                item,
                labels_by_id_by_category,
                stage=stage,
                field_path=f"{field_path}.{key}",
            )
        )
    return warnings


def validate_tag_item(
    category_id: str,
    item: Any,
    labels_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(item, dict):
        raise ValueError(f"final_tags.tags.{category_id} item must be a JSON object")
    warnings: list[dict[str, Any]] = []
    required = {"label_id", "标签名", "权重", "打标原因", "标签释义"}
    missing = sorted(required - set(item))
    if missing:
        raise ValueError(f"final_tags.tags.{category_id} item missing keys: {missing}")
    warnings.extend(
        resolve_label_id_prefix(
            category_id,
            item,
            labels_by_id,
            stage="final_tags",
            field_path=f"tags.{category_id}",
        )
    )
    label = labels_by_id[item["label_id"]]
    warnings.extend(normalize_label_text_fields(category_id, item, label))
    weight = item.get("权重")
    if not isinstance(weight, int | float) or weight <= 0 or weight > 1:
        raise ValueError(f"final_tags.tags.{category_id} item has invalid 权重: {weight}")
    return warnings


def resolve_label_id_prefix(
    category_id: str,
    item: dict[str, Any],
    labels_by_id: dict[str, dict[str, Any]],
    *,
    stage: str,
    field_path: str,
) -> list[dict[str, Any]]:
    actual_label_id = item.get("label_id")
    if actual_label_id in labels_by_id:
        return []
    if not isinstance(actual_label_id, str) or not actual_label_id:
        raise ValueError(f"{stage}.{field_path} has invalid label_id: {actual_label_id}")
    matches = [
        label_id
        for label_id in labels_by_id
        if len(actual_label_id) >= MIN_LABEL_ID_PREFIX_LENGTH and label_id.startswith(actual_label_id)
    ]
    if len(matches) != 1:
        raise ValueError(f"{stage}.{field_path} has invalid label_id: {actual_label_id}")
    resolved_label_id = matches[0]
    item["label_id"] = resolved_label_id
    return [
        {
            "stage": stage,
            "field": f"{field_path}.label_id",
            "from_type": "str",
            "from_value": actual_label_id,
            "to_type": "str",
            "to_value": resolved_label_id,
        }
    ]


def normalize_label_text_fields(category_id: str, item: dict[str, Any], label: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    field_pairs = [
        ("标签名", "name"),
        ("标签释义", "definition"),
    ]
    for output_field, schema_field in field_pairs:
        actual_value = item[output_field]
        expected_value = label[schema_field]
        if actual_value == expected_value:
            continue
        item[output_field] = expected_value
        warnings.append(
            {
                "stage": "final_tags",
                "field": f"tags.{category_id}.{item['label_id']}.{output_field}",
                "from_type": type(actual_value).__name__,
                "from_value": str(actual_value),
                "to_type": type(expected_value).__name__,
                "to_value": str(expected_value),
            }
        )
    return warnings


def checksum_json(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def checksum_job_result(job_result: dict[str, Any]) -> str:
    checksum_input = json.loads(json.dumps(job_result, ensure_ascii=False))
    checksum_input["signals"].pop("result_checksum", None)
    return checksum_json(checksum_input)


def output_dir_for_payload(root_output_dir: Path, input_payload: dict[str, Any]) -> Path:
    return root_output_dir / "per_book" / input_payload["job_params"]["t_book_id"]


async def run_one_input_async(
    input_path: Path,
    output_root: Path,
    workflow_definition: dict[str, Any],
    prompt_templates: dict[str, dict[str, Any]],
    *,
    run_model: bool,
    model: str,
    temperature: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    input_payload = load_json(input_path)
    validate_input_payload(input_payload, input_path)
    output_dir = output_dir_for_payload(output_root, input_payload)
    write_json(output_dir / "input" / "input.json", input_payload)
    if run_model:
        await run_model_flow(
            input_payload,
            output_dir,
            workflow_definition,
            prompt_templates,
            model=model,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )
    else:
        write_dry_run_prompts(input_payload, output_dir, workflow_definition, prompt_templates)
    return {"t_book_id": input_payload["job_params"]["t_book_id"], "output_dir": str(output_dir)}


def run_one_input(
    input_path: Path,
    output_root: Path,
    workflow_definition: dict[str, Any],
    prompt_templates: dict[str, dict[str, Any]],
    *,
    run_model: bool,
    model: str,
    temperature: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    return asyncio.run(
        run_one_input_async(
            input_path,
            output_root,
            workflow_definition,
            prompt_templates,
            run_model=run_model,
            model=model,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )
    )


async def run_inputs_concurrently(
    input_paths: list[Path],
    output_root: Path,
    workflow_definition: dict[str, Any],
    prompt_templates: dict[str, dict[str, Any]],
    *,
    run_model: bool,
    model: str,
    temperature: float,
    timeout_seconds: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any] | None] = [None] * len(input_paths)

    async def run_index(index: int, input_path: Path) -> None:
        async with semaphore:
            try:
                results[index] = await run_one_input_async(
                    input_path,
                    output_root,
                    workflow_definition,
                    prompt_templates,
                    run_model=run_model,
                    model=model,
                    temperature=temperature,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                raise RuntimeError(f"failed processing {format_input_error_context(input_path)}: {exc}") from exc

    await asyncio.gather(*(run_index(index, path) for index, path in enumerate(input_paths)))
    return [require_object(result, f"results[{index}]") for index, result in enumerate(results)]


def run_inputs(
    input_paths: list[Path],
    output_root: Path,
    workflow_definition: dict[str, Any],
    prompt_templates: dict[str, dict[str, Any]],
    *,
    run_model: bool,
    model: str,
    temperature: float,
    timeout_seconds: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    if concurrency < 1:
        raise ValueError(f"--concurrency must be >= 1: {concurrency}")
    validate_unique_input_t_book_ids(input_paths)
    if concurrency == 1:
        results: list[dict[str, Any]] = []
        for path in input_paths:
            try:
                results.append(
                    run_one_input(
                        path,
                        output_root,
                        workflow_definition,
                        prompt_templates,
                        run_model=run_model,
                        model=model,
                        temperature=temperature,
                        timeout_seconds=timeout_seconds,
                    )
                )
            except Exception as exc:
                raise RuntimeError(f"failed processing {format_input_error_context(path)}: {exc}") from exc
        return results
    return asyncio.run(
        run_inputs_concurrently(
            input_paths,
            output_root,
            workflow_definition,
            prompt_templates,
            run_model=run_model,
            model=model,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            concurrency=concurrency,
        )
    )


def validate_unique_input_t_book_ids(input_paths: list[Path]) -> None:
    seen: dict[str, Path] = {}
    for input_path in input_paths:
        input_payload = require_object(load_json(input_path), f"{input_path}")
        validate_input_payload(input_payload, input_path)
        t_book_id = input_payload["job_params"]["t_book_id"]
        if t_book_id in seen:
            raise ValueError(f"duplicate t_book_id would write the same output directory: {t_book_id} ({seen[t_book_id]}, {input_path})")
        seen[t_book_id] = input_path


def format_input_error_context(input_path: Path) -> str:
    t_book_id = "<unknown>"
    try:
        input_payload = load_json(input_path)
        if isinstance(input_payload, dict):
            job_params = input_payload.get("job_params")
            if isinstance(job_params, dict) and job_params.get("t_book_id") is not None:
                t_book_id = str(job_params["t_book_id"])
    except Exception:
        pass
    return f"input_path={input_path}, t_book_id={t_book_id}"


def main() -> int:
    args = parse_args()
    default_input_dir, output_root, config_path, workflow_path, prompt_templates_path = derive_runtime_paths(args)
    input_paths = discover_input_paths(args, default_input_dir)
    config = load_config(config_path)
    validate_required_keys(config, "config", {"workflow_definition", "prompt_templates"})
    if args.workflow_json is None:
        workflow_path = resolve_config_reference(config_path, config["workflow_definition"], "workflow_definition")
    if args.prompt_templates_json is None:
        prompt_templates_path = resolve_config_reference(config_path, config["prompt_templates"], "prompt_templates")
    workflow_definition = load_workflow_definition(workflow_path)
    prompt_templates = load_prompt_templates(prompt_templates_path)
    model = args.model or str(config.get("default_model", "gpt-4o-mini"))
    temperature = args.temperature if args.temperature is not None else float(config.get("temperature", 0.2))
    timeout_seconds = args.timeout_seconds if args.timeout_seconds is not None else int(config.get("timeout_seconds", 900))
    concurrency = args.concurrency if args.concurrency is not None else int(config.get("concurrency", 1))
    results = run_inputs(
        input_paths,
        output_root,
        workflow_definition,
        prompt_templates,
        run_model=args.run_model,
        model=model,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        concurrency=concurrency,
    )
    summary = {
        "output_dir": str(output_root.relative_to(ROOT_DIR)) if output_root.is_relative_to(ROOT_DIR) else str(output_root),
        "config_json": str(config_path.relative_to(ROOT_DIR)) if config_path.is_relative_to(ROOT_DIR) else str(config_path),
        "workflow_json": str(workflow_path.relative_to(ROOT_DIR)) if workflow_path.is_relative_to(ROOT_DIR) else str(workflow_path),
        "prompt_templates_json": (
            str(prompt_templates_path.relative_to(ROOT_DIR))
            if prompt_templates_path.is_relative_to(ROOT_DIR)
            else str(prompt_templates_path)
        ),
        "input_count": len(input_paths),
        "run_model": args.run_model,
        "model": model,
        "concurrency": concurrency,
        "stages": [stage["stage"] for stage in workflow_definition["stages"]],
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"short_drama_tagging_poc failed: {exc}", file=sys.stderr)
        raise
