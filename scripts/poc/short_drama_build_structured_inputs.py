from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / ".data"
POC_ROOT = DATA_DIR / "poc" / "short_drama_tagging"
DEFAULT_MATERIAL_DIR = DATA_DIR / "第三批字幕srt"
DEFAULT_TAG_XLSX = DATA_DIR / "标签体系v1.2.xlsx"
DEFAULT_WORKS_MD = DATA_DIR / "第三批打标尝试.md"
DEFAULT_OUTPUT_DIR = POC_ROOT / "inputs"
DEFAULT_CONFIG_DIR = POC_ROOT / "config"

TAG_CATEGORY_SPECS: tuple[dict[str, Any], ...] = (
    {"category_id": "000001", "name": "受众", "required": True, "min_items": 1, "max_items": 1},
    {"category_id": "000002", "name": "时空", "required": True, "min_items": 1, "max_items": 1},
    {"category_id": "000003", "name": "题材", "required": True, "min_items": 1, "max_items": 3},
    {"category_id": "000004", "name": "情节", "required": True, "min_items": 3, "max_items": 8},
    {"category_id": "000005", "name": "角色设定", "required": True, "min_items": 2, "max_items": 4},
    {"category_id": "000006", "name": "情绪", "required": True, "min_items": 1, "max_items": 1},
)
TAG_CATEGORY_BY_NAME = {item["name"]: item for item in TAG_CATEGORY_SPECS}

# The Excel v1.2 mutex sheet uses two shorthand names that do not exist as labels.
# Keep this mapping explicit so new source-data drift still fails fast.
SOURCE_LABEL_ALIASES = {
    "系统": "系统奇遇",
    "脑洞": "奇幻脑洞",
}

EXCEL_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


@dataclass(frozen=True)
class WorkMeta:
    t_book_id: str
    title: str
    content_type: str
    subtitle_language: str
    audio_language: str
    synopsis: str
    is_ai: str


@dataclass(frozen=True)
class SubtitleAsset:
    episode_no: int
    uri: str
    text: str
    content_hash: str
    filename: str
    is_preview: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build structured CPP/RS input snapshots for the short-drama tagging POC."
    )
    parser.add_argument("--poc-root", type=Path, default=POC_ROOT)
    parser.add_argument("--material-dir", type=Path, default=DEFAULT_MATERIAL_DIR)
    parser.add_argument("--tag-xlsx", type=Path, default=DEFAULT_TAG_XLSX)
    parser.add_argument("--works-md", type=Path, default=DEFAULT_WORKS_MD)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--book-id", action="append", dest="book_ids", help="Limit to one or more t_book_id values.")
    parser.add_argument("--limit", type=int, help="Limit number of works after sorting/filtering.")
    parser.add_argument("--limit-episodes", type=int, help="Limit episodes per work for quick structured-input checks.")
    return parser.parse_args()


def require_path(path: Path, label: str) -> Path:
    path = path if path.is_absolute() else ROOT_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def resolve_write_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT_DIR / path


def derive_poc_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    poc_root = resolve_write_path(args.poc_root)
    output_dir = resolve_write_path(args.output_dir) if args.output_dir else poc_root / "inputs"
    if args.config_dir:
        config_dir = resolve_write_path(args.config_dir)
    elif args.output_dir:
        config_dir = output_dir.parent / "config" if output_dir.name == "inputs" else output_dir / "config"
    else:
        config_dir = poc_root / "config"
    return output_dir, config_dir


def clean_table_cell(value: str) -> str:
    return value.replace("\\_", "_").replace("\u00a0", " ").strip()


