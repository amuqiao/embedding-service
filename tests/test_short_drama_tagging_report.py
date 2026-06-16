from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.poc import short_drama_tagging_report as report


def schema() -> dict[str, object]:
    return {
        "version": "test",
        "categories": [
            {
                "category_id": "000001",
                "name": "受众",
                "required": True,
                "min_items": 1,
                "max_items": 1,
                "labels": [
                    {
                        "label_id": "internal-audience-label",
                        "label_key": "internal-audience-label",
                        "name": "女频",
                        "definition": "女性受众",
                    }
                ],
            },
            {
                "category_id": "000002",
                "name": "题材",
                "required": True,
                "min_items": 1,
                "max_items": 2,
                "labels": [
                    {
                        "label_id": "internal-genre-label",
                        "label_key": "internal-genre-label",
                        "name": "言情",
                        "definition": "爱情关系",
                    }
                ],
            },
        ],
    }


def write_book_output(run_dir: Path, *, title: str = "Readable Title") -> Path:
    book_dir = run_dir / "per_book" / "999999"
    input_payload = {
        "job_params": {
            "t_book_id": "999999",
            "work_context": {
                "title": title,
                "synopsis": "Readable synopsis",
                "content_type": "短剧",
                "episode_count": 12,
            },
        },
        "rs_default_tag_bundle": {
            "tag_schema_snapshot": schema(),
            "mutual_exclusion_rules": [],
        },
    }
    final_tags = {
        "t_book_id": "999999",
        "tags": {
            "000001": [
                {
                    "label_id": "internal-audience-label",
                    "标签名": "女频",
                    "权重": 0.9,
                    "打标原因": "Readable reason",
                    "标签释义": "女性受众",
                }
            ],
            "000002": [],
        },
    }
    tagging_detail = {
        "rule_applications": ["Readable rule category_id=000002"],
        "removed_tags": [{"标签名": "移除项", "原因": "Readable removal reason"}],
        "notes": ["Readable note without label_id"],
        "result_status": "partial_success",
        "validation_issues": [
            {
                "category_id": "000002",
                "category_name": "题材",
                "issue": "missing_required_category",
                "message": "题材 未返回标签",
            }
        ],
    }
    write_json(book_dir / "input" / "input.json", input_payload)
    write_json(book_dir / "outputs" / "final_tags.json", final_tags)
    write_json(book_dir / "outputs" / "tagging_detail.json", tagging_detail)
    return book_dir


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_generate_report_outputs_html_csv_and_json_without_internal_ids(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    book_dir = write_book_output(run_dir)
    output_dir = tmp_path / "reports"

    artifacts = report.generate_report(
        summary={"model": "fake", "concurrency": 1, "stages": ["finalize"]},
        run_dir=run_dir,
        book_dirs=[book_dir],
        output_dir=output_dir,
        title="Readable Report",
    )

    html = Path(artifacts["html"]).read_text(encoding="utf-8")
    csv_text = Path(artifacts["csv"]).read_text(encoding="utf-8-sig")
    json_text = Path(artifacts["json"]).read_text(encoding="utf-8")
    for content in [html, csv_text, json_text]:
        assert "Readable Title" in content
        assert "受众" in content
        assert "女频" in content
        assert "Readable reason" in content
        assert "internal-audience-label" not in content
        assert "000001" not in content
        assert "000002" not in content
        assert "label_id" not in content
        assert "999999" not in content
    for content in [html, json_text]:
        assert "题材分类" in content
        assert "内部标签编号" in content
    assert "题材 未返回标签" in html
    assert "部分成功" in csv_text
    assert json.loads(json_text)["作品"][0]["分类"][0]["标签"][0]["标签"] == "女频"


def test_summary_input_discovers_book_dirs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    book_dir = write_book_output(run_dir)
    summary = {
        "output_dir": str(run_dir),
        "results": [{"t_book_id": "999999", "output_dir": str(book_dir)}],
    }

    discovered_run_dir, book_dirs = report.discover_book_dirs(summary, None)

    assert discovered_run_dir == run_dir
    assert book_dirs == [book_dir]


def test_main_writes_report_from_summary_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run"
    book_dir = write_book_output(run_dir)
    summary_path = tmp_path / "summary.json"
    output_dir = tmp_path / "reports"
    write_json(
        summary_path,
        {
            "output_dir": str(run_dir),
            "model": "fake",
            "concurrency": 2,
            "stages": ["story_overview", "candidate_tagging", "finalize"],
            "results": [{"t_book_id": "999999", "output_dir": str(book_dir)}],
        },
    )
    monkeypatch.setattr(
        report,
        "parse_args",
        lambda: SimpleNamespace(
            summary_json=summary_path,
            summary_stdin=False,
            run_dir=None,
            output_dir=output_dir,
            title="Readable Report",
        ),
    )

    assert report.main() == 0
    assert (output_dir / "report.html").exists()
    assert (output_dir / "tags.csv").exists()
    assert (output_dir / "tag_results.json").exists()


def test_report_rejects_unknown_label_id(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    book_dir = write_book_output(run_dir)
    final_tags_path = book_dir / "outputs" / "final_tags.json"
    final_tags = json.loads(final_tags_path.read_text(encoding="utf-8"))
    final_tags["tags"]["000001"][0]["label_id"] = "missing-label"
    write_json(final_tags_path, final_tags)

    with pytest.raises(ValueError, match="unknown label"):
        report.build_book_report(book_dir, 1)


def test_report_accepts_string_removed_tags(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    book_dir = write_book_output(run_dir)
    detail_path = book_dir / "outputs" / "tagging_detail.json"
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    detail["removed_tags"] = ["删除标签“职场商战”：与主线不匹配。"]
    write_json(detail_path, detail)

    book = report.build_book_report(book_dir, 1)

    assert book["移除标签"] == [{"标签": "移除说明", "原因": "删除标签“职场商战”：与主线不匹配。"}]
