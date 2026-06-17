from __future__ import annotations

import json
import re
from typing import Any

from app.core.exceptions import AppError

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_MODEL_INTERNAL_OUTPUT_KEYS = {
    "category_id",
    "label_id",
    "label_key",
    "label_ids",
    "mutex_label_ids",
    "definition",
    "标签释义",
}


def strip_srt_to_text(srt_text: str) -> str:
    lines: list[str] = []
    for line in srt_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return "\n".join(lines)


def compact_material_for_prompt(job_params: dict[str, Any]) -> str:
    context = job_params["work_context"]
    parts = [
        f"t_book_id: {job_params['t_book_id']}",
        f"title: {context['title']}",
        f"subtitle_language: {context['subtitle_language']}",
        f"series_structure: {context['series_structure']}",
        f"content_type: {context.get('content_type') or ''}",
        f"episode_count: {context.get('episode_count') if context.get('episode_count') is not None else ''}",
        f"synopsis: {context.get('synopsis') or ''}",
        "subtitles:",
    ]
    for asset in job_params["assets"]:
        if asset["asset_type"] != "subtitle_srt":
            continue
        episode = asset.get("episode_no")
        title = f"episode {episode}" if episode is not None else "episode"
        parts.append(f"\n## {title}\n{strip_srt_to_text(asset.get('text') or '')}")
    return "\n".join(parts)


def compact_schema_for_prompt(tag_schema: dict[str, Any]) -> str:
    categories: list[dict[str, Any]] = []
    for category in tag_schema["categories"]:
        categories.append(
            {
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
    return json.dumps(categories, ensure_ascii=False, indent=2)


def compact_mutual_exclusion_rules_for_prompt(
    tag_schema: dict[str, Any],
    mutual_exclusion_rules: list[dict[str, Any]],
) -> str:
    labels_by_id: dict[str, dict[str, str]] = {}
    for category in tag_schema["categories"]:
        category_name = category["name"]
        for label in category["labels"]:
            labels_by_id[label["label_id"]] = {
                "category": category_name,
                "name": label["name"],
                "definition": label["definition"],
            }

    rules: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for rule in mutual_exclusion_rules:
        label_id = rule["label_id"]
        for mutex_label_id in rule["mutex_label_ids"]:
            pair = tuple(sorted((label_id, mutex_label_id)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            rules.append(
                {
                    "rule": "do_not_select_together",
                    "labels": [
                        labels_by_id[label_id],
                        labels_by_id[mutex_label_id],
                    ],
                }
            )
    return json.dumps(rules, ensure_ascii=False, indent=2)


def strip_internal_model_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [strip_internal_model_fields(item) for item in value]
    if isinstance(value, dict):
        return {
            key: strip_internal_model_fields(item)
            for key, item in value.items()
            if key not in _MODEL_INTERNAL_OUTPUT_KEYS
        }
    return value


def stage_messages(
    stage: str,
    *,
    job_params: dict[str, Any],
    tag_schema: dict[str, Any],
    mutual_exclusion_rules: list[dict[str, Any]],
    artifacts: dict[str, Any],
) -> list[dict[str, str]]:
    language = job_params["work_context"]["subtitle_language"]
    material_text = compact_material_for_prompt(job_params)
    schema_text = compact_schema_for_prompt(tag_schema)
    mutual_exclusion_rules_text = compact_mutual_exclusion_rules_for_prompt(tag_schema, mutual_exclusion_rules)
    if stage == "story_overview":
        prompt = f"""You are a short-drama story analysis expert.

Summarize the supplied title, synopsis, and subtitles. Use subtitle_language={language} for natural-language fields.
Return JSON only. Required keys: t_book_id, analysis_status, characters, world_setting, plot_timeline, main_conflicts, uncertainties.

Short-drama material:
{material_text}
"""
    elif stage == "candidate_tagging":
        prompt = f"""You are a short-drama tagging expert.

Use subtitle_language={language} for all natural-language reasons. Choose tags only from the tag schema. Output category names and tag names only; do not output internal IDs.
Apply mutual exclusion rules and category constraints when selecting candidates.
Return JSON only. Required keys: t_book_id, category_decisions, raw_candidates, uncertainties.

Short-drama material:
{material_text}

Story overview:
{json.dumps(artifacts['story_overview_result'], ensure_ascii=False, indent=2)}

Tag schema:
{schema_text}

Mutual exclusion rules:
{mutual_exclusion_rules_text}
"""
    elif stage == "finalize":
        prompt = f"""You are a short-drama tag finalization system.

Use subtitle_language={language} for all reasons. Output selected_tags keyed by category name. Each tag item must contain only name/label_name/标签名, weight/权重, and reason/打标原因.
Do not output internal IDs or definition; the service will resolve them from the RS tag schema.
Apply mutual exclusion rules and category constraints. If evidence is insufficient, keep the available tags and leave tagging_detail notes explaining the issue.
Return JSON only with exactly selected_tags and tagging_detail.

Tag schema:
{schema_text}

Mutual exclusion rules:
{mutual_exclusion_rules_text}

Story overview:
{json.dumps(artifacts['story_overview_result'], ensure_ascii=False, indent=2)}

Candidate tags:
{json.dumps(strip_internal_model_fields(artifacts['candidate_tags']), ensure_ascii=False, indent=2)}
"""
    else:
        raise ValueError(f"unknown short drama tagging stage: {stage}")
    return [{"role": "user", "content": prompt}]


def parse_model_json(text: str, stage: str) -> dict[str, Any]:
    stripped = text.strip()
    match = _JSON_FENCE_RE.match(stripped)
    if match:
        stripped = match.group(1).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise AppError("MODEL_OUTPUT_INVALID", f"{stage} model output is not valid JSON", status_code=502) from exc
    if not isinstance(value, dict):
        raise AppError("MODEL_OUTPUT_INVALID", f"{stage} model output must be a JSON object", status_code=502)
    return value