def parse_works_md(path: Path) -> list[WorkMeta]:
    rows: list[WorkMeta] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line or "t\\_book\\_id" in line:
            continue
        cols = [clean_table_cell(col) for col in line.strip("|").split("|")]
        if len(cols) < 7 or not cols[0].isdigit():
            continue
        rows.append(
            WorkMeta(
                t_book_id=cols[0],
                title=cols[1],
                content_type=cols[2],
                subtitle_language=cols[3],
                audio_language=cols[4],
                synopsis=cols[5],
                is_ai=cols[6],
            )
        )
    if not rows:
        raise ValueError(f"No work rows parsed from {path}")
    ids = [item.t_book_id for item in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate t_book_id values in {path}")
    return rows


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_label_id(category_id: str, label_name: str) -> str:
    return hashlib.sha1(f"{category_id}:{label_name}".encode("utf-8")).hexdigest()[:24]


def stable_job_id(t_book_id: str) -> str:
    digest = hashlib.sha1(f"short-drama-poc:{t_book_id}".encode("utf-8")).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def parse_episode_no(path: Path) -> tuple[int, bool]:
    match = re.fullmatch(r".+_(\d{4})(_preview)?\.srt", path.name)
    if not match:
        raise ValueError(f"Unexpected SRT filename: {path}")
    return int(match.group(1)), bool(match.group(2))


def read_subtitle_assets(work_dir: Path, *, limit_episodes: int | None) -> list[SubtitleAsset]:
    files = sorted(work_dir.glob("*.srt"), key=lambda item: (parse_episode_no(item)[0], parse_episode_no(item)[1], item.name))
    if not files:
        raise FileNotFoundError(f"No .srt files under {work_dir}")
    assets: list[SubtitleAsset] = []
    seen_regular_episodes: set[int] = set()
    for path in files:
        episode_no, is_preview = parse_episode_no(path)
        if not is_preview:
            if episode_no in seen_regular_episodes:
                raise ValueError(f"Duplicate regular episode {episode_no} under {work_dir}")
            seen_regular_episodes.add(episode_no)
        text = path.read_text(encoding="utf-8-sig")
        assets.append(
            SubtitleAsset(
                episode_no=episode_no,
                uri=display_path(path),
                text=text,
                content_hash=sha256_text(text),
                filename=path.name,
                is_preview=is_preview,
            )
        )
    if limit_episodes is not None:
        if limit_episodes <= 0:
            raise ValueError("--limit-episodes must be greater than 0")
        assets = [asset for asset in assets if asset.is_preview or asset.episode_no <= limit_episodes]
    return assets


def language_code(value: str) -> str:
    mapping = {"英语": "en", "中文": "zh"}
    if value not in mapping:
        raise ValueError(f"Unsupported language in third batch metadata: {value}")
    return mapping[value]


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def build_work_material(work: WorkMeta, material_dir: Path, *, limit_episodes: int | None) -> dict[str, Any]:
    work_dir = material_dir / work.t_book_id
    if not work_dir.is_dir():
        raise FileNotFoundError(f"Subtitle directory not found for {work.t_book_id}: {work_dir}")
    assets = read_subtitle_assets(work_dir, limit_episodes=limit_episodes)
    regular_episodes = [asset.episode_no for asset in assets if not asset.is_preview]
    if regular_episodes:
        expected = set(range(1, max(regular_episodes) + 1))
        missing = sorted(expected - set(regular_episodes))
        if missing:
            raise ValueError(f"Missing regular episodes for {work.t_book_id}: {missing}")
    return {
        "t_book_id": work.t_book_id,
        "work_context": {
            "title": work.title,
            "synopsis": work.synopsis,
            "subtitle_language": language_code(work.subtitle_language),
            "audio_language": language_code(work.audio_language),
            "series_structure": "continuous_series",
            "content_type": work.content_type,
            "episode_count": len(regular_episodes),
            "is_ai_material": work.is_ai,
        },
        "assets": [
            {
                "asset_type": "subtitle_srt",
                "episode_no": asset.episode_no,
                "format": "srt",
                "uri": asset.uri,
                "text": asset.text,
                "content_hash": asset.content_hash,
                "metadata": {"filename": asset.filename, "is_preview": asset.is_preview},
            }
            for asset in assets
        ],
    }


def load_xlsx_rows(path: Path) -> dict[str, list[list[str]]]:
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        shared_strings = load_shared_strings(archive)
        sheets: dict[str, list[list[str]]] = {}
        sheets_node = workbook.find("a:sheets", EXCEL_NS)
        if sheets_node is None:
            raise ValueError(f"Workbook has no sheets: {path}")
        for sheet in sheets_node:
            name = sheet.attrib["name"]
            rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = relmap[rel_id]
            sheet_path = "xl/" + target if not target.startswith("/") else target[1:]
            root = ET.fromstring(archive.read(sheet_path))
            rows: list[list[str]] = []
            for row in root.findall("a:sheetData/a:row", EXCEL_NS):
                values: list[str] = []
                for cell in row.findall("a:c", EXCEL_NS):
                    idx = cell_col_index(cell.attrib["r"])
                    while len(values) <= idx:
                        values.append("")
                    values[idx] = read_cell_value(cell, shared_strings)
                if any(value.strip() for value in values):
                    rows.append([value.strip() for value in values])
            sheets[name] = rows
        return sheets


def load_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("a:si", EXCEL_NS):
        parts = [node.text or "" for node in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
        values.append("".join(parts))
    return values


def cell_col_index(ref: str) -> int:
    letters = "".join(char for char in ref if char.isalpha())
    index = 0
    for char in letters:
        index = index * 26 + ord(char.upper()) - 64
    return index - 1


def read_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(
            node.text or "" for node in cell.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        )
    value = cell.find("a:v", EXCEL_NS)
    if value is None or value.text is None:
        return ""
    if cell.attrib.get("t") == "s":
        return shared_strings[int(value.text)]
    return value.text


def build_tag_schema_snapshot(sheets: dict[str, list[list[str]]]) -> dict[str, Any]:
    categories: list[dict[str, Any]] = []
    label_index: dict[str, dict[str, str]] = {}
    for spec in TAG_CATEGORY_SPECS:
        sheet_name = spec["name"]
        if sheet_name not in sheets:
            raise ValueError(f"Missing required tag sheet: {sheet_name}")
        rows = sheets[sheet_name]
        labels: list[dict[str, str]] = []
        for row in rows[1:]:
            if len(row) < 2:
                continue
            label_name, definition = row[0].strip(), row[1].strip()
            if not label_name or not definition:
                continue
            label_id = stable_label_id(spec["category_id"], label_name)
            label = {
                "label_id": label_id,
                "label_key": label_id,
                "name": label_name,
                "definition": definition,
            }
            labels.append(label)
            if label_name in label_index:
                raise ValueError(f"Duplicate label name across schema: {label_name}")
            label_index[label_name] = {"label_id": label_id, "category_id": spec["category_id"]}
        if len(labels) < spec["min_items"]:
            raise ValueError(f"Not enough labels in sheet {sheet_name}")
        categories.append({**spec, "labels": labels})
    return {
        "version": "poc-xlsx-v1.2",
        "generated_at": int(time.time()),
        "source": str(DEFAULT_TAG_XLSX.relative_to(ROOT_DIR)),
        "categories": categories,
        "audience_filter_rules": build_audience_filter_rules(sheets, label_index),
    }


def build_audience_filter_rules(sheets: dict[str, list[list[str]]], label_index: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows = sheets.get("受众过滤规则")
    if not rows:
        raise ValueError("Missing required sheet: 受众过滤规则")
    rules: list[dict[str, Any]] = []
    for row in rows[1:]:
        if len(row) < 3 or not row[0].strip():
            continue
        audience_name = row[0].strip()
        if audience_name not in label_index:
            raise ValueError(f"Audience filter references unknown audience label: {audience_name}")
        exclude_names = [value.strip() for value in row[2:] if value.strip()]
        unknown = [name for name in exclude_names if name not in label_index]
        if unknown:
            raise ValueError(f"Audience filter for {audience_name} references unknown labels: {unknown}")
        rules.append(
            {
                "audience_label_id": label_index[audience_name]["label_id"],
                "audience_label_name": audience_name,
                "exclude_label_ids": [label_index[name]["label_id"] for name in exclude_names],
                "exclude_label_names": exclude_names,
            }
        )
    return rules


def schema_label_index(tag_schema: dict[str, Any]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for category in tag_schema["categories"]:
        for label in category["labels"]:
            index[label["name"]] = {"label_id": label["label_id"], "category_id": category["category_id"]}
    return index


def build_mutual_exclusion_rules(sheets: dict[str, list[list[str]]], tag_schema: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sheets.get("互斥规则")
    if not rows:
        raise ValueError("Missing required sheet: 互斥规则")
    label_index = schema_label_index(tag_schema)
    rules: list[dict[str, Any]] = []
    for row in rows[1:]:
        if len(row) < 1 or not row[0].strip():
            continue
        source_label_name = row[0].strip()
        label_name = normalize_source_label_name(source_label_name)
        if label_name not in label_index:
            raise ValueError(f"Mutual exclusion rule references unknown label: {source_label_name}")
        source_exclude_names = split_label_list(row[1] if len(row) > 1 else "")
        exclude_names = [normalize_source_label_name(name) for name in source_exclude_names]
        unknown = [name for name in exclude_names if name not in label_index]
        if unknown:
            raise ValueError(f"Mutual exclusion rule for {source_label_name} references unknown labels: {unknown}")
        if not exclude_names:
            continue
        rules.append(
            {
                "label_id": label_index[label_name]["label_id"],
                "label_name": label_name,
                "source_label_name": source_label_name,
                "mutex_label_ids": [label_index[name]["label_id"] for name in exclude_names],
                "mutex_label_names": exclude_names,
                "source_mutex_label_names": source_exclude_names,
            }
        )
    return rules


def normalize_source_label_name(value: str) -> str:
    return SOURCE_LABEL_ALIASES.get(value, value)


def split_label_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[、,，]", value) if item.strip()]


def select_works(works: list[WorkMeta], *, book_ids: list[str] | None, limit: int | None) -> list[WorkMeta]:
    selected = sorted(works, key=lambda item: item.t_book_id)
    if book_ids:
        requested = set(book_ids)
        known = {work.t_book_id for work in selected}
        missing = sorted(requested - known)
        if missing:
            raise ValueError(f"Requested --book-id values not found in works md: {missing}")
        selected = [work for work in selected if work.t_book_id in requested]
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be greater than 0")
        selected = selected[:limit]
    return selected


def build_job_params(material: dict[str, Any]) -> dict[str, Any]:
    return {
        "t_book_id": material["t_book_id"],
        "work_context": material["work_context"],
        "assets": material["assets"],
    }


def build_material_snapshot(materials: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "source": "cpp_poc_third_batch",
        "job_type": "short_drama.tagging.initial",
        "generated_at": int(time.time()),
        "works": materials,
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_book_inputs(
    output_dir: Path,
    materials: list[dict[str, Any]],
    tag_schema: dict[str, Any],
    mutex_rules: list[dict[str, Any]],
) -> None:
    for material in materials:
        book_dir = output_dir / "jobs" / "per_book" / material["t_book_id"]
        input_payload = {
            "job_id": stable_job_id(material["t_book_id"]),
            "client_request_id": f"cpp:{material['t_book_id']}:initial:poc-third-batch",
            "job_type": "short_drama.tagging.initial",
            "job_params": build_job_params(material),
            "rs_default_tag_bundle": {
                "tag_schema_snapshot": tag_schema,
                "mutual_exclusion_rules": mutex_rules,
            },
        }
        write_json(book_dir / "input.json", input_payload)


def build_service_config() -> dict[str, Any]:
    return {
        "workflow": "short_drama_tagging_poc",
        "job_types": ["short_drama.tagging.initial", "short_drama.tagging.incremental"],
        "default_model": "gpt-4o-mini",
        "temperature": 0.2,
        "timeout_seconds": 900,
        "concurrency": 1,
        "workflow_definition": "workflow_definition.json",
        "prompt_templates": "prompt_templates.json",
        "stages": ["story_overview", "candidate_tagging", "finalize"],
        "artifacts": {
            "intermediate": ["prompts", "story_overview_result", "candidate_tags"],
            "outputs": ["final_tags", "tagging_detail", "job_result"],
        },
        "strict_validation": {
            "json_object_outputs": True,
            "validate_t_book_id": True,
            "validate_label_id_name_definition": True,
            "validate_result_checksum": True,
        },
    }


def build_workflow_definition() -> dict[str, Any]:
    return {
        "workflow": "short_drama_tagging_poc",
        "version": "third_batch_v1",
        "stages": [
            {
                "stage": "story_overview",
                "mode": "single",
                "prompt_id": "story_overview_v1",
                "output_artifact": "story_overview_result.json",
                "required_keys": [
                    "t_book_id",
                    "analysis_status",
                    "characters",
                    "world_setting",
                    "plot_timeline",
                    "main_conflicts",
                    "uncertainties",
                ],
                "validate_t_book_id": True,
            },
            {
                "stage": "candidate_tagging",
                "mode": "single",
                "prompt_id": "candidate_tagging_v1",
                "input_artifacts": ["story_overview_result.json"],
                "output_artifact": "candidate_tags.json",
                "required_keys": ["t_book_id", "category_decisions", "raw_candidates", "uncertainties"],
                "validate_t_book_id": True,
            },
            {
                "stage": "finalize",
                "mode": "single",
                "prompt_id": "finalize_v1",
                "input_artifacts": ["story_overview_result.json", "candidate_tags.json"],
                "output_artifacts": {
                    "final_tags": "outputs/final_tags.json",
                    "tagging_detail": "outputs/tagging_detail.json",
                    "job_result": "outputs/job_result.json",
                },
                "required_keys": ["selected_tags", "tagging_detail"],
                "validate_t_book_id": False,
            },
        ],
    }


def build_prompt_templates() -> dict[str, Any]:
    return {
        "version": "third_batch_v1",
        "source": "第三批打标尝试.md",
        "templates": [
            {
                "prompt_id": "story_overview_v1",
                "stage": "story_overview",
                "source": "第三批打标尝试.md#第一步",
                "messages": [
                    {
                        "role": "user",
                        "template": """你作为一个内容理解专家，擅长从短剧素材中总结主要剧情。

请结合短剧剧名、简介和字幕，对剧情进行整体梳理和概括。

要求：
1. 梳理所有关键角色的人物关系、背景、能力、年龄阶段或身份线索。
2. 按剧情发展顺序覆盖所有主要情节、冲突、反转和结局，不要猜测素材之外的信息。
3. 输出 JSON，字段固定为：t_book_id、analysis_status、characters、world_setting、plot_timeline、main_conflicts、uncertainties。
4. 如果素材存在缺口或冲突，写入 uncertainties。

短剧素材：
{{material_text}}
""",
                    }
                ],
            },
            {
                "prompt_id": "candidate_tagging_v1",
                "stage": "candidate_tagging",
                "source": "第三批打标尝试.md#第二步",
                "messages": [
                    {
                        "role": "user",
                        "template": """你是短剧标签专家。请根据短剧剧情概览、剧名、简介和标签体系，生成候选打标结果。

硬约束：
1. 标签只能从给定标签体系中选择，输出必须使用 category_id 和标签名，不要输出 label_id。
2. 受众选 1 个，时空选 1 个，题材选 1-3 个，情节选 3-8 个，角色设定选 2-4 个，情绪选 1 个普通情绪标签。
3. 入选标签权重或浓度必须大于 0 且小于等于 1，最多两位小数。
4. 不要直接抽取关键词当标签，要基于剧情主线综合判断。
5. label_id 是服务端写回字段，AI 不要生成、复制、缩写或猜测 label_id。
6. 输出 JSON，字段固定为：t_book_id、category_decisions、raw_candidates、uncertainties。

短剧素材：
{{material_text}}

剧情概览：
{{story_overview_result}}

标签体系：
{{tag_schema}}
""",
                        "blocks": [
                            {
                                "block_id": "emotion_sequence_prompt_v1",
                                "enabled": False,
                                "source": "第三批打标尝试.md#第二步-8",
                                "template": """可选规则：判断短剧的情绪变化，并从以下单个情绪标签中选取与短剧相关的多个标签，按照剧情发展顺序打出 1 个情绪变化标签。

单个情绪标签包括：虐、甜、治愈、爽、紧张、催泪、轻松、热血。

打出的标签需为组合情绪标签，格式示例：虐-紧张-爽，不同情绪变化之间用 “-” 符号相连。

情绪是指剧情的情感基调和受众的感受体会。各核心情绪标签定义见标签文件。
""",
                            }
                        ],
                    }
                ],
            },
            {
                "prompt_id": "finalize_v1",
                "stage": "finalize",
                "source": "第三批打标尝试.md#第三步",
                "messages": [
                    {
                        "role": "user",
                        "template": """你是短剧标签优化系统。请根据候选标签、标签体系、互斥规则和受众过滤规则，生成最终标签。

硬约束：
1. 最终输出只表达 AI 标签决策，不要输出 RS 写回结构。
2. selected_tags 的 key 必须是 category_id；每个标签对象只包含 标签名、权重、打标原因。
3. 情绪 category_id=000006，默认按普通单标签输出。
4. 所有标签权重必须大于 0 且小于等于 1。
5. 应用互斥规则、受众过滤规则、数量约束和权重修正规则；如删除标签，应在 tagging_detail 中说明。
6. label_id 和标签释义是服务端写回字段，AI 不要生成、复制、缩写或猜测。
7. 顶层输出 JSON 必须且只能包含 selected_tags、tagging_detail 两个字段。
8. 尽量覆盖所有必填分类；如果剧情证据不足，不要编造标签，可以缺失分类或少于 min_items，服务端会按 partial_success 记录 validation_issues。

输出结构必须严格匹配：
{
  "selected_tags": {
    "000001": [
      {
        "标签名": "从标签体系选择的标签名",
        "权重": 0.8,
        "打标原因": "选择该标签的剧情依据"
      }
    ]
  },
  "tagging_detail": {
    "rule_applications": [],
    "removed_tags": [],
    "notes": []
  }
}

标签体系：
{{tag_schema}}

受众过滤规则：
{{audience_filter_rules}}

互斥规则：
{{mutex_rules}}

剧情概览：
{{story_overview_result}}

候选标签：
{{candidate_tags}}

权重修正规则：
- 标签权重 = min(标签浓度 * 标签初始权重, 1.0)。
- 最终 JSON 中任何“权重”都不得大于 1；如果修正后超过 1，必须输出 1.0，不要输出 1.1、1.2 等超过 1 的值。
- 狼人、吸血鬼初始权重 1.1。
- 逆袭初始权重 0.4。
- 虐渣打脸、复仇初始权重 0.9。
- 萌宝初始权重 1.2。
- 虐恋纠葛、甜宠互动、破镜重圆初始权重 1.1。
- 受众为男频时言情初始权重 0.6，否则言情初始权重 1。
- 其余标签初始权重 1。
""",
                        "blocks": [
                            {
                                "block_id": "emotion_sequence_finalize_prompt_v1",
                                "enabled": False,
                                "source": "第三批打标尝试.md#第三步-展示格式",
                                "template": """可选规则：当启用情绪组合标签时，情绪 category_id=000006 输出 1 个情绪变化组合标签。

情绪变化标签不需要包含权重，标签名使用 “-” 连接多个单个情绪标签，例如：虐-紧张-爽。
""",
                            }
                        ],
                    }
                ],
            },
        ],
    }


def main() -> int:
    args = parse_args()
    material_dir = require_path(args.material_dir, "material dir")
    tag_xlsx = require_path(args.tag_xlsx, "tag xlsx")
    works_md = require_path(args.works_md, "works md")
    output_dir, config_dir = derive_poc_paths(args)

    works = select_works(parse_works_md(works_md), book_ids=args.book_ids, limit=args.limit)
    sheets = load_xlsx_rows(tag_xlsx)
    tag_schema = build_tag_schema_snapshot(sheets)
    tag_schema["source"] = str(tag_xlsx.relative_to(ROOT_DIR)) if tag_xlsx.is_relative_to(ROOT_DIR) else str(tag_xlsx)
    mutex_rules = build_mutual_exclusion_rules(sheets, tag_schema)
    materials = [
        build_work_material(work, material_dir, limit_episodes=args.limit_episodes)
        for work in works
    ]

    material_snapshot = build_material_snapshot(materials)
    write_json(output_dir / "cpp" / "material_snapshot.json", material_snapshot)
    write_json(output_dir / "rs" / "tag_schema_snapshot.json", tag_schema)
    write_json(output_dir / "rs" / "mutual_exclusion_rules.json", mutex_rules)
    write_json(config_dir / "ai_tagging_poc_config.json", build_service_config())
    write_json(config_dir / "workflow_definition.json", build_workflow_definition())
    write_json(config_dir / "prompt_templates.json", build_prompt_templates())
    write_book_inputs(
        output_dir,
        materials,
        tag_schema,
        mutex_rules,
    )

    summary = {
        "output_dir": str(output_dir.relative_to(ROOT_DIR)) if output_dir.is_relative_to(ROOT_DIR) else str(output_dir),
        "config_dir": str(config_dir.relative_to(ROOT_DIR)) if config_dir.is_relative_to(ROOT_DIR) else str(config_dir),
        "work_count": len(materials),
        "episode_count": sum(len([asset for asset in material["assets"] if not asset["metadata"]["is_preview"]]) for material in materials),
        "tag_category_count": len(tag_schema["categories"]),
        "tag_count": sum(len(category["labels"]) for category in tag_schema["categories"]),
        "mutual_exclusion_rule_count": len(mutex_rules),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"short_drama_build_structured_inputs failed: {exc}", file=sys.stderr)
        raise
