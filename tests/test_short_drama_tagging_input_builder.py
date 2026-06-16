from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.poc import short_drama_build_structured_inputs as builder


def minimal_sheets() -> dict[str, list[list[str]]]:
    return {
        "受众": [["标签", "定义"], ["男频", "男性受众"]],
        "时空": [["标签", "定义"], ["现代都市", "现代城市"]],
        "题材": [["标签", "定义"], ["言情", "爱情关系"]],
        "情节": [["标签", "定义"], ["系统奇遇", "系统能力"], ["奇幻脑洞", "奇幻设定"], ["逆袭", "逆转处境"]],
        "角色设定": [["标签", "定义"], ["大男主", "男性主角"], ["精英阶层", "精英角色"]],
        "情绪": [["标签", "定义"], ["虐", "痛苦压抑"], ["爽", "畅快解气"]],
        "受众过滤规则": [["受众"], ["男频", "删除", "系统奇遇"]],
        "互斥规则": [["标签", "互斥标签"], ["系统", "脑洞"]],
    }


def test_subtitle_assets_fail_fast_for_bad_episode_inputs(tmp_path: Path) -> None:
    work_dir = tmp_path / "book"
    work_dir.mkdir()
    (work_dir / "book_0001.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")

    with pytest.raises(ValueError, match="limit-episodes"):
        builder.read_subtitle_assets(work_dir, limit_episodes=0)

    (work_dir / "bad.srt").write_text("bad", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected SRT filename"):
        builder.read_subtitle_assets(work_dir, limit_episodes=None)


def test_build_work_material_detects_missing_episode(tmp_path: Path) -> None:
    material_dir = tmp_path / "materials"
    work_dir = material_dir / "123"
    work_dir.mkdir(parents=True)
    (work_dir / "123_0001.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nA\n", encoding="utf-8")
    (work_dir / "123_0003.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nC\n", encoding="utf-8")
    work = builder.WorkMeta("123", "Title", "短剧", "英语", "中文", "Synopsis", "否")

    with pytest.raises(ValueError, match="Missing regular episodes"):
        builder.build_work_material(work, material_dir, limit_episodes=None)


def test_tag_schema_builds_id_based_mutex_rules_with_explicit_aliases() -> None:
    schema = builder.build_tag_schema_snapshot(minimal_sheets())
    rules = builder.build_mutual_exclusion_rules(minimal_sheets(), schema)

    assert rules == [
        {
            "label_id": builder.stable_label_id("000004", "系统奇遇"),
            "label_name": "系统奇遇",
            "source_label_name": "系统",
            "mutex_label_ids": [builder.stable_label_id("000004", "奇幻脑洞")],
            "mutex_label_names": ["奇幻脑洞"],
            "source_mutex_label_names": ["脑洞"],
        }
    ]


def test_tag_schema_unknown_mutex_reference_fails() -> None:
    sheets = minimal_sheets()
    sheets["互斥规则"] = [["标签", "互斥标签"], ["不存在", "脑洞"]]
    schema = builder.build_tag_schema_snapshot(sheets)

    with pytest.raises(ValueError, match="unknown label"):
        builder.build_mutual_exclusion_rules(sheets, schema)


def test_main_builds_inputs_and_config_under_poc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    poc_root = tmp_path / "short_drama_tagging"
    work = builder.WorkMeta("123", "Title", "短剧", "英语", "中文", "Synopsis", "否")
    material = {
        "t_book_id": "123",
        "work_context": {
            "title": "Title",
            "synopsis": "Synopsis",
            "subtitle_language": "en",
            "audio_language": "zh",
            "series_structure": "continuous_series",
            "content_type": "短剧",
            "episode_count": 1,
            "is_ai_material": "否",
        },
        "assets": [
            {
                "asset_type": "subtitle_srt",
                "episode_no": 1,
                "format": "srt",
                "uri": "memory://episode-1.srt",
                "text": "1\n00:00:01,000 --> 00:00:02,000\nHi\n",
                "content_hash": "sha256:test",
                "metadata": {"filename": "episode-1.srt", "is_preview": False},
            }
        ],
    }

    monkeypatch.setattr(
        builder,
        "parse_args",
        lambda: SimpleNamespace(
            poc_root=poc_root,
            material_dir=Path("materials"),
            tag_xlsx=Path("tags.xlsx"),
            works_md=Path("works.md"),
            output_dir=None,
            config_dir=None,
            book_ids=None,
            limit=None,
            limit_episodes=None,
        ),
    )
    monkeypatch.setattr(builder, "require_path", lambda path, label: path)
    monkeypatch.setattr(builder, "parse_works_md", lambda path: [work])
    monkeypatch.setattr(builder, "load_xlsx_rows", lambda path: minimal_sheets())
    monkeypatch.setattr(builder, "build_work_material", lambda *args, **kwargs: material)

    assert builder.main() == 0
    assert (poc_root / "inputs" / "cpp" / "material_snapshot.json").exists()
    assert (poc_root / "inputs" / "rs" / "tag_schema_snapshot.json").exists()
    assert (poc_root / "inputs" / "jobs" / "per_book" / "123" / "input.json").exists()
    assert (poc_root / "config" / "ai_tagging_poc_config.json").exists()
    assert (poc_root / "config" / "workflow_definition.json").exists()
    assert (poc_root / "config" / "prompt_templates.json").exists()
