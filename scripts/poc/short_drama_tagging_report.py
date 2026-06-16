from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build readable short-drama tagging reports from POC run outputs.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--summary-json", type=Path, help="Path to a JSON summary printed by short_drama_tagging_poc.py.")
    source.add_argument("--summary-stdin", action="store_true", help="Read a JSON summary from stdin.")
    source.add_argument("--run-dir", type=Path, help="Run directory containing per_book/<book>/outputs/final_tags.json.")
    parser.add_argument("--output-dir", type=Path, help="Report output directory. Defaults to <run-dir>/reports.")
    parser.add_argument("--title", default="短剧打标报告")
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT_DIR / path


def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def require_array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def load_summary(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.summary_json:
        return require_object(load_json(resolve_path(args.summary_json)), "summary")
    if args.summary_stdin:
        return require_object(json.loads(sys.stdin.read()), "summary")
    return None


def discover_book_dirs(summary: dict[str, Any] | None, run_dir: Path | None) -> tuple[Path, list[Path]]:
    if summary is not None:
        results = require_array(summary.get("results"), "summary.results")
        book_dirs: list[Path] = []
        for index, item in enumerate(results):
            item_obj = require_object(item, f"summary.results[{index}]")
            output_dir = item_obj.get("output_dir")
            if not isinstance(output_dir, str) or not output_dir:
                raise ValueError(f"summary.results[{index}].output_dir must be a non-empty string")
            book_dirs.append(resolve_path(Path(output_dir)))
        output_dir_value = summary.get("output_dir")
        if not isinstance(output_dir_value, str) or not output_dir_value:
            raise ValueError("summary.output_dir must be a non-empty string when using summary input")
        return resolve_path(Path(output_dir_value)), book_dirs

    if run_dir is None:
        raise ValueError("run_dir is required without summary input")
    resolved_run_dir = resolve_path(run_dir)
    per_book_dir = resolved_run_dir / "per_book"
    if not per_book_dir.is_dir():
        raise FileNotFoundError(f"per_book dir not found: {per_book_dir}")
    book_dirs = sorted(path for path in per_book_dir.iterdir() if path.is_dir())
    if not book_dirs:
        raise FileNotFoundError(f"No per-book outputs found: {per_book_dir}")
    return resolved_run_dir, book_dirs


def build_schema_indexes(schema: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    categories = require_array(schema.get("categories"), "tag_schema.categories")
    category_by_id: dict[str, dict[str, Any]] = {}
    labels_by_category_id: dict[str, dict[str, dict[str, Any]]] = {}
    ordered_categories: list[dict[str, Any]] = []
    for index, category in enumerate(categories):
        category_obj = require_object(category, f"tag_schema.categories[{index}]")
        category_id = category_obj.get("category_id")
        category_name = category_obj.get("name")
        if not isinstance(category_id, str) or not isinstance(category_name, str):
            raise ValueError(f"tag_schema.categories[{index}] must contain category_id and name")
        category_by_id[category_id] = category_obj
        ordered_categories.append(category_obj)
        labels_by_id: dict[str, dict[str, Any]] = {}
        for label_index, label in enumerate(require_array(category_obj.get("labels"), f"tag_schema.categories[{index}].labels")):
            label_obj = require_object(label, f"tag_schema.categories[{index}].labels[{label_index}]")
            label_id = label_obj.get("label_id")
            if not isinstance(label_id, str):
                raise ValueError(f"tag_schema.categories[{index}].labels[{label_index}].label_id must be a string")
            labels_by_id[label_id] = label_obj
        labels_by_category_id[category_id] = labels_by_id
    return ordered_categories, category_by_id, labels_by_category_id


def issue_texts_by_category(issues: list[Any], category_by_id: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for issue in issues:
        issue_obj = require_object(issue, "tagging_detail.validation_issues[]")
        category_id = issue_obj.get("category_id")
        category_name = issue_obj.get("category_name")
        if isinstance(category_id, str) and category_id in category_by_id:
            key = category_id
        elif isinstance(category_name, str):
            key = category_name
        else:
            key = "未归类"
        message = issue_obj.get("message")
        if not isinstance(message, str) or not message.strip():
            message = str(issue_obj.get("issue", "存在校验问题"))
        grouped.setdefault(key, []).append(sanitize_text(message, category_by_id))
    return grouped


def sanitize_text(value: Any, category_by_id: dict[str, dict[str, Any]] | None = None) -> str:
    text = str(value)
    if category_by_id:
        for category_id, category in category_by_id.items():
            category_name = str(category["name"])
            text = re.sub(rf"category_id\s*[=:：]\s*{re.escape(category_id)}", f"{category_name}分类", text)
            text = text.replace(category_id, category_name)
    text = text.replace("label_id", "内部标签编号")
    text = text.replace("category_id", "分类")
    text = text.replace("t_book_id", "作品编号")
    return text


def build_book_report(book_dir: Path, sequence: int) -> dict[str, Any]:
    input_payload = require_object(load_json(book_dir / "input" / "input.json"), f"{book_dir}/input/input.json")
    final_tags = require_object(load_json(book_dir / "outputs" / "final_tags.json"), f"{book_dir}/outputs/final_tags.json")
    tagging_detail = require_object(load_json(book_dir / "outputs" / "tagging_detail.json"), f"{book_dir}/outputs/tagging_detail.json")
    job_params = require_object(input_payload.get("job_params"), "input.job_params")
    work_context = require_object(job_params.get("work_context"), "input.job_params.work_context")
    schema = require_object(
        require_object(input_payload.get("rs_default_tag_bundle"), "input.rs_default_tag_bundle").get("tag_schema_snapshot"),
        "input.rs_default_tag_bundle.tag_schema_snapshot",
    )
    ordered_categories, category_by_id, labels_by_category_id = build_schema_indexes(schema)
    tags_by_category = require_object(final_tags.get("tags"), "final_tags.tags")
    validation_issues = require_array(tagging_detail.get("validation_issues", []), "tagging_detail.validation_issues")
    issue_map = issue_texts_by_category(validation_issues, category_by_id)

    categories: list[dict[str, Any]] = []
    total_tags = 0
    for category in ordered_categories:
        category_id = category["category_id"]
        category_name = category["name"]
        raw_items = tags_by_category.get(category_id, [])
        if raw_items is None:
            raw_items = []
        items = require_array(raw_items, f"final_tags.tags.{category_id}")
        readable_items: list[dict[str, Any]] = []
        for item_index, item in enumerate(items):
            item_obj = require_object(item, f"final_tags.tags.{category_id}[{item_index}]")
            label_id = item_obj.get("label_id")
            if not isinstance(label_id, str) or label_id not in labels_by_category_id[category_id]:
                raise ValueError(f"final_tags contains unknown label for category {category_name}")
            label = labels_by_category_id[category_id][label_id]
            readable_items.append(
                {
                    "标签": label["name"],
                    "权重": item_obj.get("权重"),
                    "打标原因": sanitize_text(item_obj.get("打标原因", ""), category_by_id),
                    "标签释义": sanitize_text(label["definition"], category_by_id),
                }
            )
        total_tags += len(readable_items)
        categories.append(
            {
                "分类": category_name,
                "标签": readable_items,
                "问题说明": issue_map.get(category_id, issue_map.get(category_name, [])),
            }
        )

    status = tagging_detail.get("result_status", "unknown")
    if status == "success":
        status_text = "成功"
    elif status == "partial_success":
        status_text = "部分成功"
    else:
        status_text = str(status)

    return {
        "序号": sequence,
        "标题": sanitize_text(work_context.get("title", f"作品 {sequence}"), category_by_id),
        "简介": sanitize_text(work_context.get("synopsis", ""), category_by_id),
        "内容类型": sanitize_text(work_context.get("content_type", ""), category_by_id),
        "集数": work_context.get("episode_count", ""),
        "状态": status_text,
        "标签总数": total_tags,
        "分类": categories,
        "规则应用": [
            sanitize_text(item, category_by_id)
            for item in require_array(tagging_detail.get("rule_applications", []), "tagging_detail.rule_applications")
        ],
        "移除标签": normalize_removed_tags(require_array(tagging_detail.get("removed_tags", []), "tagging_detail.removed_tags"), category_by_id),
        "备注": [
            sanitize_text(item, category_by_id)
            for item in require_array(tagging_detail.get("notes", []), "tagging_detail.notes")
        ],
    }


def normalize_removed_tags(items: list[Any], category_by_id: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, str):
            normalized.append({"标签": "移除说明", "原因": sanitize_text(item, category_by_id)})
            continue
        item_obj = require_object(item, "tagging_detail.removed_tags[]")
        label_name = item_obj.get("标签名", item_obj.get("label_name", ""))
        reason = item_obj.get("原因", item_obj.get("删除原因", item_obj.get("打标原因", item_obj.get("reason", ""))))
        normalized.append({"标签": sanitize_text(label_name or "移除说明", category_by_id), "原因": sanitize_text(reason, category_by_id)})
    return normalized


def build_report(summary: dict[str, Any] | None, run_dir: Path, book_dirs: list[Path], title: str) -> dict[str, Any]:
    books = [build_book_report(book_dir, index + 1) for index, book_dir in enumerate(book_dirs)]
    status_counts: dict[str, int] = {}
    tag_count_by_category: dict[str, int] = {}
    for book in books:
        status_counts[book["状态"]] = status_counts.get(book["状态"], 0) + 1
        for category in book["分类"]:
            tag_count_by_category[category["分类"]] = tag_count_by_category.get(category["分类"], 0) + len(category["标签"])
    return {
        "报告标题": title,
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "运行信息": {
            "作品数量": len(books),
            "模型": summary.get("model") if summary else "",
            "并发数": summary.get("concurrency") if summary else "",
            "执行阶段": summary.get("stages") if summary else [],
        },
        "状态统计": status_counts,
        "分类标签数量": tag_count_by_category,
        "作品": books,
        "_run_dir": str(run_dir),
    }


def write_csv_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["序号", "作品标题", "状态", "分类", "标签", "权重", "打标原因", "标签释义", "问题说明"]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for book in report["作品"]:
            for category in book["分类"]:
                issue_text = "；".join(category["问题说明"])
                if category["标签"]:
                    for tag in category["标签"]:
                        writer.writerow(
                            {
                                "序号": book["序号"],
                                "作品标题": book["标题"],
                                "状态": book["状态"],
                                "分类": category["分类"],
                                "标签": tag["标签"],
                                "权重": tag["权重"],
                                "打标原因": tag["打标原因"],
                                "标签释义": tag["标签释义"],
                                "问题说明": issue_text,
                            }
                        )
                elif issue_text:
                    writer.writerow(
                        {
                            "序号": book["序号"],
                            "作品标题": book["标题"],
                            "状态": book["状态"],
                            "分类": category["分类"],
                            "标签": "",
                            "权重": "",
                            "打标原因": "",
                            "标签释义": "",
                            "问题说明": issue_text,
                        }
                    )


def public_report_json(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if not key.startswith("_")}


def write_html_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    status_counts = report["状态统计"]
    total_tags = sum(book["标签总数"] for book in report["作品"])
    partial_count = status_counts.get("部分成功", 0)
    success_count = status_counts.get("成功", 0)
    category_rows = "".join(
        f"<tr><td>{escape(category)}</td><td>{count}</td></tr>"
        for category, count in sorted(report["分类标签数量"].items(), key=lambda item: item[0])
    )
    book_cards = "\n".join(render_book_card(book) for book in report["作品"])
    model = report["运行信息"].get("模型") or "未记录"
    concurrency = report["运行信息"].get("并发数") or "未记录"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(report["报告标题"])}</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #176b87;
      --accent-soft: #e7f2f5;
      --ok: #16794c;
      --warn: #a15c00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.5;
    }}
    header {{
      background: #12313f;
      color: white;
      padding: 28px 32px;
    }}
    main {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 24px 24px 48px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 14px; font-size: 20px; }}
    h3 {{ margin: 0; font-size: 17px; }}
    .subtle {{ color: var(--muted); }}
    header .subtle {{ color: #d6e4ea; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0 24px;
    }}
    .stat, .section, .book {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .stat {{ padding: 16px; }}
    .stat strong {{ display: block; font-size: 24px; }}
    .section {{ padding: 18px; margin-bottom: 18px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ background: #f0f3f6; font-weight: 700; }}
    .book {{ margin-bottom: 18px; overflow: hidden; }}
    .book-head {{ padding: 18px; border-bottom: 1px solid var(--line); display: flex; gap: 16px; justify-content: space-between; }}
    .book-title {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    .badge {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 10px; font-size: 13px; font-weight: 700; }}
    .badge.ok {{ background: #e8f5ee; color: var(--ok); }}
    .badge.warn {{ background: #fff3df; color: var(--warn); }}
    .book-body {{ padding: 18px; }}
    .synopsis {{ margin: 10px 0 16px; color: #364152; }}
    .category {{ margin-top: 16px; }}
    .tags {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; margin-top: 10px; }}
    .tag {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfcfd; }}
    .tag-top {{ display: flex; justify-content: space-between; gap: 10px; margin-bottom: 8px; }}
    .tag-name {{ font-weight: 700; color: var(--accent); }}
    .weight {{ color: var(--muted); white-space: nowrap; }}
    .reason {{ margin: 8px 0; }}
    .definition {{ color: var(--muted); font-size: 13px; }}
    .issues {{ background: #fff7ed; border: 1px solid #fed7aa; padding: 10px 12px; border-radius: 8px; margin-top: 10px; }}
    .removed {{ margin-top: 14px; }}
    .removed li, .notes li, .rules li {{ margin-bottom: 6px; }}
    @media (max-width: 760px) {{
      header {{ padding: 22px 18px; }}
      main {{ padding: 18px 14px 36px; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .book-head {{ display: block; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(report["报告标题"])}</h1>
    <div class="subtle">生成时间：{escape(report["生成时间"])}　模型：{escape(str(model))}　并发数：{escape(str(concurrency))}</div>
  </header>
  <main>
    <section class="stats">
      <div class="stat"><span class="subtle">作品数量</span><strong>{len(report["作品"])}</strong></div>
      <div class="stat"><span class="subtle">成功</span><strong>{success_count}</strong></div>
      <div class="stat"><span class="subtle">部分成功</span><strong>{partial_count}</strong></div>
      <div class="stat"><span class="subtle">标签总数</span><strong>{total_tags}</strong></div>
    </section>
    <section class="section">
      <h2>分类覆盖</h2>
      <table>
        <thead><tr><th>分类</th><th>标签数量</th></tr></thead>
        <tbody>{category_rows}</tbody>
      </table>
    </section>
    {book_cards}
  </main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def render_book_card(book: dict[str, Any]) -> str:
    badge_class = "ok" if book["状态"] == "成功" else "warn"
    categories = "\n".join(render_category(category) for category in book["分类"])
    removed = render_removed_tags(book["移除标签"])
    notes = render_list("备注", book["备注"], "notes")
    rules = render_list("规则应用", book["规则应用"], "rules")
    return f"""
    <article class="book">
      <div class="book-head">
        <div>
          <div class="book-title">
            <h3>{book["序号"]}. {escape(str(book["标题"]))}</h3>
            <span class="badge {badge_class}">{escape(str(book["状态"]))}</span>
          </div>
          <div class="subtle">内容类型：{escape(str(book["内容类型"]))}　集数：{escape(str(book["集数"]))}　标签数：{book["标签总数"]}</div>
        </div>
      </div>
      <div class="book-body">
        <p class="synopsis">{escape(str(book["简介"]))}</p>
        {categories}
        {removed}
        {rules}
        {notes}
      </div>
    </article>
    """


def render_category(category: dict[str, Any]) -> str:
    tags = category["标签"]
    issue_html = ""
    if category["问题说明"]:
        issue_html = '<div class="issues">' + "<br>".join(escape(str(item)) for item in category["问题说明"]) + "</div>"
    if tags:
        tag_cards = "".join(render_tag(tag) for tag in tags)
    else:
        tag_cards = '<div class="tag"><div class="subtle">未返回标签</div></div>'
    return f"""
    <section class="category">
      <h3>{escape(str(category["分类"]))}</h3>
      {issue_html}
      <div class="tags">{tag_cards}</div>
    </section>
    """


def render_tag(tag: dict[str, Any]) -> str:
    return f"""
    <div class="tag">
      <div class="tag-top"><span class="tag-name">{escape(str(tag["标签"]))}</span><span class="weight">权重 {escape(str(tag["权重"]))}</span></div>
      <div class="reason">{escape(str(tag["打标原因"]))}</div>
      <div class="definition">{escape(str(tag["标签释义"]))}</div>
    </div>
    """


def render_removed_tags(items: list[dict[str, str]]) -> str:
    if not items:
        return ""
    rows = "".join(f"<li><strong>{escape(item['标签'])}</strong>：{escape(item['原因'])}</li>" for item in items)
    return f'<section class="removed"><h3>移除标签</h3><ul>{rows}</ul></section>'


def render_list(title: str, items: list[Any], css_class: str) -> str:
    if not items:
        return ""
    rows = "".join(f"<li>{escape(str(item))}</li>" for item in items)
    return f'<section class="{css_class}"><h3>{escape(title)}</h3><ul>{rows}</ul></section>'


def generate_report(
    *,
    summary: dict[str, Any] | None,
    run_dir: Path,
    book_dirs: list[Path],
    output_dir: Path,
    title: str,
) -> dict[str, str]:
    report = build_report(summary, run_dir, book_dirs, title)
    html_path = output_dir / "report.html"
    csv_path = output_dir / "tags.csv"
    json_path = output_dir / "tag_results.json"
    write_html_report(html_path, report)
    write_csv_report(csv_path, report)
    write_json(json_path, public_report_json(report))
    return {
        "html": str(html_path),
        "csv": str(csv_path),
        "json": str(json_path),
    }


def main() -> int:
    args = parse_args()
    summary = load_summary(args)
    run_dir, book_dirs = discover_book_dirs(summary, resolve_path(args.run_dir) if args.run_dir else None)
    output_dir = resolve_path(args.output_dir) if args.output_dir else run_dir / "reports"
    artifacts = generate_report(summary=summary, run_dir=run_dir, book_dirs=book_dirs, output_dir=output_dir, title=args.title)
    result = {
        "run_dir": str(run_dir.relative_to(ROOT_DIR)) if run_dir.is_relative_to(ROOT_DIR) else str(run_dir),
        "output_dir": str(output_dir.relative_to(ROOT_DIR)) if output_dir.is_relative_to(ROOT_DIR) else str(output_dir),
        "book_count": len(book_dirs),
        "artifacts": artifacts,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"short_drama_tagging_report failed: {exc}", file=sys.stderr)
        raise
