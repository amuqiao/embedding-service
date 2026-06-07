import json
import re
from typing import Any

from app.infrastructure.ai_gateway import TextGenerationResult
from app.models.job import AIJob


EMPTY_PROJECT_MEMORY: dict[str, Any] = {
    "characters": [],
    "places": [],
    "glossary": [],
    "style_guide": "",
    "cultural_rules": [],
    "continuity_notes": [],
    "chunk_summaries": [],
}


def extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None

    candidates = [stripped]
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL):
        candidates.append(match.group(1))

    first = stripped.find("{")
    last = stripped.rfind("}")
    if first >= 0 and last > first:
        candidates.append(stripped[first : last + 1])

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def normalize_project_memory(value: dict[str, Any] | None, *, raw: str = "") -> dict[str, Any]:
    memory = dict(EMPTY_PROJECT_MEMORY)
    if value:
        for key in memory:
            candidate = value.get(key)
            if isinstance(memory[key], list):
                memory[key] = candidate if isinstance(candidate, list) else []
            elif isinstance(candidate, str):
                memory[key] = candidate
    if raw:
        memory["raw"] = raw
    memory["frozen"] = True
    return memory


def project_memory_from_generation(result: TextGenerationResult) -> dict[str, Any]:
    parsed = extract_json_object(result.text)
    return normalize_project_memory(parsed, raw=result.text)


def project_memory_from_job(job: AIJob) -> dict[str, Any] | None:
    metadata = job.metadata_payload or {}
    for key in ("project_memory", "mapping_table"):
        value = metadata.get(key)
        if isinstance(value, dict):
            return normalize_project_memory(value)

    work_note = prompt_block_content(job.prompt_payload, "work_note")
    parsed = extract_tagged_json(work_note, "project_memory") or extract_tagged_json(work_note, "mapping_table")
    if parsed:
        return normalize_project_memory(parsed)
    return None


def prompt_block_content(prompt_payload: dict[str, Any], key: str) -> str:
    for block in prompt_payload.get("blocks") or []:
        if block.get("key") == key:
            return str(block.get("content") or "")
    return ""


def extract_tagged_json(text: str, tag: str) -> dict[str, Any] | None:
    pattern = rf"<{re.escape(tag)}>\s*(.*?)\s*</{re.escape(tag)}>"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None
    return extract_json_object(match.group(1))


def append_context_to_prompt(job: AIJob, context_text: str) -> dict[str, Any]:
    if not context_text.strip():
        return job.prompt_payload

    payload = json.loads(json.dumps(job.prompt_payload, ensure_ascii=False))
    for block in payload.get("blocks") or []:
        if block.get("key") == "system":
            block["content"] = f"{context_text.strip()}\n\n{block.get('content') or ''}"
            return payload

    payload.setdefault("blocks", []).insert(
        0,
        {"key": "system_context", "role": "system", "content": context_text.strip()},
    )
    return payload


def format_project_memory(memory: dict[str, Any] | None) -> str:
    if not memory:
        return ""
    return (
        "【已冻结项目记忆 / Project Memory】\n"
        f"{json.dumps(memory, ensure_ascii=False, indent=2)}\n\n"
        "执行要求：严格遵守上述人物、地点、术语、文化转换规则和连续性事实；不得自行更改已有映射。"
    )


def build_chunk_context(
    *,
    memory: dict[str, Any] | None,
    chunk_index: int,
    chunk_count: int,
    previous_summary: str | None = None,
    next_summary: str | None = None,
) -> str:
    parts: list[str] = []
    memory_text = format_project_memory(memory)
    if memory_text:
        parts.append(memory_text)
    parts.append(f"【当前分块】{chunk_index}/{chunk_count}")
    if previous_summary:
        parts.append(f"【上一分块摘要】\n{previous_summary}")
    if next_summary:
        parts.append(f"【下一分块摘要】\n{next_summary}")
    return "\n\n".join(parts)

